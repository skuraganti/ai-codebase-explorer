"""Guided impact case study and static-boundary checks (docs/IMPACT_CASE.md, STATIC_BOUNDARIES.md).

The real-corpus checks index a temporary copy of the bootstrapped Flask/Zod snapshot as data; no
upstream code is imported, built or executed, and no model is called. Expected values are an
independently reviewed oracle tied to pinned source lines, not a re-implementation of the BFS.
"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
FLASK_COMMIT = "22d924701a6ae2e4cd01e9a15bbaf3946094af65"
PROVIDER = "src/flask/json/provider.py"
PROVIDER_ID = "80d2bb1d8671065fb49b726ce6b0a2830aa7c1e4a99e64a68026a19f6af43e90"
PROVIDER_SHA = "e62984cd8e478d5d87a1456b41b24ba970b330da595df0f46355ea74b5765c10"
# Reviewed depth-3 cohort with type-only imports excluded: path -> hop distance.
NO_TYPES = {
    "src/flask/json/__init__.py": 1,
    "src/flask/sansio/app.py": 1,
    "src/flask/__init__.py": 2,
    "src/flask/app.py": 2,
    "src/flask/debughelpers.py": 2,
    "src/flask/json/tag.py": 2,
    "src/flask/wrappers.py": 2,
    "src/flask/cli.py": 3,
    "src/flask/sessions.py": 3,
    "src/flask/templating.py": 3,
}
ZOD = "packages/zod/src/v4/"


def _open(corpus: Path, db: Path):
    return TestClient(create_app(db, corpus, db.parent / "bridge"), base_url="http://127.0.0.1")


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    work = tmp_path_factory.mktemp("case")
    corpus = shutil.copytree(ROOT / "data/corpus", work / "corpus")  # run scripts/bootstrap.py
    with _open(corpus, work / "index.sqlite") as client:
        files = {f["path"]: f for f in client.get("/api/files").json()}
        yield client, files, corpus, work / "index.sqlite"


def _imports(client, files, path):
    return client.get("/api/files/" + files[path]["id"]).json()["imports"]


def _impact(client, include_types, depth=3):
    flag = str(include_types).lower()
    url = f"/api/impact/{PROVIDER_ID}?depth={depth}&include_types={flag}"
    return client.get(url).json()


def _raw_line(corpus, repo, path, number):
    return (corpus / repo / path).read_text(encoding="utf-8").splitlines()[number - 1]


def test_provider_pin_and_dumps_lines(real):
    client, files, corpus, _ = real
    assert client.get("/api/state").json()["counts"]["files"] == 66
    provider = files[PROVIDER]
    assert (provider["id"], provider["sha256"]) == (PROVIDER_ID, PROVIDER_SHA)
    assert hashlib.sha256((corpus / "flask" / PROVIDER).read_bytes()).hexdigest() == PROVIDER_SHA
    assert f"/blob/{FLASK_COMMIT}/{PROVIDER}" in provider["url"]
    symbols = client.get("/api/files/" + PROVIDER_ID).json()["symbols"]
    dumps = next(s for s in symbols if s["qualname"] == "DefaultJSONProvider.dumps")
    assert (dumps["start_line"], dumps["end_line"]) == (166, 179)
    lines = client.get(f"/api/files/{PROVIDER_ID}/source?start=166&end=179").json()["lines"]
    assert lines[0]["text"] == "    def dumps(self, obj: t.Any, **kwargs: t.Any) -> str:"
    assert lines[-1]["text"] == "        return json.dumps(obj, **kwargs)"


def test_two_direct_consumers_with_exact_statements(real):
    client, files, _, _ = real
    direct = _impact(client, True, depth=1)
    assert sorted(x["file"]["path"] for x in direct["impacted"]) == [
        "src/flask/json/__init__.py",
        "src/flask/sansio/app.py",
    ]
    statements = {
        (path, i["line"]): i["statement"]
        for path in files
        for i in _imports(client, files, path)
        if i["target_file_id"] == PROVIDER_ID
    }
    assert statements == {
        ("src/flask/sansio/app.py", 26): "from ..json.provider import DefaultJSONProvider",
        ("src/flask/sansio/app.py", 27): "from ..json.provider import JSONProvider",
        ("src/flask/json/__init__.py", 7): "from .provider import _default",
    }


def test_depth_three_cohorts_and_target_never_its_own_consumer(real):
    client, _, _, _ = real
    with_types, without = _impact(client, True), _impact(client, False)
    assert (with_types["direct_count"], with_types["total_count"]) == (2, 19)
    assert (without["direct_count"], without["total_count"]) == (2, 10)
    assert {x["file"]["path"]: x["distance"] for x in without["impacted"]} == NO_TYPES
    assert set(NO_TYPES) < {x["file"]["path"] for x in with_types["impacted"]}
    for report in (with_types, without):  # provider -> sansio.app -> provider is a type-only cycle
        assert PROVIDER_ID not in {x["file"]["id"] for x in report["impacted"]}


def test_every_path_step_is_a_pinned_import_statement(real):
    client, files, corpus, _ = real
    by_id = {f["id"]: f for f in files.values()}
    paths = {}
    for consumer in _impact(client, False)["impacted"]:
        hops = []
        for step in consumer["path"]:
            source = by_id[step["from"]]["path"]
            record = next(
                i for i in _imports(client, files, source) if i["id"] == step["import_id"]
            )
            assert record["target_file_id"] == step["to"] and record["line"] == step["line"]
            assert _raw_line(corpus, "flask", source, step["line"]).strip() == record["statement"]
            hops.append((source, step["line"]))
        assert consumer["path"][-1]["to"] == PROVIDER_ID
        paths[consumer["file"]["path"]] = hops
    assert paths["src/flask/app.py"][0] == ("src/flask/app.py", 44)
    assert paths["src/flask/app.py"][1] in {
        ("src/flask/sansio/app.py", 26),
        ("src/flask/sansio/app.py", 27),
    }
    assert paths["src/flask/wrappers.py"] == [
        ("src/flask/wrappers.py", 10),
        ("src/flask/json/__init__.py", 7),
    ]


def test_zod_flask_boundaries_on_the_real_snapshot(real):
    client, files, _, _ = real
    parse = {i["line"]: i for i in _imports(client, files, ZOD + "core/parse.ts")}
    assert (parse[3]["type_only"], parse[3]["status"]) == (True, "resolved")
    core = files[ZOD + "core/index.ts"]["id"]
    external = {
        i["line"]: i["type_only"]
        for i in _imports(client, files, ZOD + "classic/external.ts")
        if i["target_file_id"] == core
    }
    assert external == {1: False, 8: True, 10: True, 11: False}
    edges = client.get("/api/graph?include_types=false").json()["edges"]
    pair = next(
        e
        for e in edges
        if e["from"] == files[ZOD + "classic/external.ts"]["id"] and e["to"] == core
    )
    assert [i["line"] for i in pair["imports"]] == [1, 11] and pair["type_only"] is False
    for path, line in [
        ("core/index.ts", 11),
        ("classic/external.ts", 43),
        ("classic/schemas.ts", 8),
    ]:
        record = next(i for i in _imports(client, files, ZOD + path) if i["line"] == line)
        assert "locales/" in record["specifier"] and record["status"] == "outside_sample"
    werkzeug = next(i for i in _imports(client, files, PROVIDER) if i["line"] == 11)
    assert (werkzeug["specifier"], werkzeug["status"]) == ("werkzeug.http", "external")
    assert not [i for i in _imports(client, files, "src/flask/cli.py") if i["line"] == 245]
    assert not [i for p in files for i in _imports(client, files, p) if i["status"] == "dynamic"]


def test_unchanged_rescan_and_reopen_keep_the_index(real):
    client, _, corpus, db = real
    before = client.get("/api/state").json()
    assert client.post("/api/index", json={}).json()["changed"] is False
    with _open(corpus, db) as reopened:
        after = reopened.get("/api/state").json()
        assert (after["revision"], after["counts"]) == (before["revision"], before["counts"])
        assert (
            reopened.get(f"/api/impact/{PROVIDER_ID}?include_types=false").json()["total_count"]
            == 10
        )


def test_hash_failure_on_real_copy_keeps_last_good_index(tmp_path):
    corpus = shutil.copytree(ROOT / "data/corpus", tmp_path / "corpus")
    with _open(corpus, tmp_path / "index.sqlite") as client:
        before = client.get("/api/state").json()
        with (corpus / "flask" / PROVIDER).open("a", encoding="utf-8") as handle:
            handle.write("# local edit without a manifest update\n")
        assert client.post("/api/index", json={}).status_code == 422
        state = client.get("/api/state").json()
        assert (state["revision"], state["counts"]) == (before["revision"], before["counts"])
        assert state["index_error"]
        assert client.get(f"/api/impact/{PROVIDER_ID}").json()["total_count"] == 19


SYNTHETIC = {  # parsed as data only; never imported or executed
    "pkg/__init__.py": "",
    "pkg/good.py": "from .broken import oops\nfrom . import broken\nimport pkg.broken\n",
    "pkg/broken.py": "def oops(:\n    return 1\n",
    "ts/types.ts": "export type T = string;\n",
    "ts/dep.ts": "export type Shape = { a: number };\nexport const run = () => 1;\n",
    "ts/agg.ts": (
        'export type { T } from "./types.js";\n'
        'export type { Shape } from "./dep.js";\n'
        'export { run } from "./dep.js";\n'
        'const later = import("./later.js");\n'
    ),
}


@pytest.fixture
def synthetic(tmp_path):
    corpus = tmp_path / "corpus"
    records = []
    for path, text in SYNTHETIC.items():
        (corpus / "sample" / path).parent.mkdir(parents=True, exist_ok=True)
        (corpus / "sample" / path).write_text(text)
        records.append(
            dict(
                repo_id="sample",
                path=path,
                bytes=len(text.encode()),
                sha256=hashlib.sha256(text.encode()).hexdigest(),
                language="python" if path.endswith(".py") else "typescript",
                url="https://github.com/example/sample/blob/" + "f" * 40 + "/" + path,
            )
        )
    manifest = dict(id="sample", repo="example/sample", tag="fixture", commit="f" * 40)
    manifest |= dict(
        license="MIT", prefixes=["pkg/", "ts/"], suffixes=[".py", ".ts"], files=records
    )
    (corpus / "manifest.json").write_text(json.dumps([manifest]))
    with _open(corpus, tmp_path / "index.sqlite") as client:
        yield client, {f["path"]: f for f in client.get("/api/files").json()}, corpus


def test_mixed_reexport_pair_survives_type_filter_and_dynamic_stays_unresolved(synthetic):
    client, files, _ = synthetic
    agg, dep, types = (files["ts/" + n]["id"] for n in ("agg.ts", "dep.ts", "types.ts"))
    edges = {
        (e["from"], e["to"]): e
        for e in client.get("/api/graph?include_types=false").json()["edges"]
    }
    assert (agg, types) not in edges
    assert [i["line"] for i in edges[(agg, dep)]["imports"]] == [3]
    dynamic = next(i for i in _imports(client, files, "ts/agg.ts") if i["line"] == 4)
    assert (dynamic["status"], dynamic["target_file_id"]) == ("dynamic", None)


def test_syntax_error_file_is_inspectable_without_invented_rows(synthetic):
    client, files, corpus = synthetic
    broken = client.get("/api/files/" + files["pkg/broken.py"]["id"]).json()
    assert broken["file"]["parse_status"] == "error" and broken["file"]["diagnostics"]
    assert broken["symbols"] == [] and broken["imports"] == []
    lines = client.get(f"/api/files/{files['pkg/broken.py']['id']}/source").json()["lines"]
    assert [line["text"] for line in lines] == SYNTHETIC["pkg/broken.py"].splitlines()
    assert (corpus / "sample/pkg/broken.py").read_text() == SYNTHETIC["pkg/broken.py"]
    # Only parse-ok files are resolution targets, so this in-sample import reads outside_sample.
    labels = [(i["status"], i["target_file_id"]) for i in _imports(client, files, "pkg/good.py")]
    package = files["pkg/__init__.py"]["id"]
    assert labels == [("outside_sample", None), ("resolved", package), ("external", None)]
