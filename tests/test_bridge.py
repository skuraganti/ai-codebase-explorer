"""Offline public-boundary tests; none invokes a model."""

import copy
import hashlib
import json
import uuid
from pathlib import Path

import pytest

import bridge

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def request_payload():
    repo = json.loads((ROOT / "data/corpus/manifest.json").read_text())[0]
    f = next(f for f in repo["files"] if f["path"] == "src/flask/views.py")
    lines = (ROOT / "data/corpus/flask" / f["path"]).read_text().splitlines(keepends=True)
    return dict(
        id=str(uuid.uuid4()),
        revision=1,
        kind="explain",
        question="How is the handler selected?",
        contexts=[
            dict(
                context_id="ctx",
                file_id=hashlib.sha256(("flask:" + f["path"]).encode()).hexdigest(),
                repo_id="flask",
                path=f["path"],
                commit=repo["commit"],
                sha256=f["sha256"],
                start_line=182,
                end_line=191,
                text="".join(lines[181:191]).rstrip("\r\n"),
                url=f["url"],
            )
        ],
    )


def test_exact_public_lines_allowed(request_payload):
    result = bridge.approved_context(request_payload, ROOT / "data/corpus/manifest.json")
    assert result["contexts"][0]["text"] == request_payload["contexts"][0]["text"]
    assert set(result["contexts"][0]) == {
        "context_id",
        "path",
        "repo_id",
        "start_line",
        "end_line",
        "text",
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("text", "private employer sentence" * 8),
        ("sha256", "0" * 64),
        ("file_id", "private-file"),
        ("path", "../../secret"),
        ("repo_id", "private"),
        ("commit", "0" * 40),
        ("start_line", True),
        ("end_line", 999999),
        ("context_id", []),
    ],
)
def test_unapproved_source_metadata_refused(request_payload, key, value):
    request_payload["contexts"][0][key] = value
    with pytest.raises((ValueError, TypeError)):
        bridge.approved_context(request_payload, ROOT / "data/corpus/manifest.json")


def test_blank_question_and_boolean_revision_refused(request_payload):
    changed = copy.deepcopy(request_payload)
    changed["question"] = ""
    with pytest.raises(ValueError):
        bridge.approved_context(changed, ROOT / "data/corpus/manifest.json")
    request_payload["revision"] = True
    with pytest.raises(ValueError):
        bridge.approved_context(request_payload, ROOT / "data/corpus/manifest.json")


def test_atomic_queue_refuses_symlink_without_touching_target(tmp_path):
    target = tmp_path / "unrelated.txt"
    target.write_text("preserve")
    path = tmp_path / "answer.json"
    path.symlink_to(target)
    with pytest.raises(ValueError):
        bridge.atomic_json(path, {"a": 1})
    assert target.read_text() == "preserve"


def test_atomic_private_permissions_and_valid_json(tmp_path):
    path = tmp_path / "answer.json"
    bridge.atomic_json(path, {"one": 1})
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text()) == {"one": 1}


def test_changed_publisher_manifest_refused(request_payload, tmp_path):
    manifest = json.loads((ROOT / "data/corpus/manifest.json").read_text())
    manifest[0]["commit"] = "f" * 40
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="pinned"):
        bridge.approved_context(request_payload, path)


def test_bounded_json_rejects_duplicates_and_oversize(tmp_path):
    path = tmp_path / "request.json"
    path.write_text('{"id":1,"id":2}')
    with pytest.raises(ValueError, match="Duplicate"):
        bridge.read_json(path, 1024)
    path.write_text(" " * 2048)
    with pytest.raises(ValueError):
        bridge.read_json(path, 1024)


def test_special_file_cannot_block_reader(tmp_path):
    import os

    path = tmp_path / "request.json"
    os.mkfifo(path)
    with pytest.raises(ValueError, match="regular"):
        bridge.read_json(path, 100)


def test_predictable_temporary_link_is_not_followed(tmp_path):
    target = tmp_path / "unrelated.txt"
    target.write_text("preserve")
    old_temp = tmp_path / "answer.json.tmp"
    old_temp.symlink_to(target)
    bridge.atomic_json(tmp_path / "answer.json", {"safe": True})
    assert target.read_text() == "preserve"
