"""Pinned publisher archives; selectively copy source/license bytes, never execute them."""

import argparse
import asyncio
import datetime as dt
import hashlib
import io
import json
import tarfile
from pathlib import Path, PurePosixPath

import httpx

PINNED_COMMITS = {
    "flask": "22d924701a6ae2e4cd01e9a15bbaf3946094af65",
    "zod": "b500a2ee95134f81d74da7911d7378dd2853d042",
}

SOURCES = [
    {
        "id": "flask",
        "repo": "pallets/flask",
        "tag": "3.1.3",
        "prefixes": ["src/flask/"],
        "suffixes": [".py"],
        "license_paths": ["LICENSE.txt"],
        "license": "BSD-3-Clause",
    },
    {
        "id": "zod",
        "repo": "colinhacks/zod",
        "tag": "v4.6.7",
        "prefixes": [
            "packages/zod/src/v4/core/",
            "packages/zod/src/v4/classic/",
            "packages/zod/src/v4/mini/",
        ],
        "suffixes": [".ts"],
        "license_paths": ["LICENSE"],
        "license": "MIT",
    },
]


async def fetch(client, url, cap):
    async with asyncio.timeout(30):
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            if (
                response.status_code != 200
                or response.headers.get("content-encoding", "identity") != "identity"
            ):
                raise ValueError("Expected identity HTTP200")
            body = bytearray()
            async for chunk in response.aiter_raw():
                body.extend(chunk)
                if len(body) > cap:
                    raise ValueError("Publisher response exceeds acquisition bound")
            return bytes(body), dict(
                (k, response.headers.get(k)) for k in ("etag", "last-modified", "content-type")
            )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="data")
    args = parser.parse_args()
    target = Path(args.output_dir)
    if (target / "corpus/manifest.json").exists():
        raise SystemExit("Preserve existing corpus: choose a new output-dir")
    records = []
    all_files = []
    archives = []
    licenses = []
    async with httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=10,
        headers={"User-Agent": "ContourLocalSourceResearch/1.0", "Accept-Encoding": "identity"},
    ) as client:
        for selected in SOURCES:
            ref_url = (
                "https://api.github.com/repos/"
                + selected["repo"]
                + "/git/ref/tags/"
                + selected["tag"]
            )
            ref = json.loads((await fetch(client, ref_url, 100000))[0])
            obj = ref["object"]
            if obj["type"] == "tag":
                obj = json.loads(
                    (
                        await fetch(
                            client,
                            "https://api.github.com/repos/"
                            + selected["repo"]
                            + "/git/tags/"
                            + obj["sha"],
                            100000,
                        )
                    )[0]
                )["object"]
            if obj["type"] != "commit" or len(obj["sha"]) != 40:
                raise ValueError("Tag did not resolve to commit")
            commit = obj["sha"]
            if commit != PINNED_COMMITS[selected["id"]]:
                raise ValueError("Publisher tag changed; preserve pinned source, review manually")
            url = "https://codeload.github.com/" + selected["repo"] + "/tar.gz/" + commit
            body, headers = await fetch(client, url, 12 * 1024 * 1024)
            capture = dt.datetime.now(dt.timezone.utc).isoformat()
            archive_name = selected["id"] + "-" + commit + ".tar.gz"
            archive = tarfile.open(fileobj=io.BytesIO(body), mode="r:gz")
            members = archive.getmembers()
            if len(members) > 20000 or sum(m.size for m in members) > 100 * 1024 * 1024:
                raise ValueError("Expanded archive exceeds bound")
            picked = []
            for member in members:
                parts = PurePosixPath(member.name).parts
                if PurePosixPath(member.name).is_absolute() or ".." in parts or len(parts) < 2:
                    continue
                relative = "/".join(parts[1:])
                path = PurePosixPath(relative)
                wanted = (
                    any(relative.startswith(p) for p in selected["prefixes"])
                    and path.suffix in selected["suffixes"]
                    and not any(p in {"tests", "__tests__", "benchmarks"} for p in path.parts)
                    and not relative.endswith(".test.ts")
                )
                license_file = relative in selected["license_paths"]
                if not wanted and not license_file:
                    continue
                if not member.isfile() or member.size > 512 * 1024:
                    raise ValueError("Selected entry is linked/special/oversize")
                source = archive.extractfile(member).read(512 * 1024 + 1)
                source.decode("utf-8", errors="strict")
                record = {
                    "repo_id": selected["id"],
                    "path": relative,
                    "sha256": hashlib.sha256(source).hexdigest(),
                    "bytes": len(source),
                    "language": "python" if path.suffix == ".py" else "typescript",
                    "url": "https://github.com/"
                    + selected["repo"]
                    + "/blob/"
                    + commit
                    + "/"
                    + relative,
                }
                if license_file:
                    licenses.append((selected["id"] + "/" + relative, source))
                    continue
                all_files.append((selected["id"] + "/" + relative, source))
                picked.append(record)
            if not picked or len(picked) > 150 or sum(p["bytes"] for p in picked) > 3 * 1024 * 1024:
                raise ValueError("Selected source sample outside bounds")
            record = {
                **selected,
                "commit": commit,
                "url": url,
                "archive_name": archive_name,
                "archive_bytes": len(body),
                "archive_sha256": hashlib.sha256(body).hexdigest(),
                "retrieved_at": capture,
                "headers": headers,
                "files": picked,
                "selection": (
                    "runtime source under explicit prefixes; excludes tests/benchmarks/"
                    "locales/v3 and non-code; no code executed"
                ),
            }
            records.append(record)
            archives.append((archive_name, body))
            print(
                json.dumps(
                    {
                        "id": selected["id"],
                        "tag": selected["tag"],
                        "commit": commit,
                        "archive_bytes": len(body),
                        "files": len(picked),
                        "source_bytes": sum(p["bytes"] for p in picked),
                    }
                )
            )
    raw = target / "raw"
    corpus = target / "corpus"
    raw.mkdir(parents=True, exist_ok=True)
    corpus.mkdir(parents=True, exist_ok=True)
    for name, body in archives:
        p = raw / name
        p.write_bytes(body)
    for name, body in [*all_files, *licenses]:
        p = corpus / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    (raw / "manifest.json").write_text(json.dumps(records, indent=2))
    (corpus / "manifest.json").write_text(json.dumps(records, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
