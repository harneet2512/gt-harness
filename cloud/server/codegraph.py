"""File-relation graph for a session workspace.

Two independent sources of edges:

* ``import`` — file-level static imports parsed out of the source itself
  (Python, JavaScript/TypeScript, Go, Rust). Deliberately conservative: a
  specifier only becomes an edge when it resolves to a file that is actually
  in the tree, so nothing here invents a node.
* ``gt_call`` / ``gt_ref`` / ``gt_import`` — symbol-level edges from the GT
  indexer's SQLite graph, collapsed to file level. Fail-open by design: any
  problem reading that database drops the GT edges and reports ``gt: false``.

Nothing in this module knows about sessions, HTTP or the store; it is handed a
workspace path and the file list ``workspace.list_tree`` produced.
"""
from __future__ import annotations

import ast
import os
import posixpath
import re
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: files larger than this are never opened for import parsing
MAX_FILE_BYTES = 1_000_000
#: a NUL in this many leading bytes means "binary", the way git decides it
BINARY_SNIFF_BYTES = 8192
#: hard ceiling on graph size; beyond it only the busiest files survive
MAX_NODES = 5000

_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_JS_SOURCE = frozenset(_JS_EXTS)

#: ``from 'x'`` / ``import 'x'`` / ``require('x')`` — covers ``export … from``
_JS_SPEC_RE = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*|\brequire\s*\(\s*)(['"])([^'"\n]+)\1"""
)
_GO_MODULE_RE = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)
_GO_SINGLE_RE = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"\n]+)"', re.MULTILINE)
_GO_BLOCK_RE = re.compile(r"^\s*import\s*\(([^)]*)\)", re.MULTILINE | re.DOTALL)
_GO_QUOTED_RE = re.compile(r'"([^"\n]+)"')
_RS_MOD_RE = re.compile(
    r"^\s*(?:pub(?:\s*\([^)]*\))?\s+)?mod\s+([A-Za-z_]\w*)\s*;", re.MULTILINE
)
_RS_USE_RE = re.compile(
    r"^\s*(?:pub(?:\s*\([^)]*\))?\s+)?use\s+crate::([A-Za-z_][\w:]*)", re.MULTILINE
)


@dataclass(frozen=True)
class _Repo:
    """Everything a language finder needs to resolve a specifier to a file."""

    workspace: str
    paths: frozenset[str]
    go_module: str
    go_dirs: dict[str, tuple[str, ...]]


# -- public API ---------------------------------------------------------------


def build_graph(
    workspace: str, files: list[dict], graph_db: str | None = None
) -> dict[str, Any]:
    """Nodes for every file in ``files``, plus import and (optional) GT edges."""
    edges = build_import_graph(workspace, files)
    gt = False
    if graph_db:
        gt_edges, gt = build_gt_edges(graph_db, workspace)
        if gt:
            known = {f["path"] for f in files}
            edges.extend(
                e for e in gt_edges if e["source"] in known and e["target"] in known
            )
    nodes = [_node(entry) for entry in files]
    if len(nodes) <= MAX_NODES:
        return {"gt": gt, "nodes": nodes, "edges": edges}
    nodes, edges = _cap(nodes, edges)
    return {"gt": gt, "nodes": nodes, "edges": edges, "truncated": True}


def build_import_graph(workspace: str, files: list[dict]) -> list[dict]:
    """Static import edges between files of the tree, deduped into weights."""
    repo = _repo(workspace, files)
    counts: Counter[tuple[str, str]] = Counter()
    for entry in files:
        path = str(entry["path"])
        finder = _FINDERS.get(posixpath.splitext(path)[1])
        if finder is None:
            continue
        text = _read_text(workspace, path)
        if text is None:
            continue
        for target in finder(path, text, repo):
            if target != path:
                counts[(path, target)] += 1
    return [
        {"source": source, "target": target, "kind": "import", "weight": weight}
        for (source, target), weight in sorted(counts.items())
    ]


def build_gt_edges(graph_db: str, workspace: str) -> tuple[list[dict], bool]:
    """GT symbol edges collapsed to file level, or ``([], False)`` on any fault."""
    try:
        return _read_gt_edges(graph_db, workspace), True
    except Exception:  # noqa: BLE001 - GT is an enrichment, never a failure mode
        return [], False


# -- nodes / truncation -------------------------------------------------------


def _node(entry: dict) -> dict:
    path = str(entry["path"])
    extension = posixpath.splitext(posixpath.basename(path))[1]
    head, _, tail = path.partition("/")
    return {
        "id": path,
        "path": path,
        "size": int(entry.get("size", 0) or 0),
        "lang": extension[1:] if extension else "",
        "dir": head if tail else "",
    }


