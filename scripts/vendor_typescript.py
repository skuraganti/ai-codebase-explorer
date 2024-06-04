"Pin the official JS compiler API, without npm scripts or any analyzed-code execution."

import base64
import hashlib
import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

URL = "https://registry.npmjs.org/typescript/-/typescript-6.0.3.tgz"
INTEGRITY = (
    "y2TvuxSZPDyQakkFRPZHKFm+KKVqIisdg9/CZwm9ftvKXLP8NRWj38/ODjNbr43SsoXqNuAisEf1GdCxqWcdBw=="
)


def main():
    body = bytearray()
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as client:
        with client.stream("GET", URL, headers={"Accept-Encoding": "identity"}) as r:
            r.raise_for_status()
            for chunk in r.iter_raw():
                body.extend(chunk)
                if len(body) > 6 * 1024 * 1024:
                    raise ValueError("Compiler archive exceeds bound")
    raw = bytes(body)
    if base64.b64encode(hashlib.sha512(raw).digest()).decode() != INTEGRITY:
        raise ValueError("Published compiler integrity mismatch")
    archive = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    members = archive.getmembers()
    if len(members) > 500 or sum(m.size for m in members) > 30 * 1024 * 1024:
        raise ValueError("Expanded compiler archive exceeds bound")
    out = Path("app/ts_vendor")
    out.mkdir(exist_ok=True)
    files = []
    for name in ["lib/typescript.js", "LICENSE.txt", "ThirdPartyNoticeText.txt"]:
        m = archive.getmember("package/" + name)
        if not m.isfile() or m.size > 12 * 1024 * 1024:
            raise ValueError("Unexpected compiler entry")
        data = archive.extractfile(m).read(12 * 1024 * 1024 + 1)
        target = out / Path(name).name
        target.write_bytes(data)
        files.append(
            {"file": target.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )
    record = {
        "name": "TypeScript",
        "publisher": "Microsoft and contributors",
        "version": "6.0.3",
        "license": "Apache-2.0",
        "url": URL,
        "integrity": "sha512-" + INTEGRITY,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "archive_sha256": hashlib.sha256(raw).hexdigest(),
        "files": files,
        "purpose": (
            "Parsing only via createSourceFile; no compiler host, module "
            "resolution, emit, eval or execution of analyzed code."
        ),
    }
    (out / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
