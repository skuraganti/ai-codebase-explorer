"""Static Python and TypeScript parsing. Analyzed source is never imported or executed."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class ParseResult:
    symbols: list[dict[str, Any]]
    imports: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _symbol(
    file_id: str,
    repo_id: str,
    name: str,
    qualname: str,
    kind: str,
    start: int,
    end: int,
    signature: str,
    exported: bool,
    docstring: str | None = None,
) -> dict[str, Any]:
    result = {
        "id": _digest(f"{file_id}:{qualname}:{kind}:{start}"),
        "file_id": file_id,
        "repo_id": repo_id,
        "name": name,
        "qualname": qualname,
        "kind": kind,
        "start_line": start,
        "end_line": end,
        "signature": signature,
        "exported": exported,
    }
    if docstring is not None:
        result["docstring"] = docstring
    return result


def _import(
    file_id: str,
    repo_id: str,
    line: int,
    end_line: int,
    specifier: str,
    statement: str,
    kind: str = "import",
    type_only: bool = False,
    names: list[str] | None = None,
    relative_level: int = 0,
) -> dict[str, Any]:
    return {
        "id": _digest(f"{file_id}:{line}:{end_line}:{specifier}:{kind}:{statement}:{names}"),
        "file_id": file_id,
        "repo_id": repo_id,
        "line": line,
        "end_line": end_line,
        "specifier": specifier,
        "statement": statement,
        "kind": kind,
        "type_only": type_only,
        "target_file_id": None,
        "status": "unresolved",
        "names": names or [],
        "relative_level": relative_level,
    }


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, source: str, file_id: str, repo_id: str) -> None:
        self.source = source
        self.file_id = file_id
        self.repo_id = repo_id
        self.symbols: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self.scope: list[str] = []
        self.scope_kinds: list[str] = []
        self.type_checking = 0
        self.typing_aliases: set[str] = {"typing"}
        self.type_checking_names = {"TYPE_CHECKING"}

    def _add_definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        qualname = ".".join([*self.scope, node.name])
        if isinstance(node, ast.ClassDef):
            kind = "class"
        elif self.scope and self.scope_kinds[-1] == "class":
            kind = "async_method" if isinstance(node, ast.AsyncFunctionDef) else "method"
        else:
            kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
        segment = ast.get_source_segment(self.source, node) or ""
        body = node.body[0] if node.body else node
        header_end = getattr(body, "lineno", node.lineno)
        signature = "\n".join(segment.splitlines()[: max(1, header_end - node.lineno)]).strip()
        self.symbols.append(
            _symbol(
                self.file_id,
                self.repo_id,
                node.name,
                qualname,
                kind,
                node.lineno,
                node.end_lineno or node.lineno,
                signature,
                not self.scope and not node.name.startswith("_"),
                ast.get_docstring(node, clean=False),
            )
        )
        self.scope.append(node.name)
        self.scope_kinds.append("class" if isinstance(node, ast.ClassDef) else "function")
        self.generic_visit(node)
        self.scope_kinds.pop()
        self.scope.pop()

    visit_FunctionDef = _add_definition
    visit_AsyncFunctionDef = _add_definition
    visit_ClassDef = _add_definition

    def visit_If(self, node: ast.If) -> None:
        is_tc = isinstance(node.test, ast.Name) and node.test.id in self.type_checking_names
        if not is_tc and isinstance(node.test, ast.Attribute):
            is_tc = (
                node.test.attr == "TYPE_CHECKING"
                and isinstance(node.test.value, ast.Name)
                and node.test.value.id in self.typing_aliases
            )
        self.type_checking += is_tc
        for item in node.body:
            self.visit(item)
        self.type_checking -= is_tc
        for item in node.orelse:
            self.visit(item)

    def visit_Import(self, node: ast.Import) -> None:
        statement = ast.get_source_segment(self.source, node) or ""
        for alias in node.names:
            if alias.name == "typing" and alias.asname:
                self.typing_aliases.add(alias.asname)
            self.imports.append(
                _import(
                    self.file_id,
                    self.repo_id,
                    node.lineno,
                    node.end_lineno or node.lineno,
                    alias.name,
                    statement,
                    type_only=bool(self.type_checking),
                    names=[alias.asname or alias.name],
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        statement = ast.get_source_segment(self.source, node) or ""
        specifier = "." * node.level + (node.module or "")
        for alias in node.names:
            if node.module == "typing" and alias.name == "TYPE_CHECKING":
                self.type_checking_names.add(alias.asname or alias.name)
            self.imports.append(
                _import(
                    self.file_id,
                    self.repo_id,
                    node.lineno,
                    node.end_lineno or node.lineno,
                    specifier,
                    statement,
                    type_only=bool(self.type_checking),
                    names=[
                        alias.name if alias.asname is None else f"{alias.name} as {alias.asname}"
                    ],
                    relative_level=node.level,
                )
            )

    def visit_Assign(self, node: ast.Assign) -> None:
        if not self.scope_kinds or self.scope_kinds[-1] == "class":
            for target in node.targets:
                for name in _assignment_names(target):
                    self.symbols.append(
                        _symbol(
                            self.file_id,
                            self.repo_id,
                            name,
                            ".".join([*self.scope, name]),
                            "binding",
                            node.lineno,
                            node.end_lineno or node.lineno,
                            name,
                            not name.startswith("_"),
                        )
                    )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if not self.scope_kinds or self.scope_kinds[-1] == "class":
            for name in _assignment_names(node.target):
                signature = ast.get_source_segment(self.source, node.annotation) or name
                self.symbols.append(
                    _symbol(
                        self.file_id,
                        self.repo_id,
                        name,
                        ".".join([*self.scope, name]),
                        "binding",
                        node.lineno,
                        node.end_lineno or node.lineno,
                        f"{name}: {signature}",
                        not name.startswith("_"),
                    )
                )
        self.generic_visit(node)


def _assignment_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [name for item in node.elts for name in _assignment_names(item)]
    return []


def parse_python(source: str, file_id: str, repo_id: str) -> ParseResult:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ParseResult([], [], [{"line": exc.lineno or 1, "message": exc.msg}])
    visitor = _PythonVisitor(source, file_id, repo_id)
    visitor.visit(tree)
    return ParseResult(visitor.symbols, visitor.imports, [])


def parse_typescript(source: str, file_id: str, repo_id: str, *, tsx: bool = False) -> ParseResult:
    """Call one fixed trusted parser with credential-free env and bounded JSON stdin.

    The compiler API only creates an in-memory AST. It never resolves/imports,
    emits, evaluates, builds or executes supplied source.
    """
    try:
        process = subprocess.run(
            [
                "node",
                "--no-addons",
                "--max-old-space-size=160",
                str(Path(__file__).with_name("ts_parser.cjs")),
            ],
            input=json.dumps({"source": source, "tsx": tsx}, ensure_ascii=False).encode(),
            capture_output=True,
            timeout=8,
            env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "LANG": "C.UTF-8"},
        )
        if len(process.stdout) > 4 * 1024 * 1024 or process.returncode:
            raise ValueError("parser process failed or exceeded output bound")
        value = json.loads(process.stdout)
        symbols = [
            _symbol(
                file_id,
                repo_id,
                s["name"],
                s["qualname"],
                s["kind"],
                s["start_line"],
                s["end_line"],
                s["signature"],
                s["exported"],
            )
            for s in value["symbols"]
        ]
        imports = [
            _import(
                file_id,
                repo_id,
                i["line"],
                i["end_line"],
                i["specifier"],
                i["statement"],
                i["kind"],
                i["type_only"],
                i["names"],
            )
            for i in value["imports"]
        ]
        return ParseResult(symbols, imports, value["diagnostics"])
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return ParseResult(
            [], [], [{"line": 1, "message": "TypeScript parser unavailable or exceeded a bound"}]
        )


def parse_source(source: str, language: str, file_id: str, repo_id: str) -> ParseResult:
    if language == "python":
        return parse_python(source, file_id, repo_id)
    if language == "typescript":
        return parse_typescript(source, file_id, repo_id)
    raise ValueError(f"unsupported language: {language}")


def python_module(path: str, prefix: str) -> str:
    prefix_path = PurePosixPath(prefix.rstrip("/"))
    relative = PurePosixPath(path).relative_to(prefix_path)
    parts = [prefix_path.name, *relative.parts]
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)
