"""Lead-completed core regressions after bounded worker delivery; model IPC is mocked."""

import json
import os
import sqlite3
import time

import pytest
from test_acceptance import fixture as fixture
from test_acceptance import mocked_response, publish, queue

from app.main import bridge_status
from app.parser import parse_python, parse_typescript, python_module


@pytest.mark.parametrize(
    "path,module",
    [
        ("src/flask/app.py", "flask.app"),
        ("src/flask/__init__.py", "flask"),
        ("src/flask/sansio/app.py", "flask.sansio.app"),
    ],
)
def test_python_package_mapping(path, module):
    assert python_module(path, "src/flask/") == module


def test_python_nested_async_and_type_checking_alias():
    source = (
        "import typing as t\nfrom typing import TYPE_CHECKING as TC\nif"
        " t.TYPE_CHECKING:\n    import alpha\nif TC:\n    from beta impo"
        "rt Thing\nclass lower:\n    async def run(self, item: int) -> "
        "str:\n        def nested():\n            return item\n        r"
        "eturn str(nested())\n"
    )
    result = parse_python(source, "file", "repo")
    assert not result.diagnostics
    symbols = {s["qualname"]: s for s in result.symbols}
    assert symbols["lower.run"]["kind"] == "async_method"
    assert symbols["lower.run.nested"]["kind"] == "function"
    assert symbols["lower.run.nested"]["start_line"] == 9
    assert next(i for i in result.imports if i["specifier"] == "alpha")["type_only"]
    assert next(i for i in result.imports if i["specifier"] == "beta")["type_only"]


@pytest.mark.parametrize(
    "source,diagnostics",
    [("export const fragment = <div>ok</div>;", False), ("export function broken( {", True)],
)
def test_typescript_tsx_and_diagnostics(source, diagnostics):
    result = parse_typescript(source, "file", "repo", tsx=True)
    assert bool(result.diagnostics) == diagnostics
    if diagnostics:
        assert not result.symbols


def test_type_only_default_import_is_runtime_and_unicode_range():
    source = (
        '// café 🌐\nimport runtime, { type T } from "./dep.js";\nexport'
        " interface Result { run(): string; }\nexport const parse = ()"
        " => 1;\n"
    )
    result = parse_typescript(source, "file", "repo")
    assert not result.diagnostics
    assert result.imports[0]["type_only"] is False
    assert next(s for s in result.symbols if s["qualname"] == "Result.run")["start_line"] == 3
    assert next(s for s in result.symbols if s["name"] == "parse")["start_line"] == 4


def test_changed_revision_scrubs_all_old_evidence_and_cleans_ipc(fixture):
    client, app, source, ipc, manifest = fixture
    succeeded = queue(client)
    publish(ipc, mocked_response(app, succeeded))
    assert client.get("/api/jobs/" + succeeded["id"]).json()["state"] == "succeeded"
    failed = queue(client)
    bad = mocked_response(app, failed)
    bad["output"]["reason"] = {}
    publish(ipc, bad)
    assert client.get("/api/jobs/" + failed["id"]).json()["state"] == "failed"
    pending = queue(client)
    raw = (source / "sample/pkg/c.py").read_bytes() + b"\n# explicit synthetic update\n"
    (source / "sample/pkg/c.py").write_bytes(raw)
    import hashlib

    entry = next(f for f in manifest[0]["files"] if f["path"] == "pkg/c.py")
    entry.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (source / "manifest.json").write_text(json.dumps(manifest))
    r = client.post("/api/index", json={})
    assert r.json()["changed"] is True
    for identifier in (succeeded["id"], failed["id"], pending["id"]):
        value = client.get("/api/jobs/" + identifier).json()
        assert (
            value["state"] == "cancelled"
            and value["question"] == ""
            and value["claims"] == []
            and value["citations"] == []
        )
        with app.state.index.connect() as db:
            assert (
                db.execute("SELECT contexts FROM jobs WHERE id=?", (identifier,)).fetchone()[0]
                == "[]"
            )
        assert not (ipc / (identifier + ".request.json")).exists()
    publish(ipc, mocked_response_for_stale(succeeded))
    assert client.get("/api/jobs/" + succeeded["id"]).json()["state"] == "cancelled"
    assert not (ipc / (succeeded["id"] + ".response.json")).exists()
    assert client.get("/api/state").json()["model_budget"]["attempted"] == 3


def mocked_response_for_stale(job):
    return {
        "id": job["id"],
        "revision": job["revision"],
        "state": "succeeded",
        "model": "cookllm",
        "latency_ms": 1,
        "output": {"abstain": True, "reason": "Stale fixture", "claims": [], "uncertainty": []},
    }


