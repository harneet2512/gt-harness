"""Auto-detect a code repository and build the gateway's graph.db.

Uses ``groundtruth._binary.run_index`` (the Go gt-index binary) - NEVER the
``groundtruth index`` CLI, which builds the MCP SymbolStore index.db, a
DIFFERENT database the gateway cannot read.

Binary resolution is find_binary()'s: $GT_INDEX_BINARY -> PATH -> local build
-> release download. Because find_binary's "local build" probe is cwd-relative,
this module additionally seeds $GT_INDEX_BINARY from a known local build when
one exists and nothing else resolves.

No source files under the root -> return None: GT stays dormant for non-code
tasks (no harm, no noise).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from contextlib import redirect_stderr
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from gt_engine.language_registry import (
    INDEXABLE_SOURCE_SUFFIXES,
    LanguageCapability,
    LanguageResolution,
    LanguageResolutionStatus,
    candidate_capabilities,
    is_indexable_source,
    resolve_language,
)

# Extensions gt-index parses (tree-sitter structural coverage). A root with at
# least one of these is a code repository worth indexing.
SOURCE_EXTS = INDEXABLE_SOURCE_SUFFIXES

# Never descend into these (vendored/build/VCS trees are not the task's code).
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".gt",
        ".groundtruth",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".idea",
        ".vscode",
        "target",
        "vendor",
    }
)

# Known local gt-index builds probed only when nothing else resolves.
_LOCAL_BINARY_CANDIDATES = (
    str(Path(__file__).resolve().parents[1] / "vendor" / "gt-index-src" / "gt-index.exe"),
    r"D:\Groundtruth\gt-index\gt-index.exe",
    "/opt/groundtruth/gt-index/gt-index",
)

_MAX_SCAN_FILES = 50_000  # detection bound; a hit returns immediately


class IndexBuildStatus(StrEnum):
    """Replayable reason why repository indexing did or did not run."""

    AVAILABLE = "available"
    NO_SUPPORTED_SOURCE = "no_supported_source"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    INCOMPLETE_COVERAGE = "incomplete_source_coverage"
    MISSING_RUNTIME = "missing_runtime"
    MISSING_BINARY = "missing_binary"
    BUILD_FAILED = "build_failed"
    INVALID_DATABASE = "invalid_database"


@dataclass(frozen=True, slots=True)
class IndexBuildReceipt:
    status: IndexBuildStatus
    graph_db: str | None = None
    graph_revision: str = ""
    binary_sha256: str = ""
    elapsed_ms: float = 0.0
    error_type: str | None = None
    error_diagnostic: str = ""
    source_files: int = 0
    indexable_files: int = 0
    unsupported_suffixes: tuple[str, ...] = ()
    unsupported_paths: tuple[str, ...] = ()
    ambiguous_paths: tuple[str, ...] = ()
    language_file_counts: tuple[tuple[str, int], ...] = ()
    resolution_reason_counts: tuple[tuple[str, int], ...] = ()
    parser_failures: int = 0
    schema_valid: bool = False
    node_count: int = 0
    edge_count: int = 0
    fts_tables: tuple[str, ...] = ()
    source_revision: str = ""

    @property
    def available(self) -> bool:
        return self.status is IndexBuildStatus.AVAILABLE and bool(self.graph_db)

    @property
    def coverage_complete(self) -> bool:
        return (
            self.source_files == self.indexable_files
            and not self.unsupported_suffixes
            and not self.unsupported_paths
            and not self.ambiguous_paths
            and self.parser_failures == 0
        )


@dataclass(frozen=True, slots=True)
class SourceCoverage:
    status: IndexBuildStatus
    source_files: int
    indexable_files: int
    unsupported_suffixes: tuple[str, ...] = ()
    unsupported_paths: tuple[str, ...] = ()
    ambiguous_paths: tuple[str, ...] = ()
    language_file_counts: tuple[tuple[str, int], ...] = ()
    resolution_reason_counts: tuple[tuple[str, int], ...] = ()


def _read_language_prefix(path: str | os.PathLike[str], limit: int = 65_536) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read(limit)
    except OSError:
        return b""


def _resolve_file_language(
    path: str | os.PathLike[str],
) -> tuple[LanguageResolution, tuple[LanguageCapability, ...]]:
    """Resolve a path without reading files whose identity is already final."""

    candidates = candidate_capabilities(path)
    suffix = os.path.splitext(os.fspath(path))[1]
    needs_content = len(candidates) > 1 or (not candidates and not suffix)
    prefix = _read_language_prefix(path) if needs_content else b""
    return resolve_language(path, prefix), candidates


def inspect_source_coverage(root: str | os.PathLike[str]) -> SourceCoverage:
    """Inspect structural source coverage without invoking the index binary."""

    source_files = 0
    indexable_files = 0
    unsupported: set[str] = set()
    unsupported_paths: set[str] = set()
    ambiguous_paths: set[str] = set()
    languages: dict[str, int] = {}
    resolution_reasons: dict[str, int] = {}
    seen = 0
    try:
        root_text = os.fspath(root)
        for dirpath, dirnames, filenames in os.walk(root_text):
            dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
            for filename in filenames:
                seen += 1
                absolute = os.path.join(dirpath, filename)
                relative = os.path.relpath(absolute, root_text).replace("\\", "/")
                resolution, candidates = _resolve_file_language(absolute)
                resolution_reasons[resolution.reason_code] = (
                    resolution_reasons.get(resolution.reason_code, 0) + 1
                )
                required_candidates = tuple(
                    capability
                    for capability in candidates
                    if capability.validation_relevant and capability.structural_required
                )
                if resolution.status is LanguageResolutionStatus.RESOLVED:
                    capability = resolution.capability
                    if capability is not None and capability.structural_required:
                        source_files += 1
                        languages[capability.name] = languages.get(capability.name, 0) + 1
                        if capability.structural_index:
                            indexable_files += 1
                        else:
                            unsupported.add(os.path.splitext(filename)[1].lower())
                            unsupported_paths.add(relative)
                elif (
                    resolution.status is LanguageResolutionStatus.AMBIGUOUS
                    and required_candidates
                ):
                    source_files += 1
                    suffix = os.path.splitext(filename)[1].lower()
                    unsupported.add(suffix or "<no-extension>")
                    ambiguous_paths.add(relative)
                if seen >= _MAX_SCAN_FILES:
                    break
            if seen >= _MAX_SCAN_FILES:
                break
    except OSError:
        return SourceCoverage(IndexBuildStatus.BUILD_FAILED, 0, 0)
    if source_files == 0:
        status = IndexBuildStatus.NO_SUPPORTED_SOURCE
    elif indexable_files == 0:
        status = IndexBuildStatus.UNSUPPORTED_LANGUAGE
    elif unsupported:
        status = IndexBuildStatus.INCOMPLETE_COVERAGE
    else:
        status = IndexBuildStatus.AVAILABLE
    return SourceCoverage(
        status,
        source_files,
        indexable_files,
        tuple(sorted(unsupported)),
        tuple(sorted(unsupported_paths)),
        tuple(sorted(ambiguous_paths)),
        tuple(sorted(languages.items())),
        tuple(sorted(resolution_reasons.items())),
    )


def is_code_repo(root: str) -> bool:
    """True iff ``root`` contains at least one source file (bounded scan)."""
    seen = 0
    try:
        for _dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                seen += 1
                path = os.path.join(_dirpath, fn)
                resolution, candidates = _resolve_file_language(path)
                if (
                    resolution.capability is not None
                    and resolution.capability.validation_relevant
                    and resolution.capability.structural_required
                ) or any(
                    capability.validation_relevant and capability.structural_required
                    for capability in candidates
                ):
                    return True
                if seen >= _MAX_SCAN_FILES:
                    return False
    except OSError:
        return False
    return False


def _seed_binary_env() -> None:
    """Make find_binary() succeed offline when a known local build exists."""
    if os.environ.get("GT_INDEX_BINARY") or shutil.which("gt-index"):
        return
    for cand in _LOCAL_BINARY_CANDIDATES:
        if Path(cand).exists():
            os.environ["GT_INDEX_BINARY"] = cand
            return


def _binary_certification() -> dict[str, str]:
    candidate = _resolved_binary_path()
    path = Path(candidate).resolve() if candidate else None
    if path is None or not path.is_file():
        return {"path_sha256": "", "binary_sha256": ""}
    return {
        "path_sha256": hashlib.sha256(str(path).encode("utf-8")).hexdigest(),
        "binary_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _resolved_binary_path() -> str:
    candidate = os.environ.get("GT_INDEX_BINARY") or shutil.which("gt-index") or ""
    if not candidate:
        try:
            from groundtruth._binary import CACHE_DIR, GT_INDEX_VERSION

            name = "gt-index.exe" if os.name == "nt" else "gt-index"
            cached = Path(CACHE_DIR) / GT_INDEX_VERSION / name
            candidate = str(cached) if cached.is_file() else ""
        except (ImportError, AttributeError):
            candidate = ""
    return candidate


def _atomic_write(path: Path, payload: bytes) -> None:
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _graph_schema_receipt(path: Path) -> tuple[bool, int, int, tuple[str, ...], str]:
    try:
        connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                )
            }
            required = {"nodes", "nodes_fts"}
            schema_valid = quick_check.lower() == "ok" and required <= tables
            node_count = (
                int(connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
                if "nodes" in tables
                else 0
            )
            edge_count = (
                int(connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
                if "edges" in tables
                else 0
            )
            fts_tables = tuple(sorted(name for name in tables if name.endswith("_fts")))
            detail = quick_check
            if quick_check.lower() == "ok" and not required <= tables:
                detail = "missing_tables:" + ",".join(sorted(required - tables))
            return schema_valid, node_count, edge_count, fts_tables, detail
        finally:
            connection.close()
    except (sqlite3.Error, OSError) as exc:
        return False, 0, 0, (), type(exc).__name__


def _graph_parser_failures(path: Path) -> int:
    try:
        connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT value FROM project_meta WHERE key='parse_failures'"
            ).fetchone()
            return max(0, int(row[0])) if row is not None else -1
        finally:
            connection.close()
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return -1


def ensure_index_with_receipt(
    root: str | os.PathLike[str] | None,
    *,
    state_dir: str | os.PathLike[str] | None = None,
    source_revision: str = "",
) -> IndexBuildReceipt:
    """Ensure a fresh graph.db exists and preserve the exact abstention reason.

    When ``GT_STATE_DIR`` is set, the db lives in a root-identity subdirectory
    there, completely outside the indexed/graded repository. The local default
    remains ``<root>/.gt/graph.db`` with a self-ignoring ``.gitignore``.
    Re-indexed on every call (a stale graph would violate correct-or-quiet;
    gt-index is fast). Never raises.
    """
    started = time.perf_counter()

    def receipt(
        status: IndexBuildStatus,
        *,
        graph_db: str | None = None,
        graph_revision: str = "",
        binary_sha256: str = "",
        error_type: str | None = None,
        error_diagnostic: str = "",
        coverage: SourceCoverage | None = None,
        schema_valid: bool = False,
        node_count: int = 0,
        edge_count: int = 0,
        fts_tables: tuple[str, ...] = (),
        parser_failures: int = 0,
    ) -> IndexBuildReceipt:
        observed = coverage or SourceCoverage(IndexBuildStatus.NO_SUPPORTED_SOURCE, 0, 0)
        return IndexBuildReceipt(
            status=status,
            graph_db=graph_db,
            graph_revision=graph_revision,
            binary_sha256=binary_sha256,
            elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
            error_type=error_type,
            error_diagnostic=" ".join(str(error_diagnostic).split())[:600],
            source_files=observed.source_files,
            indexable_files=observed.indexable_files,
            unsupported_suffixes=observed.unsupported_suffixes,
            unsupported_paths=observed.unsupported_paths,
            ambiguous_paths=observed.ambiguous_paths,
            language_file_counts=observed.language_file_counts,
            resolution_reason_counts=observed.resolution_reason_counts,
            parser_failures=parser_failures,
            schema_valid=schema_valid,
            node_count=node_count,
            edge_count=edge_count,
            fts_tables=fts_tables,
            source_revision=source_revision,
        )

    if not root or not os.path.isdir(root):
        return receipt(IndexBuildStatus.BUILD_FAILED, error_type="invalid_root")
    root_text = os.fspath(root)
    coverage = inspect_source_coverage(root_text)
    if coverage.status in {
        IndexBuildStatus.NO_SUPPORTED_SOURCE,
        IndexBuildStatus.UNSUPPORTED_LANGUAGE,
        IndexBuildStatus.BUILD_FAILED,
    }:
        return receipt(coverage.status, coverage=coverage)
    try:
        _seed_binary_env()
        try:
            from groundtruth._binary import run_index
        except (ImportError, ModuleNotFoundError, AttributeError) as exc:
            return receipt(
                IndexBuildStatus.MISSING_RUNTIME,
                error_type=type(exc).__name__,
                coverage=coverage,
            )

        binary = _binary_certification()
        if not binary["binary_sha256"]:
            return receipt(IndexBuildStatus.MISSING_BINARY, coverage=coverage)

        external = str(state_dir or os.environ.get("GT_STATE_DIR") or "").strip()
        if external:
            root_key = hashlib.sha256(
                os.path.realpath(root_text).encode("utf-8", "surrogatepass")
            ).hexdigest()[:16]
            gt_dir = Path(external) / root_key
            gt_dir.mkdir(parents=True, exist_ok=True)
        else:
            gt_dir = Path(root) / ".gt"
            gt_dir.mkdir(exist_ok=True)
            ignore = gt_dir / ".gitignore"
            if not ignore.exists():
                ignore.write_text("*\n", encoding="utf-8")
        db = gt_dir / "graph.db"
        with tempfile.NamedTemporaryFile(
            dir=gt_dir, prefix=".graph.", suffix=".db", delete=False
        ) as handle:
            candidate = Path(handle.name)
        candidate.unlink(missing_ok=True)
        diagnostic_stream = io.StringIO()
        with redirect_stderr(diagnostic_stream):
            index_ok = run_index(root_text, str(candidate))
        if not index_ok:
            candidate.unlink(missing_ok=True)
            return receipt(
                IndexBuildStatus.BUILD_FAILED,
                binary_sha256=binary["binary_sha256"],
                error_type="run_index_false",
                error_diagnostic=diagnostic_stream.getvalue(),
                coverage=coverage,
            )
        if not candidate.is_file():
            return receipt(
                IndexBuildStatus.BUILD_FAILED,
                binary_sha256=binary["binary_sha256"],
                error_type="graph_not_created",
                coverage=coverage,
            )
        schema_valid, node_count, edge_count, fts_tables, quick_check = _graph_schema_receipt(
            candidate
        )
        if not schema_valid:
            candidate.unlink(missing_ok=True)
            return receipt(
                IndexBuildStatus.INVALID_DATABASE,
                binary_sha256=binary["binary_sha256"],
                error_type=f"schema:{quick_check[:80]}",
                coverage=coverage,
            )
        graph_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest()
        parser_failures = _graph_parser_failures(candidate)
        manifest = {
            "schema": "gt.graph_certification.v1",
            "repository_root_sha256": hashlib.sha256(
                os.path.realpath(root_text).encode("utf-8", "surrogatepass")
            ).hexdigest(),
            "graph_sha256": graph_sha256,
            "graph_bytes": candidate.stat().st_size,
            "sqlite_quick_check": "ok",
            "source_revision": source_revision,
            "parser_failures": parser_failures,
            **_binary_certification(),
        }
        manifest["binary_certified"] = bool(manifest["binary_sha256"])
        manifest_bytes = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        backup = gt_dir / ".graph.previous.db"
        had_previous = db.is_file()
        if had_previous:
            shutil.copyfile(db, backup)
        try:
            # The database itself is published in one atomic filesystem swap.
            os.replace(candidate, db)
            _atomic_write(db.with_suffix(".manifest.json"), manifest_bytes)
        except Exception:
            if had_previous and backup.is_file():
                os.replace(backup, db)
            else:
                db.unlink(missing_ok=True)
                db.with_suffix(".manifest.json").unlink(missing_ok=True)
            raise
        finally:
            candidate.unlink(missing_ok=True)
            backup.unlink(missing_ok=True)
        return receipt(
            coverage.status,
            graph_db=str(db),
            graph_revision=graph_sha256,
            binary_sha256=str(manifest["binary_sha256"]),
            coverage=coverage,
            schema_valid=True,
            node_count=node_count,
            edge_count=edge_count,
            fts_tables=fts_tables,
            parser_failures=parser_failures,
        )
    except Exception as exc:  # noqa: BLE001 - indexing failure remains correct-or-quiet
        return receipt(
            IndexBuildStatus.BUILD_FAILED,
            error_type=type(exc).__name__,
            coverage=coverage,
        )


def ensure_index(root: str, *, state_dir: str | None = None) -> str | None:
    """Compatibility wrapper returning only the available graph path."""

    return ensure_index_with_receipt(root, state_dir=state_dir).graph_db


def refresh_index_files(
    root: str | os.PathLike[str],
    graph_db: str | os.PathLike[str],
    changed_paths: tuple[str, ...],
    *,
    timeout: float = 30.0,
    source_revision: str = "",
) -> IndexBuildReceipt:
    """Atomically refresh changed indexable files in an existing graph."""

    started = time.perf_counter()
    coverage = inspect_source_coverage(root)
    certification = _binary_certification()

    def result(
        status: IndexBuildStatus,
        *,
        graph_revision: str = "",
        schema_valid: bool = False,
        node_count: int = 0,
        edge_count: int = 0,
        fts_tables: tuple[str, ...] = (),
        error_type: str | None = None,
        parser_failures: int = 0,
    ) -> IndexBuildReceipt:
        return IndexBuildReceipt(
            status=status,
            graph_db=os.fspath(graph_db) if schema_valid else None,
            graph_revision=graph_revision,
            binary_sha256=certification["binary_sha256"],
            elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
            error_type=error_type,
            source_files=coverage.source_files,
            indexable_files=coverage.indexable_files,
            unsupported_suffixes=coverage.unsupported_suffixes,
            unsupported_paths=coverage.unsupported_paths,
            ambiguous_paths=coverage.ambiguous_paths,
            language_file_counts=coverage.language_file_counts,
            resolution_reason_counts=coverage.resolution_reason_counts,
            parser_failures=parser_failures,
            schema_valid=schema_valid,
            node_count=node_count,
            edge_count=edge_count,
            fts_tables=fts_tables,
            source_revision=source_revision,
        )

    root_path = Path(root).resolve()
    database = Path(graph_db).resolve()
    binary = _resolved_binary_path()
    if not root_path.is_dir() or not database.is_file():
        return result(IndexBuildStatus.BUILD_FAILED, error_type="invalid_incremental_root")
    if not binary or not Path(binary).is_file():
        return result(IndexBuildStatus.MISSING_BINARY)
    selected: list[str] = []
    for raw in changed_paths:
        normalized = str(raw or "").replace("\\", "/").lstrip("./")
        target = (root_path / normalized).resolve()
        try:
            target.relative_to(root_path)
        except ValueError:
            return result(IndexBuildStatus.BUILD_FAILED, error_type="unsafe_incremental_path")
        if not target.is_file() or not is_indexable_source(
            target, _read_language_prefix(target)
        ):
            continue
        if normalized not in selected:
            selected.append(normalized)
    if not selected:
        schema_valid, nodes, edges, fts, check = _graph_schema_receipt(database)
        return result(
            coverage.status if schema_valid else IndexBuildStatus.INVALID_DATABASE,
            graph_revision=(
                hashlib.sha256(database.read_bytes()).hexdigest() if schema_valid else ""
            ),
            schema_valid=schema_valid,
            node_count=nodes,
            edge_count=edges,
            fts_tables=fts,
            error_type=None if schema_valid else f"schema:{check[:80]}",
            parser_failures=_graph_parser_failures(database) if schema_valid else 0,
        )
    with tempfile.NamedTemporaryFile(
        dir=database.parent, prefix=".graph.incremental.", suffix=".db", delete=False
    ) as handle:
        candidate = Path(handle.name)
    try:
        shutil.copyfile(database, candidate)
        per_call_timeout = max(1.0, float(timeout) / (len(selected) + 1))
        for relative in selected:
            completed = subprocess.run(
                [
                    binary,
                    "-root",
                    str(root_path),
                    "-output",
                    str(candidate),
                    "-file",
                    relative,
                    "-closure=false",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=per_call_timeout,
                check=False,
            )
            if completed.returncode:
                return result(
                    IndexBuildStatus.BUILD_FAILED,
                    error_type=f"incremental_file:{completed.returncode}",
                )
        closure = subprocess.run(
            [
                binary,
                "-root",
                str(root_path),
                "-output",
                str(candidate),
                "-rebuild-closure",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=per_call_timeout,
            check=False,
        )
        if closure.returncode:
            return result(
                IndexBuildStatus.BUILD_FAILED,
                error_type=f"incremental_closure:{closure.returncode}",
            )
        schema_valid, nodes, edges, fts, check = _graph_schema_receipt(candidate)
        if not schema_valid:
            return result(
                IndexBuildStatus.INVALID_DATABASE,
                error_type=f"schema:{check[:80]}",
            )
        graph_revision = hashlib.sha256(candidate.read_bytes()).hexdigest()
        manifest_path = database.with_suffix(".manifest.json")
        manifest = {
            "schema": "gt.graph_certification.v1",
            "repository_root_sha256": hashlib.sha256(
                os.path.realpath(root_path).encode("utf-8", "surrogatepass")
            ).hexdigest(),
            "graph_sha256": graph_revision,
            "graph_bytes": candidate.stat().st_size,
            "sqlite_quick_check": "ok",
            "source_revision": source_revision,
            "parser_failures": _graph_parser_failures(candidate),
            **certification,
            "binary_certified": bool(certification["binary_sha256"]),
            "refresh_mode": "incremental",
            "changed_paths": selected,
        }
        manifest_bytes = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        with tempfile.NamedTemporaryFile(
            dir=database.parent,
            prefix=".graph.incremental.previous.",
            suffix=".db",
            delete=False,
        ) as handle:
            backup = Path(handle.name)
        previous_manifest = manifest_path.read_bytes() if manifest_path.is_file() else None
        shutil.copyfile(database, backup)
        try:
            os.replace(candidate, database)
            _atomic_write(manifest_path, manifest_bytes)
        except Exception:
            os.replace(backup, database)
            if previous_manifest is None:
                manifest_path.unlink(missing_ok=True)
            else:
                _atomic_write(manifest_path, previous_manifest)
            raise
        finally:
            backup.unlink(missing_ok=True)
        return result(
            coverage.status,
            graph_revision=graph_revision,
            schema_valid=True,
            node_count=nodes,
            edge_count=edges,
            fts_tables=fts,
            parser_failures=_graph_parser_failures(database),
        )
    except subprocess.TimeoutExpired:
        return result(IndexBuildStatus.BUILD_FAILED, error_type="incremental_timeout")
    except OSError as exc:
        return result(IndexBuildStatus.BUILD_FAILED, error_type=type(exc).__name__)
    finally:
        candidate.unlink(missing_ok=True)