def _cap(nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep the ``MAX_NODES`` busiest files and the edges wholly inside them."""
    degree: Counter[str] = Counter()
    for edge in edges:
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1
    ranked = sorted(nodes, key=lambda n: (-degree[n["path"]], n["path"]))
    kept = ranked[:MAX_NODES]
    keep = {node["path"] for node in kept}
    kept.sort(key=lambda n: n["path"])
    return kept, [
        e for e in edges if e["source"] in keep and e["target"] in keep
    ]


# -- file access --------------------------------------------------------------


def _read_text(workspace: str, path: str) -> str | None:
    """Contents of a tree file, or ``None`` if too large, binary or unreadable."""
    full = os.path.join(workspace, *path.split("/"))
    try:
        if os.path.getsize(full) > MAX_FILE_BYTES:
            return None
        with open(full, "rb") as handle:
            head = handle.read(BINARY_SNIFF_BYTES)
            if b"\0" in head:
                return None
            body = head + handle.read()
    except OSError:
        return None
    return body.decode("utf-8", errors="replace")


def _repo(workspace: str, files: list[dict]) -> _Repo:
    paths = frozenset(str(f["path"]) for f in files)
    go_dirs: dict[str, list[str]] = {}
    for path in paths:
        if path.endswith(".go"):
            go_dirs.setdefault(posixpath.dirname(path), []).append(path)
    module = ""
    if "go.mod" in paths:
        match = _GO_MODULE_RE.search(_read_text(workspace, "go.mod") or "")
        module = match.group(1) if match else ""
    return _Repo(
        workspace=workspace,
        paths=paths,
        go_module=module,
        go_dirs={d: tuple(sorted(v)) for d, v in go_dirs.items()},
    )


def _first_hit(repo: _Repo, candidates: Iterator[str] | list[str]) -> str | None:
    for candidate in candidates:
        normalised = posixpath.normpath(candidate).replace("\\", "/")
        if normalised in repo.paths:
            return normalised
    return None


# -- Python -------------------------------------------------------------------


def _python_targets(path: str, text: str, repo: _Repo) -> Iterator[str]:
    try:
        module = ast.parse(text)
    except (SyntaxError, ValueError):
        return
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                hit = _py_module(alias.name, path, repo, 0)
                if hit:
                    yield hit
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            hit = _py_module(base, path, repo, node.level)
            if hit:
                yield hit
            for alias in node.names:
                dotted = f"{base}.{alias.name}" if base else alias.name
                hit = _py_module(dotted, path, repo, node.level)
                if hit:
                    yield hit


def _py_module(dotted: str, path: str, repo: _Repo, level: int) -> str | None:
    """Resolve a dotted module name to a file of the tree, or ``None``."""
    if dotted == "*":
        return None
    if not dotted:
        # ``from . import x`` — the dot itself is the importer's own package
        root = _py_roots(path, level)[0] if level else ""
        return _first_hit(repo, [f"{root}__init__.py"]) if level else None
    relative = dotted.replace(".", "/")
    for root in _py_roots(path, level):
        hit = _first_hit(repo, [f"{root}{relative}.py", f"{root}{relative}/__init__.py"])
        if hit:
            return hit
    return None


def _py_roots(path: str, level: int) -> list[str]:
    """Source roots to try: the importer's package for a relative import, else
    the repo root, ``src/`` and the importer's own top-level directory."""
    if level:
        directory = posixpath.dirname(path)
        for _ in range(level - 1):
            directory = posixpath.dirname(directory)
        return [f"{directory}/" if directory else ""]
    roots = ["", "src/"]
    head, _, tail = path.partition("/")
    if tail:
        roots.append(f"{head}/")
    return roots


# -- JavaScript / TypeScript --------------------------------------------------


def _js_targets(path: str, text: str, repo: _Repo) -> Iterator[str]:
    directory = posixpath.dirname(path)
    for match in _JS_SPEC_RE.finditer(text):
        specifier = match.group(2)
        if not specifier.startswith("."):  # bare package specifier
            continue
        base = posixpath.normpath(posixpath.join(directory, specifier))
        hit = _first_hit(repo, _js_candidates(base))
        if hit:
            yield hit


def _js_candidates(base: str) -> list[str]:
    candidates = [base] if posixpath.splitext(base)[1] in _JS_SOURCE else []
    candidates += [f"{base}{ext}" for ext in _JS_EXTS]
    candidates += [f"{base}/index{ext}" for ext in _JS_EXTS]
    return candidates


# -- Go -----------------------------------------------------------------------


def _go_targets(path: str, text: str, repo: _Repo) -> Iterator[str]:
    if not repo.go_module:
        return
    for specifier in _go_specifiers(text):
        if specifier == repo.go_module:
            package = ""
        elif specifier.startswith(f"{repo.go_module}/"):
            package = specifier[len(repo.go_module) + 1:]
        else:
            continue  # a third-party or stdlib package
        yield from repo.go_dirs.get(package, ())


def _go_specifiers(text: str) -> Iterator[str]:
    for match in _GO_SINGLE_RE.finditer(text):
        yield match.group(1)
    for block in _GO_BLOCK_RE.finditer(text):
        for match in _GO_QUOTED_RE.finditer(block.group(1)):
            yield match.group(1)


# -- Rust ---------------------------------------------------------------------


def _rust_targets(path: str, text: str, repo: _Repo) -> Iterator[str]:
    directory = posixpath.dirname(path)
    for match in _RS_MOD_RE.finditer(text):
        name = match.group(1)
        hit = _first_hit(
            repo,
            [
                posixpath.join(directory, f"{name}.rs"),
                posixpath.join(directory, name, "mod.rs"),
            ],
        )
        if hit:
            yield hit
    for match in _RS_USE_RE.finditer(text):
        hit = _rust_crate_path(match.group(1), repo)
        if hit:
            yield hit


def _rust_crate_path(dotted: str, repo: _Repo) -> str | None:
    """``crate::a::b::Thing`` — try the longest module prefix that is a file."""
    segments = [s for s in dotted.split("::") if s]
    while segments:
        stem = "src/" + "/".join(segments)
        hit = _first_hit(repo, [f"{stem}.rs", f"{stem}/mod.rs"])
        if hit:
            return hit
        segments.pop()
    return None


_FINDERS = {
    ".py": _python_targets,
    ".pyi": _python_targets,
    ".go": _go_targets,
    ".rs": _rust_targets,
    **{ext: _js_targets for ext in _JS_EXTS},
}


# -- GT graph database --------------------------------------------------------

#: ``edges.type`` (the producer's DDL is vendor/gt-index-src/internal/store/
#: sqlite.go:231; the literals are written across cmd/gt-index and
#: internal/resolver) mapped onto the three GT edge kinds of this API.
#: ``CONTAINS`` is deliberately absent: it is structural nesting (file ->
#: symbol, class -> method), which only ever collapses into a self-edge.
_GT_EDGE_KINDS = {
    "CALLS": "gt_call",
    "API_CALL": "gt_call",
    "IMPORTS": "gt_import",
    "RE_EXPORTS": "gt_import",
    "EXTENDS": "gt_ref",
    "IMPLEMENTS": "gt_ref",
    "COMPOSES": "gt_ref",
    "READS": "gt_ref",
    "WRITES": "gt_ref",
    "DATA_FLOW": "gt_ref",
    "HANDLES_ROUTE": "gt_ref",
    "CO_SERIALIZES": "gt_ref",
    "PRECEDES": "gt_ref",
}
#: defensive ceiling on how many file pairs a single graph may contribute
MAX_GT_GROUPS = 200_000

#: symbol edges collapsed to the file pair they connect. ``nodes.file_path`` is
#: repo-root-relative and slash-normalised by the producer (walker.go:99), but
#: the reader normalises again rather than trusting it.
_GT_QUERY = (
    "SELECT src.file_path, tgt.file_path, e.type, COUNT(*) "
    "FROM edges e "
    "JOIN nodes src ON src.id = e.source_id "
    "JOIN nodes tgt ON tgt.id = e.target_id "
    "WHERE e.type IN ({placeholders}) "
    "GROUP BY src.file_path, tgt.file_path, e.type "
    f"LIMIT {MAX_GT_GROUPS}"
)


def _read_gt_edges(graph_db: str, workspace: str) -> list[dict]:
    query = _GT_QUERY.format(placeholders=",".join("?" * len(_GT_EDGE_KINDS)))
    uri = f"file:{Path(graph_db).resolve().as_posix()}?mode=ro"
    prefix = Path(workspace).resolve().as_posix().rstrip("/")
    counts: Counter[tuple[str, str, str]] = Counter()
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        rows = connection.execute(query, tuple(_GT_EDGE_KINDS)).fetchall()
    for raw_source, raw_target, edge_type, total in rows:
        source = _gt_path(raw_source, prefix)
        target = _gt_path(raw_target, prefix)
        if source and target and source != target:
            counts[(source, target, _GT_EDGE_KINDS[edge_type])] += int(total)
    return [
        {"source": source, "target": target, "kind": kind, "weight": weight}
        for (source, target, kind), weight in sorted(counts.items())
    ]


def _gt_path(raw: Any, prefix: str) -> str:
    """A ``nodes.file_path`` as a tree-relative path, or "" if unusable."""
    path = str(raw or "").strip().replace("\\", "/")
    if prefix and path.casefold().startswith(f"{prefix.casefold()}/"):
        path = path[len(prefix) + 1:]
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")
