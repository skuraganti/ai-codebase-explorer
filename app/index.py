"""Validated manifest ingestion and transactional SQLite index."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .parser import parse_source, python_module

MAX_FILES = 200
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
LOCK = threading.RLock()


class IndexValidationError(ValueError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _loads(value: str | None, default: Any = None) -> Any:
    return default if value is None else json.loads(value)


def _safe_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise IndexValidationError("invalid manifest path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise IndexValidationError(f"unsafe manifest path: {value!r}")
    return value


def _read_regular(root: Path, relative: str, limit: int) -> bytes:
    """Walk directory descriptors, rejecting links and special files before reading."""
    _safe_path(relative)
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(os.path.abspath(root)).parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise IndexValidationError(f"source is not a bounded regular file: {relative}")
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise IndexValidationError(f"source exceeds size limit: {relative}")
        return data
    except OSError as exc:
        raise IndexValidationError(f"source path unavailable or unsafe: {relative}") from exc
    finally:
        os.close(directory)


def _validate_string(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise IndexValidationError(f"manifest field {key} must be a non-empty string")
    return value


def load_snapshot(source_dir: Path) -> dict[str, Any]:
    try:
        raw = _read_regular(source_dir, "manifest.json", MAX_TOTAL_BYTES)
        manifest = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise IndexValidationError(f"invalid manifest: {exc}") from exc
    if not isinstance(manifest, list) or not manifest:
        raise IndexValidationError("manifest must be a non-empty list")
    repositories: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    seen_repos: set[str] = set()
    seen_paths: set[tuple[str, str]] = set()
    total = 0
    for repo in manifest:
        if not isinstance(repo, dict):
            raise IndexValidationError("repository record must be an object")
        repo_id = _validate_string(repo, "id")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", repo_id):
            raise IndexValidationError("unsafe repository identifier")
        if repo_id in seen_repos:
            raise IndexValidationError(f"duplicate repository id: {repo_id}")
        seen_repos.add(repo_id)
        prefixes = repo.get("prefixes")
        suffixes = repo.get("suffixes")
        listed = repo.get("files")
        valid_prefixes = (
            isinstance(prefixes, list)
            and prefixes
            and all(isinstance(value, str) for value in prefixes)
        )
        if valid_prefixes:
            for prefix in prefixes:
                if not prefix.endswith("/"):
                    raise IndexValidationError("prefix must end in slash")
                _safe_path(prefix[:-1])
        if not valid_prefixes:
            raise IndexValidationError("prefixes must be a non-empty string list")
        valid_suffixes = (
            isinstance(suffixes, list)
            and suffixes
            and all(value in {".py", ".ts"} for value in suffixes)
        )
        if not valid_suffixes:
            raise IndexValidationError("suffixes must contain only .py or .ts")
        if not isinstance(listed, list):
            raise IndexValidationError("files must be a list")
        repositories.append(
            {
                "id": repo_id,
                "name": _validate_string(repo, "repo"),
                "tag": _validate_string(repo, "tag"),
                "commit": _validate_string(repo, "commit"),
                "license": _validate_string(repo, "license"),
            }
        )
        if not re.fullmatch(r"[a-f0-9]{40}", repositories[-1]["commit"]):
            raise IndexValidationError("invalid pinned commit")
        for item in listed:
            if len(files) >= MAX_FILES or not isinstance(item, dict):
                raise IndexValidationError("file count exceeds limit or record is invalid")
            path = _safe_path(item.get("path"))
            if item.get("repo_id") != repo_id or (repo_id, path) in seen_paths:
                raise IndexValidationError("duplicate path or repo_id mismatch")
            seen_paths.add((repo_id, path))
            in_prefix = any(path.startswith(prefix) for prefix in prefixes)
            has_suffix = any(path.endswith(suffix) for suffix in suffixes)
            if not in_prefix or not has_suffix:
                raise IndexValidationError(f"file outside allowed selection: {path}")
            expected_size = item.get("bytes")
            expected_hash = item.get("sha256")
            language = item.get("language")
            if type(expected_size) is not int or not 0 <= expected_size <= MAX_FILE_BYTES:
                raise IndexValidationError(f"invalid byte count: {path}")
            if language not in {"python", "typescript"}:
                raise IndexValidationError(f"invalid language: {path}")
            if (language == "python") != path.endswith(".py"):
                raise IndexValidationError("source extension and language disagree")
            expected_url = (
                "https://github.com/"
                + repositories[-1]["name"]
                + "/blob/"
                + repositories[-1]["commit"]
                + "/"
                + path
            )
            if item.get("url") != expected_url:
                raise IndexValidationError("source URL does not match pinned provenance")
            data = _read_regular(source_dir / repo_id, path, MAX_FILE_BYTES)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise IndexValidationError("selected source exceeds total size limit")
            digest = hashlib.sha256(data).hexdigest()
            if len(data) != expected_size or digest != expected_hash:
                raise IndexValidationError(f"size or hash mismatch: {repo_id}/{path}")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise IndexValidationError(f"source is not UTF-8: {path}") from exc
            file_id = hashlib.sha256(f"{repo_id}:{path}".encode()).hexdigest()
            files.append(
                {
                    "id": file_id,
                    "repo_id": repo_id,
                    "path": path,
                    "language": language,
                    "sha256": digest,
                    "bytes": len(data),
                    "lines": len(text.splitlines()),
                    "url": _validate_string(item, "url"),
                    "source": text,
                    "prefix": next(prefix for prefix in prefixes if path.startswith(prefix)),
                }
            )
    fingerprint = hashlib.sha256(
        _json(
            {
                "parser_version": "contour-python-ast-ts6-api-v2",
                "repositories": repositories,
                "files": [
                    {
                        k: f[k]
                        for k in ("id", "path", "sha256", "language", "url", "prefix", "bytes")
                    }
                    for f in files
                ],
            }
        ).encode()
    ).hexdigest()
    return {"repositories": repositories, "files": files, "fingerprint": fingerprint}


class Index:
    def __init__(self, db_path: Path, source_dir: Path) -> None:
        self.db_path = Path(db_path)
        self.source_dir = Path(source_dir)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with LOCK, self.connect() as db:
            db.executescript(SCHEMA)

    def revision(self, db: sqlite3.Connection | None = None) -> int:
        own = db is None
        db = db or self.connect()
        try:
            row = db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()
            return int(row[0]) if row else 0
        finally:
            if own:
                db.close()

    def rebuild(self) -> dict[str, Any]:
        snapshot = load_snapshot(self.source_dir)
        self.initialize()
        with LOCK, self.connect() as db:
            current = db.execute("SELECT value FROM metadata WHERE key='fingerprint'").fetchone()
            if current and current[0] == snapshot["fingerprint"]:
                db.execute("INSERT OR REPLACE INTO metadata VALUES('index_error','')")
                db.commit()
                return {"changed": False, "revision": self.revision(db), "counts": self.counts(db)}
            parsed: list[tuple[dict[str, Any], Any]] = []
            for file in snapshot["files"]:
                result = parse_source(file["source"], file["language"], file["id"], file["repo_id"])
                parsed.append((file, result))
            revision = self.revision(db) + 1
            now = datetime.now(timezone.utc).isoformat()
            db.execute("BEGIN IMMEDIATE")
            try:
                for table in ("imports", "symbols", "files", "repositories"):
                    db.execute(f"DELETE FROM {table}")
                for repo in snapshot["repositories"]:
                    db.execute(
                        "INSERT INTO repositories VALUES(?,?,?,?,?)",
                        (repo["id"], repo["name"], repo["tag"], repo["commit"], repo["license"]),
                    )
                for file, result in parsed:
                    status = "error" if result.diagnostics else "ok"
                    db.execute(
                        "INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            file["id"],
                            file["repo_id"],
                            file["path"],
                            file["language"],
                            file["sha256"],
                            file["bytes"],
                            file["lines"],
                            status,
                            _json(result.diagnostics),
                            file["url"],
                            file["source"],
                            file["prefix"],
                        ),
                    )
                    if status == "ok":
                        for symbol in result.symbols:
                            db.execute(
                                "INSERT INTO symbols VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                (
                                    symbol["id"],
                                    symbol["file_id"],
                                    symbol["repo_id"],
                                    symbol["name"],
                                    symbol["qualname"],
                                    symbol["kind"],
                                    symbol["start_line"],
                                    symbol["end_line"],
                                    symbol["signature"],
                                    symbol.get("docstring"),
                                    int(symbol["exported"]),
                                    " ".join(
                                        (
                                            symbol["name"],
                                            symbol["qualname"],
                                            symbol.get("docstring", ""),
                                        )
                                    ).lower(),
                                ),
                            )
                        for imp in result.imports:
                            db.execute(
                                "INSERT INTO imports VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (
                                    imp["id"],
                                    imp["file_id"],
                                    imp["repo_id"],
                                    imp["line"],
                                    imp["end_line"],
                                    imp["specifier"],
                                    imp["statement"],
                                    imp["kind"],
                                    int(imp["type_only"]),
                                    None,
                                    "unresolved",
                                    _json(imp["names"]),
                                    imp.get("relative_level", 0),
                                    None,
                                ),
                            )
                self._resolve_imports(db)
                db.execute("INSERT OR REPLACE INTO metadata VALUES('revision',?)", (str(revision),))
                db.execute(
                    "INSERT OR REPLACE INTO metadata VALUES('fingerprint',?)",
                    (snapshot["fingerprint"],),
                )
                db.execute("INSERT OR REPLACE INTO metadata VALUES('indexed_at',?)", (now,))
                db.execute("INSERT OR REPLACE INTO metadata VALUES('index_error','')")
                db.execute(
                    """
                    UPDATE jobs
                    SET state='cancelled', question='', claims='[]', citations='[]',
                        uncertainty='[]', reason='Corpus changed', contexts='[]', completed_at=?
                    WHERE revision <> ?
                    """,
                    (now, revision),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
            return {"changed": True, "revision": revision, "counts": self.counts(db)}

    def _resolve_imports(self, db: sqlite3.Connection) -> None:
        files = [dict(row) for row in db.execute("SELECT * FROM files WHERE parse_status='ok'")]
        by_repo_path = {(f["repo_id"], f["path"]): f["id"] for f in files}
        modules: dict[tuple[str, str], str] = {}
        for file in files:
            if file["language"] == "python":
                modules[(file["repo_id"], python_module(file["path"], file["prefix"]))] = file["id"]
        for row in db.execute("SELECT * FROM imports").fetchall():
            imp = dict(row)
            source = next(f for f in files if f["id"] == imp["file_id"])
            target = None
            status = "external"
            if source["language"] == "python":
                target = self._resolve_python(imp, source, modules)
                if target is not None:
                    status = "resolved"
                elif imp["relative_level"]:
                    status = "outside_sample"
            elif imp["specifier"].startswith("."):
                target = self._resolve_ts(source, imp["specifier"], by_repo_path)
                status = "resolved" if target else "outside_sample"
            if imp["kind"] == "dynamic":
                status = "dynamic"
            db.execute(
                "UPDATE imports SET target_file_id=?,status=? WHERE id=?",
                (target, status, imp["id"]),
            )

    @staticmethod
    def _resolve_python(
        imp: dict[str, Any], source: dict[str, Any], modules: dict[tuple[str, str], str]
    ) -> str | None:
        spec = imp["specifier"]
        if imp["relative_level"]:
            current = python_module(source["path"], source["prefix"])
            is_package = source["path"].endswith("/__init__.py")
            package = current if is_package else current.rpartition(".")[0]
            parts = package.split(".") if package else []
            climb = imp["relative_level"] - 1
            base = parts[: len(parts) - climb] if climb <= len(parts) else []
            module = spec.lstrip(".")
            candidate = ".".join([*base, *([module] if module else [])])
            for name in _loads(imp["names"], []):
                child = ".".join([candidate, name.split(" as ", 1)[0]])
                if (source["repo_id"], child) in modules:
                    return modules[(source["repo_id"], child)]
            return modules.get((source["repo_id"], candidate))
        for name in _loads(imp["names"], []):
            child = f"{spec}.{name.split(' as ', 1)[0]}"
            if (source["repo_id"], child) in modules:
                return modules[(source["repo_id"], child)]
        return modules.get((source["repo_id"], spec))

    @staticmethod
    def _resolve_ts(
        source: dict[str, Any], specifier: str, paths: dict[tuple[str, str], str]
    ) -> str | None:
        base = PurePosixPath(source["path"]).parent
        raw = str(base.joinpath(specifier))
        normalized = str(PurePosixPath(os.path.normpath(raw)))
        candidates = [normalized]
        if normalized.endswith(".js"):
            candidates.append(normalized[:-3] + ".ts")
        if not PurePosixPath(normalized).suffix:
            candidates += [normalized + ".ts", normalized + "/index.ts"]
        for path in candidates:
            target = paths.get((source["repo_id"], path))
            if target is not None:
                return target
        return None

    def counts(self, db: sqlite3.Connection | None = None) -> dict[str, int]:
        own = db is None
        db = db or self.connect()
        try:
            result = {}
            for key, sql in {
                "files": "SELECT count(*) FROM files",
                "symbols": "SELECT count(*) FROM symbols",
                "imports": "SELECT count(*) FROM imports",
                "resolved_imports": "SELECT count(*) FROM imports WHERE status='resolved'",
                "parse_errors": "SELECT count(*) FROM files WHERE parse_status='error'",
            }.items():
                result[key] = db.execute(sql).fetchone()[0]
            return result
        finally:
            if own:
                db.close()

    def row(self, table: str, identifier: str) -> dict[str, Any] | None:
        if table not in {"files", "symbols", "imports", "jobs"}:
            raise ValueError("invalid table")
        with self.connect() as db:
            result = db.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
        return decode_row(table, result) if result else None

    def rows(
        self, table: str, where: str = "", params: tuple[Any, ...] = (), order: str = ""
    ) -> list[dict[str, Any]]:
        if table not in {"files", "symbols", "imports", "repositories", "jobs"}:
            raise ValueError("invalid table")
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        if order:
            sql += f" ORDER BY {order}"
        with self.connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [decode_row(table, row) for row in rows]


def decode_row(table: str, row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    if table == "files":
        item.pop("source", None)
        item.pop("prefix", None)
        item["diagnostics"] = _loads(item["diagnostics"], [])
        item["symbols"] = item.get("symbols", 0)
    elif table == "symbols":
        item["exported"] = bool(item["exported"])
        item.pop("search_text", None)
        if item.get("docstring") is None:
            item.pop("docstring", None)
    elif table == "imports":
        item["type_only"] = bool(item["type_only"])
        item["names"] = _loads(item["names"], [])
        item.pop("relative_level", None)
        item.pop("resolution_note", None)
    elif table == "jobs":
        for key in ("claims", "citations", "uncertainty", "usage", "contexts"):
            item[key] = _loads(item.get(key), {} if key == "usage" else [])
        for key in ("completed_at", "reason", "model", "usage", "latency_ms"):
            if item.get(key) is None:
                item.pop(key, None)
        item.pop("contexts", None)
    return item


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repositories(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, tag TEXT NOT NULL,
    commit_sha TEXT NOT NULL, license TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files(
    id TEXT PRIMARY KEY, repo_id TEXT NOT NULL, path TEXT NOT NULL,
    language TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
    lines INTEGER NOT NULL, parse_status TEXT NOT NULL, diagnostics TEXT NOT NULL,
    url TEXT NOT NULL, source TEXT NOT NULL, prefix TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS symbols(
    id TEXT PRIMARY KEY, file_id TEXT NOT NULL, repo_id TEXT NOT NULL,
    name TEXT NOT NULL, qualname TEXT NOT NULL, kind TEXT NOT NULL,
    start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, signature TEXT NOT NULL,
    docstring TEXT, exported INTEGER NOT NULL, search_text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imports(
    id TEXT PRIMARY KEY, file_id TEXT NOT NULL, repo_id TEXT NOT NULL,
    line INTEGER NOT NULL, end_line INTEGER NOT NULL, specifier TEXT NOT NULL,
    statement TEXT NOT NULL, kind TEXT NOT NULL, type_only INTEGER NOT NULL,
    target_file_id TEXT, status TEXT NOT NULL, names TEXT NOT NULL,
    relative_level INTEGER NOT NULL, resolution_note TEXT
);
CREATE TABLE IF NOT EXISTS jobs(
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
    question TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL,
    completed_at TEXT, claims TEXT NOT NULL, citations TEXT NOT NULL,
    uncertainty TEXT NOT NULL, reason TEXT, model TEXT, usage TEXT,
    latency_ms INTEGER, contexts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS symbol_name ON symbols(name,qualname);
CREATE INDEX IF NOT EXISTS import_target ON imports(target_file_id);
"""
