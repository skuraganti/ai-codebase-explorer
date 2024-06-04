"""Supervisor-owned offline acceptance. Synthetic code is parsed, never executed.

IPC responses are explicit mocks and do not establish live-model performance.
"""

import copy
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def fixture(tmp_path):
    sources = {
        "pkg/__init__.py": "from . import a, b\n",
        "pkg/a.py": (
            "from . import b\n\nclass lower:\n    def method(self, value: in"
            't) -> str:\n        """Convert the supplied value to text."""'
            "\n        return str(value)\n"
        ),
        "pkg/b.py": "from . import c\n\ndef helper(value: int) -> int:\n    return value + 1\n",
        "pkg/c.py": (
            'def base():\n    """A synthetic parser fixture, never executed."""\n    return 42\n'
        ),
        "ts/types.ts": "export type T = string;\n",
        "ts/dep.ts": "export const run = () => 1;\n",
        "ts/index.ts": (
            'import { type T } from "./types.js";\nimport { run, type T as'
            ' U } from "./dep.js";\nexport { type T } from "./types.js";\ne'
            "xport const factory = () => { const localTemp = 1; return lo"
            'calTemp; };\nconst deferred = import("./later.js");\n'
        ),
    }
    source = tmp_path / "corpus"
    source.mkdir()
    records = []
    for path, text in sources.items():
        raw = text.encode()
        p = source / "sample" / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        records.append(
            dict(
                repo_id="sample",
                path=path,
                bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                language="python" if path.endswith(".py") else "typescript",
                url="https://github.com/example/sample/blob/" + "f" * 40 + "/" + path,
            )
        )
    manifest = [
        dict(
            id="sample",
            repo="example/sample",
            tag="fixture",
            commit="f" * 40,
            license="MIT",
            prefixes=["pkg/", "ts/"],
            suffixes=[".py", ".ts"],
            files=records,
        )
    ]
    (source / "manifest.json").write_text(json.dumps(manifest))
    app = create_app(tmp_path / "index.sqlite", source, tmp_path / "bridge")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, app, source, tmp_path / "bridge", manifest


def target(client, path="pkg/a.py", qual="lower.method"):
    f = next(f for f in client.get("/api/files").json() if f["path"] == path)
    symbols = client.get("/api/files/" + f["id"]).json()["symbols"]
    return f, next(s for s in symbols if s["qualname"] == qual)


def queue(client):
    f, s = target(client)
    r = client.post("/api/jobs", json={"kind": "explain", "symbol_id": s["id"]})
    assert r.status_code == 200, r.text
    return r.json()


