import time

import pytest

from bridge import atomic_json, read_json


def test_fixed_heartbeat_frames_preserve_json_and_previous_file(tmp_path):
    path = tmp_path / "heartbeat.json"
    for status, count in [("ready", 5), ("busy", 10), ("stopped", 10)]:
        value = {"at": time.time(), "model": "cookllm", "status": status, "calls": count}
        atomic_json(path, value, pad_to=256)
        assert path.stat().st_size == 256
        assert read_json(path, 4096) == value
    previous = path.read_bytes()
    with pytest.raises(ValueError):
        atomic_json(path, value, pad_to=8)
    assert path.read_bytes() == previous
