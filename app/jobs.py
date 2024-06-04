"""Bounded file-bridge job queue and source-citation validation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .index import LOCK, Index, _json, _loads, _read_regular

MAX_ATTEMPTS = 10
MAX_PENDING = 3
MAX_RESPONSE = 64 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strict_json(data: bytes) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    return json.loads(
        data,
        object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )


class JobQueue:
    def __init__(self, index: Index, bridge_dir: Path) -> None:
        self.index = index
        self.bridge_dir = Path(bridge_dir)

    def reconcile(self) -> None:
        self.bridge_dir.mkdir(parents=True, exist_ok=True)
        with LOCK:
            # Already-returned results are consumed, never paid for again.
            self.consume_ready()
            with self.index.connect() as db:
                pending = db.execute(
                    "SELECT id,revision,kind,question,contexts FROM jobs WHERE state='queued'"
                ).fetchall()
            for row in pending:
                identifier = row["id"]
                try:
                    request = _strict_json(
                        _read_regular(self.bridge_dir, identifier + ".request.json", 16384)
                    )
                    expected = {
                        "id": identifier,
                        "revision": row["revision"],
                        "kind": row["kind"],
                        "question": row["question"],
                        "contexts": _loads(row["contexts"], []),
                    }
                    if request != expected or row["revision"] != self.index.revision():
                        raise ValueError("pending request changed")
                except (ValueError, OSError, TypeError):
                    self._finish(identifier, "failed", "Orphaned or invalid request after restart")
            self.cleanup_terminal()

    def cleanup_terminal(self):
        with LOCK, self.index.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM jobs WHERE state<>'queued'")]
            for identifier in ids:
                self._cleanup(identifier)

    def consume_ready(self) -> None:
        with self.index.connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM jobs WHERE state='queued'")]
        for identifier in ids:
            self.get(identifier)

    def create(self, kind: str, target: dict[str, Any], question: str) -> dict[str, Any]:
        with LOCK:
            return self._create_locked(kind, target, question)

    def _create_locked(self, kind: str, target: dict[str, Any], question: str) -> dict[str, Any]:
        current = self.index.row("symbols" if kind == "explain" else "files", target["id"])
        if current is None:
            raise KeyError("target no longer indexed")
        self.consume_ready()
        question = question.strip() or (
            ("Explain the selected definition using only supplied source; state its limits.")
            if kind == "explain"
            else (
                "Discuss potential static change impact visible in supplied source; "
                "state what is unknown."
            )
        )
        contexts = self._contexts(current, kind)
        if not contexts:
            raise ValueError("no bounded source context available")
        identifier = str(uuid.uuid4())
        revision = self.index.revision()
        request = {
            "id": identifier,
            "revision": revision,
            "kind": kind,
            "question": question,
            "contexts": contexts,
        }
        encoded = _json(request).encode()
        if len(encoded) > 16384:
            raise ValueError("model request exceeds the IPC bound")
        created = _now()
        with LOCK, self.index.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            attempts = db.execute("SELECT count(*) FROM jobs").fetchone()[0]
            pending = db.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0]
            if attempts >= MAX_ATTEMPTS:
                db.rollback()
                raise OverflowError("model attempt limit reached")
            if pending >= MAX_PENDING:
                db.rollback()
                raise OverflowError("pending job limit reached")
            db.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    kind,
                    "queued",
                    question,
                    revision,
                    created,
                    None,
                    "[]",
                    "[]",
                    "[]",
                    None,
                    None,
                    None,
                    None,
                    _json(contexts),
                ),
            )
            db.commit()
        self.bridge_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.bridge_dir / f".{identifier}.request.tmp"
        final = self.bridge_dir / f"{identifier}.request.json"
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(_json(request))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, final)
            directory_fd = os.open(self.bridge_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            with LOCK, self.index.connect() as db:
                db.execute(
                    "UPDATE jobs SET state='failed',reason=?,completed_at=? WHERE id=?",
                    (f"Publish failed: {exc}", _now(), identifier),
                )
                db.commit()
            temporary.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
        return self.index.row("jobs", identifier) or {}

    def _contexts(self, target: dict[str, Any], kind: str = "explain") -> list[dict[str, Any]]:
        file = self.index.row("files", target["file_id"] if "file_id" in target else target["id"])
        if file is None:
            raise KeyError("file not found")
        start = int(target.get("start_line", 1))
        contexts = [
            self._context(
                file, start, min(file["lines"], start + 59, target.get("end_line", file["lines"]))
            )
        ]
        if not contexts[0]:
            return []
        imports = self.index.rows(
            "imports",
            ("target_file_id=?" if kind == "impact" else "file_id=?") + " AND status='resolved'",
            (file["id"],),
            "line,id",
        )
        seen = {file["id"]}
        for imp in imports:
            neighbor_id = imp["file_id"] if kind == "impact" else imp["target_file_id"]
            neighbor = self.index.row("files", neighbor_id)
            if neighbor and neighbor_id not in seen:
                seen.add(neighbor_id)
                begin = imp["line"] if kind == "impact" else 1
                contexts.append(self._context(neighbor, begin, min(neighbor["lines"], begin + 59)))
            if len(contexts) == 4:
                break
        total = 0
        bounded = []
        for context in contexts:
            if not context or not 20 <= len(context["text"]) <= 1800:
                continue
            if total + len(context["text"]) > 7200:
                continue
            total += len(context["text"])
            bounded.append(context)
        return bounded

    def _context(self, file: dict[str, Any], start: int, end: int) -> dict[str, Any]:
        with self.index.connect() as db:
            row = db.execute(
                (
                    "SELECT f.source,f.sha256,r.commit_sha FROM files f JOIN repositories r "
                    "ON r.id=f.repo_id WHERE f.id=?"
                ),
                (file["id"],),
            ).fetchone()
        source, current_hash, commit = row
        lines = source.splitlines(keepends=True)
        end = min(end, start + 59)
        text = "".join(lines[start - 1 : end])
        while len(text) > 1800 and end > start:
            end -= 1
            text = "".join(lines[start - 1 : end])
        if not 20 <= len(text) <= 1800:
            return {}
        return {
            "context_id": uuid.uuid4().hex,
            "file_id": file["id"],
            "repo_id": file["repo_id"],
            "path": file["path"],
            "commit": commit,
            "start_line": start,
            "end_line": end,
            "text": text,
            "sha256": current_hash,
            "url": file["url"],
        }

    def get(self, identifier: str) -> dict[str, Any] | None:
        try:
            if str(uuid.UUID(identifier)) != identifier:
                return None
        except (ValueError, AttributeError):
            return None
        with LOCK:
            return self._get_locked(identifier)

    def _get_locked(self, identifier: str) -> dict[str, Any] | None:
        job = self.index.row("jobs", identifier)
        if job is None:
            return None
        if job["state"] != "queued":
            self._cleanup(identifier)
            return job
        with self.index.connect() as db:
            raw = db.execute(
                "SELECT contexts,created_at,revision FROM jobs WHERE id=?", (identifier,)
            ).fetchone()
        created = datetime.fromisoformat(raw[1]).timestamp()
        if time.time() - created > 360:
            self._finish(identifier, "failed", "Job expired")
            self._cleanup(identifier)
            return self.index.row("jobs", identifier)
        response_path = self.bridge_dir / f"{identifier}.response.json"
        try:
            descriptor = os.open(response_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RESPONSE:
                    raise ValueError("invalid response file")
                data = stream.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE:
                raise ValueError("response grew beyond bound")
            response = _strict_json(data)
            update = self._validate(response, identifier, raw[2], _loads(raw[0], []))
        except FileNotFoundError:
            return job
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._finish(identifier, "failed", f"Invalid model response: {exc}")
        else:
            self._succeed(identifier, update)
        self._cleanup(identifier)
        return self.index.row("jobs", identifier)

    def _validate(
        self, value: Any, identifier: str, revision: int, contexts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) - {
            "id",
            "revision",
            "state",
            "model",
            "latency_ms",
            "usage",
            "error",
            "output",
        }:
            raise ValueError("invalid response shape")
        if revision != self.index.revision():
            raise ValueError("index revision changed")
        for context in contexts:
            with self.index.connect() as db:
                row = db.execute(
                    "SELECT f.source,f.sha256,r.commit_sha FROM files f "
                    "JOIN repositories r ON r.id=f.repo_id WHERE f.id=?",
                    (context["file_id"],),
                ).fetchone()
            if row is None or row[1] != context["sha256"] or row[2] != context["commit"]:
                raise ValueError("source context is stale")
            if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
                raise ValueError("indexed source digest mismatch")
            excerpt = "".join(
                row[0].splitlines(keepends=True)[context["start_line"] - 1 : context["end_line"]]
            )
            if context["text"] not in (excerpt, excerpt.rstrip("\r\n")):
                raise ValueError("context no longer matches indexed source")
        response_revision = value.get("revision")
        if (
            value.get("id") != identifier
            or type(response_revision) is not int
            or response_revision != revision
            or value.get("model") != "cookllm"
        ):
            raise ValueError("response identity mismatch")
        latency = value.get("latency_ms")
        if (
            type(latency) not in {int, float}
            or not 0 <= latency <= 86_400_000
            or not math.isfinite(latency)
        ):
            raise ValueError("invalid latency")
        usage = value.get("usage", {})
        if not isinstance(usage, dict) or len(usage) > 20:
            raise ValueError("invalid usage")
        for key, number in usage.items():
            if (
                not isinstance(key, str)
                or type(number) not in {int, float}
                or abs(number) > 10**15
                or not math.isfinite(number)
                or number < 0
            ):
                raise ValueError("invalid usage counter")
        if value.get("state") == "failed":
            error = value.get("error")
            if not isinstance(error, str) or not 1 <= len(error) <= 1000:
                raise ValueError("invalid failure")
            return {
                "state": "failed",
                "reason": error,
                "model": "cookllm",
                "usage": usage,
                "latency_ms": latency,
            }
        if value.get("state") != "succeeded" or not isinstance(value.get("output"), dict):
            raise ValueError("invalid state")
        output = value["output"]
        if (
            set(output) != {"abstain", "reason", "claims", "uncertainty"}
            or type(output["abstain"]) is not bool
        ):
            raise ValueError("invalid output shape")
        claims, uncertainty = output["claims"], output["uncertainty"]
        if (
            not isinstance(claims, list)
            or len(claims) > 5
            or not isinstance(uncertainty, list)
            or len(uncertainty) > 8
        ):
            raise ValueError("invalid claims")
        if not output["abstain"] and (not 1 <= len(claims) <= 5 or not 1 <= len(uncertainty) <= 8):
            raise ValueError("successful answer requires bounded claims and uncertainty")
        if not isinstance(output["reason"], str) or len(output["reason"]) > 1000:
            raise ValueError("invalid reason")
        if not all(isinstance(x, str) and 1 <= len(x) <= 500 for x in uncertainty):
            raise ValueError("invalid uncertainty")
        if not output["abstain"] and not uncertainty:
            raise ValueError("successful answer requires uncertainty")
        if output["abstain"] and claims:
            raise ValueError("abstention cannot contain claims")
        by_id = {c["context_id"]: c for c in contexts}
        citations = []
        clean_claims = []
        for claim in claims:
            if (
                not isinstance(claim, dict)
                or set(claim) != {"text", "supports"}
                or not isinstance(claim["text"], str)
                or not 1 <= len(claim["text"]) <= 1000
            ):
                raise ValueError("invalid claim")
            supports = claim["supports"]
            if not isinstance(supports, list) or not 1 <= len(supports) <= 3:
                raise ValueError("invalid supports")
            claim_citations = []
            for support in supports:
                if not isinstance(support, dict) or set(support) != {"context_id", "quote"}:
                    raise ValueError("invalid support")
                if not isinstance(support["context_id"], str):
                    raise ValueError("invalid context id")
                context = by_id.get(support["context_id"])
                quote = support["quote"]
                if context is None:
                    raise ValueError("unknown source context id")
                if not isinstance(quote, str) or not 20 <= len(quote) <= 400:
                    raise ValueError("quote must contain20..400exact characters")
                offset = context["text"].find(quote)
                if offset < 0:
                    raise ValueError("quote is not exact context text")
                start = context["start_line"] + context["text"][:offset].count("\n")
                end = start + quote.rstrip("\r\n").count("\n")
                citation = {
                    key: context[key]
                    for key in (
                        "context_id",
                        "file_id",
                        "repo_id",
                        "path",
                        "commit",
                        "sha256",
                        "url",
                    )
                }
                citation.update({"start_line": start, "end_line": end, "quote": quote})
                citations.append(citation)
                claim_citations.append(citation)
            clean_claims.append({"text": claim["text"], "supports": claim_citations})
        return {
            "state": "succeeded",
            "claims": clean_claims,
            "citations": citations,
            "uncertainty": uncertainty,
            "reason": output["reason"],
            "model": "cookllm",
            "usage": usage,
            "latency_ms": latency,
        }

    def _finish(self, identifier: str, state: str, reason: str) -> None:
        with LOCK, self.index.connect() as db:
            db.execute(
                ("UPDATE jobs SET state=?,reason=?,completed_at=? WHERE id=? AND state='queued'"),
                (state, reason, _now(), identifier),
            )
            db.commit()

    def _succeed(self, identifier: str, result: dict[str, Any]) -> None:
        with LOCK, self.index.connect() as db:
            if result["state"] == "failed":
                db.execute(
                    (
                        "UPDATE jobs SET state='failed',reason=?,model=?,usage=?,late"
                        "ncy_ms=?,completed_at=? WHERE id=? AND state='queued'"
                    ),
                    (
                        result["reason"],
                        result["model"],
                        _json(result["usage"]),
                        result["latency_ms"],
                        _now(),
                        identifier,
                    ),
                )
            else:
                db.execute(
                    (
                        "UPDATE jobs SET state='succeeded',claims=?,citations=?,uncer"
                        "tainty=?,reason=?,model=?,usage=?,latency_ms=?,completed_at=? WHERE "
                        "id=? AND state='queued'"
                    ),
                    (
                        _json(result["claims"]),
                        _json(result["citations"]),
                        _json(result["uncertainty"]),
                        result["reason"],
                        result["model"],
                        _json(result["usage"]),
                        result["latency_ms"],
                        _now(),
                        identifier,
                    ),
                )
            db.commit()

    def _cleanup(self, identifier: str) -> None:
        for suffix in ("request.json", "response.json"):
            try:
                (self.bridge_dir / f"{identifier}.{suffix}").unlink(missing_ok=True)
            except OSError:
                # A malformed directory cannot be recursively removed via a job lookup.
                pass


def stat_is_symlink(mode: int) -> bool:
    import stat

    return stat.S_ISLNK(mode)