def mocked_response(app, job):
    row = app.state.index.row("jobs", job["id"])
    with app.state.index.connect() as db:
        contexts = json.loads(
            db.execute("SELECT contexts FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        )
    c = contexts[0]
    quote = c["text"][: min(100, len(c["text"]))]
    return dict(
        id=job["id"],
        revision=row["revision"],
        state="succeeded",
        model="cookllm",
        latency_ms=12.3,
        usage={"input_tokens": 20},
        output=dict(
            abstain=False,
            reason="",
            claims=[
                dict(
                    text="Synthetic offline claim.",
                    supports=[dict(context_id=c["context_id"], quote=quote)],
                )
            ],
            uncertainty=["Static excerpt only."],
        ),
    )


def publish(ipc, value):
    # Test payload writing is not used by production app; explicit mock response.
    (ipc / (value["id"] + ".response.json")).write_text(json.dumps(value))


def test_real_factory_source_graph_and_nested_semantics(fixture):
    client, app, source, ipc, manifest = fixture
    assert client.get("/health").status_code == 200
    state = client.get("/api/state").json()
    assert state["counts"]["files"] == 7
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    file, s = target(client)
    assert s["kind"] == "method" and "value: int" in s["signature"] and "-> str" in s["signature"]
    ts = next(f for f in client.get("/api/files").json() if f["path"] == "ts/index.ts")
    d = client.get("/api/files/" + ts["id"]).json()
    assert "localTemp" not in [s["name"] for s in d["symbols"]]
    by_line = {i["line"]: i for i in d["imports"]}
    assert by_line[1]["type_only"] is True and by_line[2]["type_only"] is False
    assert by_line[3]["type_only"] is True and by_line[5]["status"] == "dynamic"
    root = next(f for f in client.get("/api/files").json() if f["path"] == "pkg/__init__.py")
    imports = client.get("/api/files/" + root["id"]).json()["imports"]
    assert len({i["target_file_id"] for i in imports if i["status"] == "resolved"}) == 2
    c = next(f for f in client.get("/api/files").json() if f["path"] == "pkg/c.py")
    impact = client.get("/api/impact/" + c["id"] + "?depth=3").json()
    for consumer in impact["impacted"]:
        cursor = consumer["file"]["id"]
        for step in consumer["path"]:
            assert step["from"] == cursor
            cursor = step["to"]
        assert cursor == c["id"] and len(consumer["path"]) == consumer["distance"]


def test_idempotent_and_bad_hash_preserve_index(fixture):
    client, app, source, ipc, manifest = fixture
    before = client.get("/api/state").json()["revision"]
    assert client.post("/api/index", json={}).json()["changed"] is False
    (source / "sample/pkg/c.py").write_text("changed without manifest update")
    assert client.post("/api/index", json={}).status_code == 422
    state = client.get("/api/state").json()
    assert state["revision"] == before and state["counts"]["files"] == 7 and state["index_error"]


def test_mock_float_latency_quotes_and_default_question(fixture):
    client, app, source, ipc, manifest = fixture
    job = queue(client)
    request = json.loads((ipc / (job["id"] + ".request.json")).read_text())
    assert request["question"].strip()
    assert (ipc / (job["id"] + ".request.json")).stat().st_mode & 0o777 == 0o600
    value = mocked_response(app, job)
    publish(ipc, value)
    completed = client.get("/api/jobs/" + job["id"]).json()
    assert completed["state"] == "succeeded", completed
    assert completed["latency_ms"] == 12.3 and completed["citations"]
    assert not (ipc / (job["id"] + ".request.json")).exists()
    assert client.get("/api/state").json()["model_budget"]["attempted"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "bool_revision",
        "dict_reason",
        "empty_claims",
        "huge_usage",
        "list_context_id",
        "wrong_quote",
    ],
)
def test_malformed_mock_response_fails_visible(fixture, mutation):
    client, app, source, ipc, manifest = fixture
    job = queue(client)
    value = mocked_response(app, job)
    if mutation == "bool_revision":
        value["revision"] = True
    if mutation == "dict_reason":
        value["output"]["reason"] = {}
    if mutation == "empty_claims":
        value["output"]["claims"] = []
    if mutation == "huge_usage":
        value["usage"]["input_tokens"] = 10**400
    if mutation == "list_context_id":
        value["output"]["claims"][0]["supports"][0]["context_id"] = []
    if mutation == "wrong_quote":
        value["output"]["claims"][0]["supports"][0]["quote"] = (
            "fabricated phrase not found anywhere"
        )
    publish(ipc, value)
    r = client.get("/api/jobs/" + job["id"])
    assert r.status_code == 200
    assert r.json()["state"] == "failed", r.json()


def test_state_consumes_results_and_orphan_removes_requests(fixture):
    client, app, source, ipc, manifest = fixture
    job = queue(client)
    publish(ipc, mocked_response(app, job))
    state = client.get("/api/state").json()
    assert next(j for j in state["jobs"] if j["id"] == job["id"])["state"] == "succeeded"
    orphan = queue(client)
    (ipc / (orphan["id"] + ".request.json")).unlink()
    app.state.queue.reconcile()
    assert client.get("/api/jobs/" + orphan["id"]).json()["state"] == "failed"
    assert not (ipc / (orphan["id"] + ".request.json")).exists()
    assert client.get("/api/state").json()["model_budget"]["attempted"] == 2


def test_queue_cap_and_bad_uuid_do_not_touch_unrelated_file(fixture):
    client, app, source, ipc, manifest = fixture
    for _ in range(3):
        queue(client)
    f, s = target(client)
    assert (
        client.post("/api/jobs", json={"kind": "explain", "symbol_id": s["id"]}).status_code == 429
    )
    untouched = ipc.parent / "preserve.request.json"
    untouched.write_text("preserve")
    assert client.get("/api/jobs/not-a-uuid").status_code in {404, 422}
    assert untouched.read_text() == "preserve"


def test_symlink_repository_and_traversal_refused_without_read(fixture, tmp_path):
    client, app, source, ipc, manifest = fixture
    current = source / "sample"
    outside = tmp_path / "outside"
    current.rename(outside)
    current.symlink_to(outside, target_is_directory=True)
    assert client.post("/api/index", json={}).status_code == 422
    current.unlink()
    outside.rename(current)
    altered = copy.deepcopy(manifest)
    altered[0]["id"] = "../outside"
    (source / "manifest.json").write_text(json.dumps(altered))
    assert client.post("/api/index", json={}).status_code == 422
