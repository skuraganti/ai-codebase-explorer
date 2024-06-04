"""Contour FastAPI application."""

from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .index import LOCK, Index, IndexValidationError, _read_regular, decode_row
from .jobs import JobQueue
from .security import protect


def coherent(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with LOCK:
            return function(*args, **kwargs)

    return locked


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=200)
    repo_id: str | None = None
    language: Literal["python", "typescript"] | None = None
    limit: int = Field(default=15, ge=1, le=30)
    mode: Literal["symbols", "text"] = "symbols"


class JobRequest(StrictModel):
    kind: Literal["explain", "impact"]
    symbol_id: str | None = None
    file_id: str | None = None
    question: str = Field(default="", max_length=500)


def create_app(
    db_path: str | Path | None = None,
    source_dir: str | Path | None = None,
    bridge_dir: str | Path | None = None,
) -> FastAPI:
    db = Path(db_path or os.getenv("CONTOUR_DB", "data/contour.sqlite"))
    source = Path(source_dir or os.getenv("CONTOUR_SOURCE", "data/corpus"))
    bridge = Path(bridge_dir or os.getenv("CONTOUR_BRIDGE", "data/bridge"))
    index = Index(db, source)
    queue = JobQueue(index, bridge)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        index.initialize()
        try:
            index.rebuild()
        except IndexValidationError as exc:
            with index.connect() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO metadata VALUES('index_error',?)", (str(exc),)
                )
                connection.commit()
        queue.reconcile()
        yield

    app = FastAPI(title="Contour", lifespan=lifespan)
    app.state.index = index
    app.state.queue = queue
    protect(app)
    static = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    @coherent
    def home():
        return FileResponse(static / "index.html")

    @app.get("/health")
    @coherent
    def health() -> dict[str, Any]:
        try:
            with index.connect() as connection:
                connection.execute("SELECT count(*) FROM files").fetchone()
            return {"status": "ok", "revision": index.revision()}
        except Exception as exc:
            raise HTTPException(503, f"database unavailable: {exc}") from exc

    @app.get("/api/state")
    @coherent
    def state() -> dict[str, Any]:
        queue.consume_ready()
        with index.connect() as connection:
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            repos = []
            for row in connection.execute("SELECT * FROM repositories ORDER BY id"):
                repo = dict(row)
                repo["commit"] = repo.pop("commit_sha")
                counts = connection.execute(
                    "SELECT count(*),coalesce(sum(bytes),0) FROM files WHERE repo_id=?",
                    (repo["id"],),
                ).fetchone()
                repo.update({"files": counts[0], "source_bytes": counts[1]})
                for key, sql in {
                    "symbols": "SELECT count(*) FROM symbols WHERE repo_id=?",
                    "imports": "SELECT count(*) FROM imports WHERE repo_id=?",
                    "parse_errors": (
                        "SELECT count(*) FROM files WHERE repo_id=? AND parse_status='error'"
                    ),
                }.items():
                    repo[key] = connection.execute(sql, (repo["id"],)).fetchone()[0]
                repos.append(repo)
            attempted = connection.execute("SELECT count(*) FROM jobs").fetchone()[0]
            pending = connection.execute(
                "SELECT count(*) FROM jobs WHERE state='queued'"
            ).fetchone()[0]
            jobs = [
                decode_row("jobs", row)
                for row in connection.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 20"
                )
            ]
        return {
            "revision": int(metadata.get("revision", 0)),
            "indexed_at": metadata.get("indexed_at"),
            "repositories": repos,
            "counts": index.counts(),
            "index_error": metadata.get("index_error") or None,
            "bridge": bridge_status(bridge),
            "model_budget": {"attempted": attempted, "limit": 10, "pending": pending},
            "jobs": jobs,
        }

    @app.post("/api/index")
    @coherent
    def rebuild(payload: dict[str, Any]) -> dict[str, Any]:
        if payload:
            raise HTTPException(422, "request body must be empty object")
        try:
            result = index.rebuild()
            queue.cleanup_terminal()
            return result
        except IndexValidationError as exc:
            with index.connect() as db:
                db.execute("INSERT OR REPLACE INTO metadata VALUES('index_error',?)", (str(exc),))
                db.commit()
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/files")
    @coherent
    def files(repo_id: str | None = None) -> list[dict[str, Any]]:
        rows = index.rows(
            "files", "repo_id=?" if repo_id else "", (repo_id,) if repo_id else (), "repo_id,path"
        )
        for row in rows:
            row["symbols"] = len(index.rows("symbols", "file_id=?", (row["id"],)))
        return rows

    @app.get("/api/files/{file_id}")
    @coherent
    def file_detail(file_id: str) -> dict[str, Any]:
        file = require(index.row("files", file_id), "file")
        return {
            "file": file,
            "symbols": index.rows("symbols", "file_id=?", (file_id,), "start_line,id"),
            "imports": index.rows("imports", "file_id=?", (file_id,), "line,id"),
        }

    @app.get("/api/files/{file_id}/source")
    @coherent
    def file_source(
        file_id: str, start: Annotated[int, Query(ge=1)] = 1, end: Annotated[int, Query(ge=1)] = 200
    ) -> dict[str, Any]:
        if end < start or end - start + 1 > 200:
            raise HTTPException(422, "invalid source bounds")
        file = require(index.row("files", file_id), "file")
        with index.connect() as connection:
            source_text = connection.execute(
                "SELECT source FROM files WHERE id=?", (file_id,)
            ).fetchone()[0]
        source_lines = source_text.splitlines()
        clipped = min(end, len(source_lines))
        return {
            "file": file,
            "lines": [
                {"number": n, "text": source_lines[n - 1]} for n in range(start, clipped + 1)
            ],
            "total_lines": len(source_lines),
            "truncated": end < len(source_lines),
        }

    @app.get("/api/symbols/{symbol_id}")
    @coherent
    def symbol_detail(symbol_id: str) -> dict[str, Any]:
        symbol = require(index.row("symbols", symbol_id), "symbol")
        return {"symbol": symbol, "file": require(index.row("files", symbol["file_id"]), "file")}

    @app.post("/api/search")
    @coherent
    def search(request: SearchRequest) -> dict[str, Any]:
        began = time.perf_counter()
        hits = search_index(index, request)
        return {
            "query": request.query,
            "mode": request.mode,
            "revision": index.revision(),
            "took_ms": round((time.perf_counter() - began) * 1000, 3),
            "hits": hits,
        }

    @app.get("/api/graph")
    @coherent
    def graph(repo_id: str | None = None, include_types: bool = True) -> dict[str, Any]:
        nodes = index.rows(
            "files", "repo_id=?" if repo_id else "", (repo_id,) if repo_id else (), "repo_id,path"
        )[:200]
        node_ids = {node["id"] for node in nodes}
        symbols = index.rows("symbols")
        for node in nodes:
            node["symbols"] = sum(symbol["file_id"] == node["id"] for symbol in symbols)
        imports = index.rows("imports", order="file_id,target_file_id,line,id")
        eligible = [
            i for i in imports if i["file_id"] in node_ids and (include_types or not i["type_only"])
        ]
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        unresolved = []
        for imp in eligible:
            if imp["status"] == "resolved" and imp["target_file_id"] in node_ids:
                grouped.setdefault((imp["file_id"], imp["target_file_id"]), []).append(imp)
            elif imp["status"] != "resolved":
                unresolved.append(imp)
        edges = [
            {
                "from": pair[0],
                "to": pair[1],
                "imports": values,
                "type_only": all(item["type_only"] for item in values),
            }
            for pair, values in list(grouped.items())[:2000]
        ]
        return {
            "nodes": nodes,
            "edges": edges,
            "unresolved": unresolved,
            "revision": index.revision(),
        }

    @app.get("/api/impact/{file_id}")
    @coherent
    def impact(
        file_id: str, depth: Annotated[int, Query(ge=1, le=5)] = 3, include_types: bool = True
    ) -> dict[str, Any]:
        file = require(index.row("files", file_id), "file")
        files_by_id = {f["id"]: f for f in index.rows("files")}
        imports = [
            i
            for i in index.rows("imports", "status='resolved'", order="file_id,line,id")
            if include_types or not i["type_only"]
        ]
        reverse: dict[str, list[dict[str, Any]]] = {}
        for imp in imports:
            reverse.setdefault(imp["target_file_id"], []).append(imp)
        seen = {file_id}
        frontier = deque([(file_id, [])])
        impacted = []
        while frontier:
            current, path = frontier.popleft()
            if len(path) >= depth:
                continue
            for imp in sorted(
                reverse.get(current, []),
                key=lambda x: (files_by_id[x["file_id"]]["path"], x["id"]),
            ):
                consumer = imp["file_id"]
                if consumer in seen:
                    continue
                seen.add(consumer)
                step = {
                    "from": consumer,
                    "to": current,
                    "import_id": imp["id"],
                    "line": imp["line"],
                    "specifier": imp["specifier"],
                    "type_only": imp["type_only"],
                }
                new_path = [step, *path]
                impacted.append(
                    {
                        "file": files_by_id[consumer],
                        "distance": len(new_path),
                        "path": new_path,
                    }
                )
                frontier.append((consumer, new_path))
        impacted.sort(key=lambda x: (x["distance"], x["file"]["path"]))
        return {
            "file": file,
            "revision": index.revision(),
            "depth": depth,
            "include_types": include_types,
            "impacted": impacted,
            "direct_count": sum(x["distance"] == 1 for x in impacted),
            "total_count": len(impacted),
            "limitations": [
                "Static import reachability only; not a runtime breakage or call graph.",
                f"Traversal capped at depth {depth}.",
            ],
        }

    @app.post("/api/jobs")
    @coherent
    def create_job(request: JobRequest) -> dict[str, Any]:
        if request.kind == "explain":
            if not request.symbol_id or request.file_id:
                raise HTTPException(422, "explain requires only symbol_id")
            target = require(index.row("symbols", request.symbol_id), "symbol")
        else:
            if not request.file_id or request.symbol_id:
                raise HTTPException(422, "impact requires only file_id")
            target = require(index.row("files", request.file_id), "file")
        try:
            return queue.create(request.kind, target, request.question)
        except OverflowError as exc:
            raise HTTPException(429, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "source target no longer indexed") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/jobs/{job_id}")
    @coherent
    def get_job(job_id: str) -> dict[str, Any]:
        return require(queue.get(job_id), "job")

    @app.get("/api/evaluation")
    @coherent
    def evaluation():
        path = source / "evaluation.json"
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
                return {"available": False}
        except OSError:
            return {"available": False}
        return FileResponse(path, media_type="application/json")

    return app


