"""Four frozen, explicit real-model tasks. Existing IDs are polled, never re-enqueued."""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        raise SystemExit(
            "No calls made. Use --live for the four authorized, frozen public-source checks."
        )
    tasks = json.loads((ROOT / "evals/heldout.json").read_text())["live_tasks"]
    output_path = ROOT / "data/verification/live-results.json"
    output = (
        json.loads(output_path.read_text())
        if output_path.exists()
        else {
            "protocol": (
                "4 frozen explicit tasks; exact citations machine-checked, "
                "semantic support reviewed separately; no retry; no general accuracy claim"
            ),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "model": "cookllm",
            "actual_billing_usd": None,
            "tasks": [],
        }
    )

    def save():
        output_path.write_text(json.dumps(output, indent=2) + "\n")

    with httpx.Client(base_url="http://127.0.0.1:8284", trust_env=False, timeout=20) as client:

        def get(path):
            r = client.get(path)
            r.raise_for_status()
            return r.json()

        def post(path, body):
            r = client.post(path, json=body)
            r.raise_for_status()
            return r.json()

        for i, task in enumerate(tasks):
            if i < len(output["tasks"]):
                entry = output["tasks"][i]
                if entry.get("final"):
                    continue
            else:
                files = get("/api/files?repo_id=" + task["repo_id"])
                file = next(f for f in files if f["path"] == task["path"])
                detail = get("/api/files/" + file["id"])
                symbols = [s for s in detail["symbols"] if s["qualname"] == task["qualname"]]
                assert len(symbols) == 1, (task["qualname"], len(symbols))
                job = post(
                    "/api/jobs",
                    {
                        "kind": "explain",
                        "symbol_id": symbols[0]["id"],
                        "question": task["question"],
                    },
                )
                entry = {
                    "case": i + 1,
                    **task,
                    "job_id": job["id"],
                    "final": False,
                    "queued_at": datetime.now(timezone.utc).isoformat(),
                }
                output["tasks"].append(entry)
                save()
                print(
                    json.dumps({"case": i + 1, "job_id": job["id"], "state": "queued"}), flush=True
                )
            deadline = time.monotonic() + 125
            while time.monotonic() < deadline:
                job = get("/api/jobs/" + entry["job_id"])
                if job["state"] != "queued":
                    break
                time.sleep(1)
            if job["state"] == "queued":
                entry["observation"] = "Still pending at observer deadline; no new model attempt"
                save()
                raise SystemExit("Check local bridge; existing ID retained, no automatic retry")
            entry.update(final=True, result=job)
            citations = job.get("citations", [])
            for c in citations:
                raw = (ROOT / "data/corpus" / c["repo_id"] / c["path"]).read_text()
                assert c["quote"] in raw
                lines = raw.splitlines(keepends=True)
                excerpt = "".join(lines[c["start_line"] - 1 : c["end_line"]])
                assert c["quote"] in excerpt
            if task["expect"].startswith("abstain"):
                passed = (
                    job["state"] == "succeeded"
                    and not job.get("claims")
                    and bool(job.get("reason"))
                )
            else:
                passed = (
                    job["state"] == "succeeded"
                    and bool(job.get("claims"))
                    and bool(citations)
                    and bool(job.get("uncertainty"))
                )
            entry["structural_criteria_passed"] = passed
            entry["human_semantic_review"] = "pending independent source review"
            save()
            print(
                json.dumps(
                    {
                        "case": i + 1,
                        "state": job["state"],
                        "criteria": passed,
                        "claims": len(job.get("claims", [])),
                        "latency_ms": job.get("latency_ms"),
                        "reason": job.get("reason"),
                    }
                ),
                flush=True,
            )
    output["completed_at"] = datetime.now(timezone.utc).isoformat()
    save()


if __name__ == "__main__":
    main()