def test_expiry_and_ten_lifetime_attempts_preserved(fixture):
    client, app, source, ipc, manifest = fixture
    for _ in range(10):
        job = queue(client)
        with app.state.index.connect() as db:
            db.execute(
                "UPDATE jobs SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (job["id"],)
            )
            db.commit()
        state = client.get("/api/state").json()
        assert state["model_budget"]["pending"] == 0
        assert client.get("/api/jobs/" + job["id"]).json()["state"] == "failed"
    from test_acceptance import target

    f, s = target(client)
    assert (
        client.post("/api/jobs", json={"kind": "explain", "symbol_id": s["id"]}).status_code == 429
    )
    app.state.queue.reconcile()
    assert client.get("/api/state").json()["model_budget"]["attempted"] == 10


def test_quote_end_line_does_not_include_phantom_line(fixture):
    client, app, source, ipc, manifest = fixture
    job = queue(client)
    value = mocked_response(app, job)
    with app.state.index.connect() as db:
        context = json.loads(
            db.execute("SELECT contexts FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        )[0]
    quote = context["text"].splitlines(keepends=True)[0]
    assert len(quote) >= 20
    value["output"]["claims"][0]["supports"][0]["quote"] = quote
    publish(ipc, value)
    result = client.get("/api/jobs/" + job["id"]).json()
    assert result["state"] == "succeeded"
    assert (
        result["citations"][0]["start_line"]
        == result["citations"][0]["end_line"]
        == context["start_line"]
    )


def test_transient_result_commit_failure_retains_artifacts_for_same_id(fixture, monkeypatch):
    client, app, source, ipc, manifest = fixture
    job = queue(client)
    publish(ipc, mocked_response(app, job))
    original = app.state.queue._succeed

    def fail(*args):
        raise sqlite3.OperationalError("explicit synthetic commit failure")

    monkeypatch.setattr(app.state.queue, "_succeed", fail)
    with pytest.raises(sqlite3.OperationalError):
        app.state.queue.get(job["id"])
    assert (ipc / (job["id"] + ".request.json")).exists() and (
        ipc / (job["id"] + ".response.json")
    ).exists()
    monkeypatch.setattr(app.state.queue, "_succeed", original)
    assert client.get("/api/jobs/" + job["id"]).json()["state"] == "succeeded"
    assert client.get("/api/state").json()["model_budget"]["attempted"] == 1


def test_current_index_hash_mismatch_cannot_commit_old_response(fixture):
    client, app, source, ipc, manifest = fixture
    job = queue(client)
    publish(ipc, mocked_response(app, job))
    with app.state.index.connect() as db:
        db.execute("UPDATE files SET sha256=?", ("f" * 64,))
        db.commit()
    assert client.get("/api/jobs/" + job["id"]).json()["state"] == "failed"


def test_fifo_response_is_rejected_without_blocking(fixture):
    client, app, source, ipc, manifest = fixture
    job = queue(client)
    os.mkfifo(ipc / (job["id"] + ".response.json"))
    assert client.get("/api/jobs/" + job["id"]).json()["state"] == "failed"


@pytest.mark.parametrize(
    "heartbeat",
    [
        [],
        {"at": True},
        {"at": 10**400, "model": "cookllm", "status": "ready"},
        {"at": 1e100, "model": "cookllm", "status": "ready"},
    ],
)
def test_bad_heartbeat_is_offline_not_server_error(tmp_path, heartbeat):
    (tmp_path / "heartbeat.json").write_text(json.dumps(heartbeat))
    assert bridge_status(tmp_path)["online"] is False


def test_current_and_future_heartbeat_and_fifo(tmp_path):
    path = tmp_path / "heartbeat.json"
    path.write_text(json.dumps({"at": time.time(), "model": "cookllm", "status": "ready"}))
    assert bridge_status(tmp_path)["online"] is True
    path.write_text(json.dumps({"at": time.time() + 100, "model": "cookllm", "status": "ready"}))
    assert bridge_status(tmp_path)["online"] is False
    path.unlink()
    os.mkfifo(path)
    assert bridge_status(tmp_path)["online"] is False


def test_repeated_package_basename_keeps_both_segments():
    assert python_module("src/flask/flask/nested.py", "src/flask/") == "flask.flask.nested"


def test_restart_consumes_completed_response_and_preserves_intact_pending(fixture):
    from fastapi.testclient import TestClient

    from app.main import create_app

    client, app, source, ipc, manifest = fixture
    pending = queue(client)
    completed = queue(client)
    publish(ipc, mocked_response(app, completed))
    assert app.state.index.row("jobs", completed["id"])["state"] == "queued"
    path = ipc / (pending["id"] + ".request.json")
    mtime = path.stat().st_mtime_ns
    with TestClient(
        create_app(app.state.index.db_path, source, ipc), base_url="http://127.0.0.1"
    ) as resumed:
        assert resumed.get("/api/jobs/" + completed["id"]).json()["state"] == "succeeded"
        assert resumed.get("/api/jobs/" + pending["id"]).json()["state"] == "queued"
        assert path.stat().st_mtime_ns == mtime
        assert resumed.get("/api/state").json()["model_budget"]["attempted"] == 2