def require(value: Any, noun: str) -> Any:
    if value is None:
        raise HTTPException(404, f"{noun} not found")
    return value


def search_index(index: Index, request: SearchRequest) -> list[dict[str, Any]]:
    query = request.query.casefold()
    with index.connect() as db:
        if request.mode == "symbols":
            rows = db.execute(
                (
                    "SELECT s.*,f.path,f.language FROM symbols s JOIN files f ON "
                    "f.id=s.file_id ORDER BY s.name,s.qualname,s.id"
                )
            ).fetchall()
            hits = []
            for row in rows:
                item = dict(row)
                if (
                    request.repo_id
                    and item["repo_id"] != request.repo_id
                    or request.language
                    and item["language"] != request.language
                ):
                    continue
                searchable = " ".join(
                    (item["name"], item["qualname"], item.get("docstring") or "", item["path"])
                ).casefold()
                if query not in searchable:
                    continue
                symbol = index.row("symbols", item["id"])
                file = index.row("files", symbol["file_id"])
                score = (
                    100.0
                    if symbol["name"].casefold() == query
                    else 50.0
                    if query in symbol["name"].casefold()
                    else 10.0
                )
                hits.append(
                    {
                        "file": file,
                        "symbol": symbol,
                        "line": symbol["start_line"],
                        "excerpt": symbol["signature"],
                        "score": score,
                    }
                )
            return sorted(hits, key=lambda x: (-x["score"], x["file"]["path"], x["line"]))[
                : request.limit
            ]
        rows = db.execute("SELECT * FROM files ORDER BY repo_id,path").fetchall()
        hits = []
        for row in rows:
            item = dict(row)
            if (
                request.repo_id
                and item["repo_id"] != request.repo_id
                or request.language
                and item["language"] != request.language
            ):
                continue
            for number, line in enumerate(item["source"].splitlines(), 1):
                if query in line.casefold():
                    file = decode_row("files", row)
                    hits.append(
                        {
                            "file": file,
                            "symbol": None,
                            "line": number,
                            "excerpt": line[:500],
                            "score": 1.0,
                        }
                    )
                    if len(hits) >= request.limit:
                        return hits
        return hits


def bridge_status(bridge: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_regular(bridge, "heartbeat.json", 4096))
        if not isinstance(value, dict):
            raise ValueError("invalid heartbeat")
        timestamp = value.get("at")
        valid = (
            isinstance(value, dict)
            and type(timestamp) in (int, float)
            and abs(timestamp) <= 10**12
            and math.isfinite(timestamp)
            and 0 <= time.time() - timestamp < 30
            and value.get("model") == "cookllm"
            and value.get("status") in {"ready", "busy"}
        )
        return {"online": valid, "heartbeat_at": timestamp if valid else None}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"online": False, "heartbeat_at": None}


app = create_app()
