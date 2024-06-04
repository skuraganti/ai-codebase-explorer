"""Fetch a pinned MIT browser library without running package scripts."""

import base64
import hashlib
import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

VERSION = "3.34.4"
URL = f"https://registry.npmjs.org/cytoscape/-/cytoscape-{VERSION}.tgz"
INTEGRITY = (
    "msY99tMY8iWTg+sIPGfSqDcPNOt4xcj+v/K/7qUFaCUxxlqnMkUYLBjRUzn9W5CU/R2hrmtJa5+UtovrEM2ctg=="
)


def main():
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as client:
        with client.stream("GET", URL, headers={"Accept-Encoding": "identity"}) as response:
            response.raise_for_status()
            body = bytearray()
            for part in response.iter_raw():
                body.extend(part)
                if len(body) > 4 * 1024 * 1024:
                    raise ValueError("Vendor download exceeds bound")
    raw = bytes(body)
    if base64.b64encode(hashlib.sha512(raw).digest()).decode() != INTEGRITY:
        raise ValueError("npm published integrity mismatch")
    archive = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    members = archive.getmembers()
    if len(members) > 500 or sum(m.size for m in members) > 10 * 1024 * 1024:
        raise ValueError("Expanded vendor archive exceeds bound")
    output = Path("app/static/vendor")
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for entry, name in [
        ("package/dist/cytoscape.min.js", "cytoscape.min.js"),
        ("package/LICENSE", "CYTOSCAPE-LICENSE.txt"),
    ]:
        member = archive.getmember(entry)
        if not member.isfile() or member.size > 1024 * 1024:
            raise ValueError("Unexpected vendor entry")
        data = archive.extractfile(member).read(1024 * 1024 + 1)
        (output / name).write_bytes(data)
        records.append(
            {"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )
    record = {
        "name": "Cytoscape.js",
        "version": VERSION,
        "license": "MIT",
        "url": URL,
        "integrity": "sha512-" + INTEGRITY,
        "archive_sha256": hashlib.sha256(raw).hexdigest(),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "files": records,
        "use": (
            "Graph rendering; original Contour interface and static analysis. "
            "No package scripts run."
        ),
    }
    (output / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
