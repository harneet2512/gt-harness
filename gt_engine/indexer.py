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

import base64
import binascii
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr
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
    graph_db_sha256: str = ""
    graph_manifest_sha256: str = ""
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
class GraphReadLease:
    """An open-time proof that a reader sees the certified graph generation."""

    graph_path: Path
    graph_revision: str
    source_revision: str
    graph_db_sha256: str
    graph_manifest_sha256: str


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
    try:
        root_text = os.fspath(root)
        for dirpath, dirnames, filenames in os.walk(root_text):
            dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
            for filename in filenames:
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
        _durable_replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _fsync_file(path: Path) -> None:
    with open(path, "r+b") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_replace(source: Path, destination: Path) -> None:
    """Atomically replace a path and durably order the metadata update."""

    if os.name == "nt":
        import ctypes

        movefile_replace_existing = 0x1
        movefile_write_through = 0x8
        moved = ctypes.windll.kernel32.MoveFileExW(  # type: ignore[attr-defined]
            str(source),
            str(destination),
            movefile_replace_existing | movefile_write_through,
        )
        if not moved:
            raise ctypes.WinError()
        return
    os.replace(source, destination)
    _fsync_directory(destination.parent)


_REQUIRED_GRAPH_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "nodes": frozenset(
        {
            "id",
            "label",
            "name",
            "qualified_name",
            "file_path",
            "start_line",
            "end_line",
            "signature",
            "return_type",
            "is_exported",
            "is_test",
            "language",
            "parent_id",
            "repo_id",
        }
    ),
    "edges": frozenset(
        {
            "id",
            "source_id",
            "target_id",
            "type",
            "source_line",
            "source_file",
            "resolution_method",
            "confidence",
            "metadata",
            "trust_tier",
            "candidate_count",
            "evidence_type",
            "verification_status",
            "repo_id",
        }
    ),
    "properties": frozenset(
        {
            "id",
            "node_id",
            "kind",
            "value",
            "line",
            "confidence",
            "property_id",
            "start_line",
            "end_line",
            "extractor",
            "evidence_method",
            "trust_tier",
            "verification_status",
            "source_revision",
            "repo_id",
        }
    ),
    "assertions": frozenset(
        {"id", "test_node_id", "target_node_id", "resolution_score", "kind", "expression"}
    ),
    "repos": frozenset({"id", "root", "commit"}),
    "file_hashes": frozenset({"file_path", "content_hash", "language", "indexed_at"}),
    "project_meta": frozenset({"key", "value"}),
    "cochanges": frozenset({"file_a", "file_b", "count"}),
    "cochange_sets": frozenset({"commit_hash", "file_path"}),
    "closure": frozenset({"source_id", "target_id", "depth", "min_confidence", "repo_id"}),
    "edge_metadata": frozenset({"edge_id", "key", "value", "schema_version"}),
    "content_passages": frozenset(
        {"passage_id", "node_id", "start_line", "end_line", "content", "content_hash", "repo_id"}
    ),
}
_REQUIRED_GRAPH_FTS_TABLES = frozenset(
    {"nodes_fts", "symbol_content_fts", "content_passages_fts"}
)
_REQUIRED_GRAPH_META_KEYS = frozenset(
    {"schema_version", "file_count", "parse_failures", "post_revision"}
)
_SUPPORTED_GRAPH_SCHEMA_VERSIONS = frozenset(
    {"v15.2-trust-tier", "v16-multirepo"}
)


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
            required = set(_REQUIRED_GRAPH_TABLE_COLUMNS) | set(
                _REQUIRED_GRAPH_FTS_TABLES
            )
            missing_tables = required - tables
            missing_columns: list[str] = []
            if not missing_tables:
                for table, expected_columns in _REQUIRED_GRAPH_TABLE_COLUMNS.items():
                    actual_columns = {
                        str(row[1])
                        for row in connection.execute(f'PRAGMA table_info("{table}")')
                    }
                    for column in sorted(expected_columns - actual_columns):
                        missing_columns.append(f"{table}.{column}")
            metadata_keys = (
                {
                    str(row[0])
                    for row in connection.execute("SELECT key FROM project_meta")
                }
                if "project_meta" in tables
                else set()
            )
            missing_meta = _REQUIRED_GRAPH_META_KEYS - metadata_keys
            schema_version_row = (
                connection.execute(
                    "SELECT value FROM project_meta WHERE key='schema_version'"
                ).fetchone()
                if "schema_version" in metadata_keys
                else None
            )
            schema_version = str(schema_version_row[0] if schema_version_row else "")
            unsupported_schema_version = (
                schema_version not in _SUPPORTED_GRAPH_SCHEMA_VERSIONS
            )
            foreign_key_violations = (
                list(connection.execute("PRAGMA foreign_key_check"))
                if not missing_tables
                else []
            )
            schema_valid = bool(
                quick_check.lower() == "ok"
                and not missing_tables
                and not missing_columns
                and not missing_meta
                and not unsupported_schema_version
                and not foreign_key_violations
            )
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
            if quick_check.lower() == "ok" and missing_tables:
                detail = "missing_tables:" + ",".join(sorted(missing_tables))
            elif quick_check.lower() == "ok" and missing_columns:
                detail = "missing_columns:" + ",".join(missing_columns)
            elif quick_check.lower() == "ok" and missing_meta:
                detail = "missing_metadata:" + ",".join(sorted(missing_meta))
            elif quick_check.lower() == "ok" and unsupported_schema_version:
                detail = "unsupported_schema_version:" + schema_version
            elif quick_check.lower() == "ok" and foreign_key_violations:
                detail = f"foreign_key_violations:{len(foreign_key_violations)}"
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


def _graph_logical_revision(path: Path) -> str:
    """Read the canonical content-derived graph revision."""

    try:
        connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT value FROM project_meta WHERE key='post_revision'"
            ).fetchone()
        finally:
            connection.close()
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return ""
    revision = str(row[0] if row else "").strip().lower()
    return (
        revision
        if len(revision) == 64
        and all(character in "0123456789abcdef" for character in revision)
        else ""
    )


def _graph_publication_journal_path(database: Path) -> Path:
    return database.parent / f".{database.name}.publication.json"


def _publication_payload(value: bytes | None) -> str:
    return base64.b64encode(value or b"").decode("ascii")


def _publication_bytes(value: object) -> bytes:
    return base64.b64decode(str(value or ""), validate=True)


def _cleanup_graph_publication_journal(database: Path, journal: dict[str, object]) -> None:
    for key in ("backup_name", "candidate_name"):
        name = str(journal.get(key) or "")
        if name and Path(name).name == name:
            (database.parent / name).unlink(missing_ok=True)
    _graph_publication_journal_path(database).unlink(missing_ok=True)
    _fsync_directory(database.parent)


def _recover_interrupted_graph_publication(
    database: Path, manifest_path: Path
) -> tuple[bool, str]:
    """Complete or roll back a durable two-file publication transaction."""

    journal_path = _graph_publication_journal_path(database)
    if not journal_path.is_file():
        return True, ""
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if (
            journal.get("schema") != "gt.graph_publication_journal.v1"
            or str(journal.get("database_name") or "") != database.name
            or str(journal.get("manifest_name") or "") != manifest_path.name
        ):
            return False, "publication_journal_identity_invalid"
        new_hash = str(journal.get("new_graph_sha256") or "")
        previous_hash = str(journal.get("previous_graph_sha256") or "")
        new_manifest = _publication_bytes(journal.get("new_manifest_b64"))
        previous_manifest = _publication_bytes(journal.get("previous_manifest_b64"))
        backup_name = str(journal.get("backup_name") or "")
        candidate_name = str(journal.get("candidate_name") or "")
        if backup_name and Path(backup_name).name != backup_name:
            return False, "publication_journal_backup_unsafe"
        if candidate_name and Path(candidate_name).name != candidate_name:
            return False, "publication_journal_candidate_unsafe"
        backup = database.parent / backup_name if backup_name else None
        candidate = database.parent / candidate_name if candidate_name else None

        actual_hash = (
            hashlib.sha256(database.read_bytes()).hexdigest()
            if database.is_file()
            else ""
        )
        if actual_hash and actual_hash == new_hash:
            _atomic_write(manifest_path, new_manifest)
        elif actual_hash and previous_hash and actual_hash == previous_hash:
            if previous_manifest:
                _atomic_write(manifest_path, previous_manifest)
            else:
                manifest_path.unlink(missing_ok=True)
        elif backup is not None and backup.is_file() and previous_hash:
            if hashlib.sha256(backup.read_bytes()).hexdigest() != previous_hash:
                return False, "publication_journal_backup_hash_mismatch"
            _durable_replace(backup, database)
            if previous_manifest:
                _atomic_write(manifest_path, previous_manifest)
            else:
                manifest_path.unlink(missing_ok=True)
        elif candidate is not None and candidate.is_file() and new_hash:
            if hashlib.sha256(candidate.read_bytes()).hexdigest() != new_hash:
                return False, "publication_journal_candidate_hash_mismatch"
            _durable_replace(candidate, database)
            _atomic_write(manifest_path, new_manifest)
        else:
            return False, "publication_journal_unrecoverable"
        _cleanup_graph_publication_journal(database, journal)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        binascii.Error,
    ):
        return False, "publication_journal_unreadable"
    return True, ""


def _certify_published_graph(
    database: Path,
    manifest_path: Path,
    *,
    expected_root: str | os.PathLike[str],
    expected_source_revision: str = "",
    expected_binary_sha256: str = "",
    lock_timeout: float = 30.0,
    _lock_held: bool = False,
    _recover_pending: bool = True,
) -> tuple[bool, str]:
    """Prove that the published database and manifest are one current identity."""

    if not _lock_held:
        try:
            with _graph_publication_lock(database.parent, timeout=lock_timeout):
                return _certify_published_graph(
                    database,
                    manifest_path,
                    expected_root=expected_root,
                    expected_source_revision=expected_source_revision,
                    expected_binary_sha256=expected_binary_sha256,
                    lock_timeout=lock_timeout,
                    _lock_held=True,
                    _recover_pending=_recover_pending,
                )
        except TimeoutError:
            return False, "publication_lock_timeout"

    if _recover_pending:
        recovered, recovery_error = _recover_interrupted_graph_publication(
            database, manifest_path
        )
        if not recovered:
            return False, recovery_error
    if not database.is_file() or not manifest_path.is_file():
        return False, "missing_graph_or_manifest"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema") != "gt.graph_certification.v1":
            return False, "manifest_schema_mismatch"
        expected_root_hash = hashlib.sha256(
            os.path.realpath(expected_root).encode("utf-8", "surrogatepass")
        ).hexdigest()
        if str(manifest.get("repository_root_sha256") or "") != expected_root_hash:
            return False, "repository_root_mismatch"
        graph_bytes = database.read_bytes()
        if int(manifest.get("graph_bytes") or -1) != len(graph_bytes):
            return False, "graph_bytes_mismatch"
        if str(manifest.get("graph_sha256") or "") != hashlib.sha256(
            graph_bytes
        ).hexdigest():
            return False, "graph_sha256_mismatch"
        if expected_source_revision and str(
            manifest.get("source_revision") or ""
        ) != str(expected_source_revision):
            return False, "source_revision_mismatch"
        if expected_binary_sha256 and str(
            manifest.get("binary_sha256") or ""
        ) != expected_binary_sha256:
            return False, "binary_identity_mismatch"
        if not bool(manifest.get("binary_certified")):
            return False, "binary_not_certified"
        if str(manifest.get("sqlite_quick_check") or "").lower() != "ok":
            return False, "manifest_sqlite_quick_check_invalid"
        schema_valid, _nodes, _edges, _fts, schema_detail = _graph_schema_receipt(
            database
        )
        if not schema_valid:
            return False, "graph_schema_invalid:" + schema_detail[:80]
        manifest_logical_revision = str(manifest.get("logical_graph_revision") or "")
        if manifest_logical_revision and manifest_logical_revision != _graph_logical_revision(
            database
        ):
            return False, "logical_graph_revision_mismatch"
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return False, "manifest_unreadable"
    return True, ""


@contextmanager
def _graph_publication_lock(directory: Path, *, timeout: float = 30.0):
    """Serialize graph/manifest publication across host processes."""

    lock_path = directory / ".graph.publish.lock"
    handle = open(lock_path, "a+b")
    if lock_path.stat().st_size == 0:
        handle.write(b"\0")
        handle.flush()
    deadline = time.monotonic() + max(0.05, float(timeout))
    acquired = False
    try:
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("graph_publication_lock_timeout") from None
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@contextmanager
def certified_graph_read(
    receipt: IndexBuildReceipt,
    *,
    expected_root: Path,
    expected_source_revision: str,
    lock_timeout: float = 30.0,
) -> Iterator[GraphReadLease]:
    """Hold publication authority while consuming one certified graph pair."""

    if not receipt.available or not receipt.schema_valid or not receipt.graph_db:
        raise ValueError("graph_read_receipt_not_available")
    if not expected_source_revision or receipt.source_revision != expected_source_revision:
        raise ValueError("graph_read_source_revision_mismatch")
    database = Path(receipt.graph_db).resolve()
    if not database.is_file():
        raise FileNotFoundError(database)
    manifest_path = database.with_suffix(".manifest.json")
    with _graph_publication_lock(database.parent, timeout=lock_timeout):
        certified, error = _certify_published_graph(
            database,
            manifest_path,
            expected_root=expected_root.resolve(),
            expected_source_revision=expected_source_revision,
            expected_binary_sha256=receipt.binary_sha256,
            _lock_held=True,
        )
        if not certified:
            raise ValueError("graph_read_certification:" + error)
        observed_hash = hashlib.sha256(database.read_bytes()).hexdigest()
        if not receipt.graph_db_sha256 or observed_hash != receipt.graph_db_sha256:
            raise ValueError("graph_read_database_hash_mismatch")
        observed_revision = _graph_logical_revision(database)
        if receipt.graph_revision and observed_revision != receipt.graph_revision:
            raise ValueError("graph_read_logical_revision_mismatch")
        yield GraphReadLease(
            graph_path=database,
            graph_revision=observed_revision,
            source_revision=expected_source_revision,
            graph_db_sha256=observed_hash,
            graph_manifest_sha256=receipt.graph_manifest_sha256,
        )


def _publish_graph_pair(
    database: Path,
    candidate: Path,
    manifest_bytes: bytes,
    *,
    expected_root: str | os.PathLike[str],
    expected_source_revision: str,
    expected_binary_sha256: str,
    _lock_held: bool = False,
) -> None:
    """Publish and certify one DB/manifest pair, rolling both back together."""

    if not _lock_held:
        with _graph_publication_lock(database.parent):
            return _publish_graph_pair(
                database,
                candidate,
                manifest_bytes,
                expected_root=expected_root,
                expected_source_revision=expected_source_revision,
                expected_binary_sha256=expected_binary_sha256,
                _lock_held=True,
            )

    manifest_path = database.with_suffix(".manifest.json")
    recovered, recovery_error = _recover_interrupted_graph_publication(
        database, manifest_path
    )
    if not recovered:
        raise OSError("graph_publication_recovery_" + recovery_error)
    previous_manifest = manifest_path.read_bytes() if manifest_path.is_file() else None
    had_previous = database.is_file()
    backup: Path | None = None
    previous_hash = ""
    if had_previous:
        previous_hash = hashlib.sha256(database.read_bytes()).hexdigest()
        with tempfile.NamedTemporaryFile(
            dir=database.parent,
            prefix=".graph.previous.",
            suffix=".db",
            delete=False,
        ) as handle:
            backup = Path(handle.name)
        shutil.copyfile(database, backup)
        _fsync_file(backup)
    try:
        new_manifest = json.loads(manifest_bytes.decode("utf-8"))
        new_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if (
            str(new_manifest.get("graph_sha256") or "") != new_hash
            or int(new_manifest.get("graph_bytes") or -1) != candidate.stat().st_size
        ):
            raise OSError("publication_candidate_manifest_mismatch")
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if backup is not None:
            backup.unlink(missing_ok=True)
        raise OSError("publication_manifest_invalid") from exc

    journal = {
        "schema": "gt.graph_publication_journal.v1",
        "database_name": database.name,
        "manifest_name": manifest_path.name,
        "candidate_name": candidate.name,
        "backup_name": backup.name if backup is not None else "",
        "new_graph_sha256": new_hash,
        "previous_graph_sha256": previous_hash,
        "new_manifest_b64": _publication_payload(manifest_bytes),
        "previous_manifest_b64": _publication_payload(previous_manifest),
    }
    journal_bytes = json.dumps(
        journal, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    try:
        _fsync_file(candidate)
        _atomic_write(_graph_publication_journal_path(database), journal_bytes)
        _durable_replace(candidate, database)
        _atomic_write(manifest_path, manifest_bytes)
        certified, certification_error = _certify_published_graph(
            database,
            manifest_path,
            expected_root=expected_root,
            expected_source_revision=expected_source_revision,
            expected_binary_sha256=expected_binary_sha256,
            _lock_held=True,
            _recover_pending=False,
        )
        if not certified:
            raise OSError("published_graph_" + certification_error)
    except Exception:
        if had_previous and backup is not None and backup.is_file():
            _durable_replace(backup, database)
            if previous_manifest is None:
                manifest_path.unlink(missing_ok=True)
            else:
                _atomic_write(manifest_path, previous_manifest)
        else:
            database.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
        _cleanup_graph_publication_journal(database, journal)
        raise
    else:
        _cleanup_graph_publication_journal(database, journal)


def ensure_index_with_receipt(
    root: str | os.PathLike[str] | None,
    *,
    state_dir: str | os.PathLike[str] | None = None,
    source_revision: str = "",
    timeout: float = 600.0,
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
        graph_db_sha256: str = "",
        graph_manifest_sha256: str = "",
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
            graph_db_sha256=graph_db_sha256,
            graph_manifest_sha256=graph_manifest_sha256,
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
            index_ok = run_index(
                root_text,
                str(candidate),
                timeout=int(max(1.0, float(timeout))),
            )
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
        logical_graph_revision = _graph_logical_revision(candidate)
        parser_failures = _graph_parser_failures(candidate)
        manifest = {
            "schema": "gt.graph_certification.v1",
            "repository_root_sha256": hashlib.sha256(
                os.path.realpath(root_text).encode("utf-8", "surrogatepass")
            ).hexdigest(),
            "graph_sha256": graph_sha256,
            "logical_graph_revision": logical_graph_revision,
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
        try:
            _publish_graph_pair(
                db,
                candidate,
                manifest_bytes,
                expected_root=root_text,
                expected_source_revision=source_revision,
                expected_binary_sha256=str(manifest["binary_sha256"]),
            )
        finally:
            candidate.unlink(missing_ok=True)
        return receipt(
            coverage.status,
            graph_db=str(db),
            graph_revision=logical_graph_revision,
            graph_db_sha256=graph_sha256,
            graph_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
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


def graph_reverse_dependents(
    graph_db: str | os.PathLike[str],
    changed_paths: tuple[str, ...],
    *,
    limit: int = 10_000,
) -> tuple[str, ...]:
    """Return graph-recorded reverse dependents of changed files.

    This is a conservative dependency closure seed for incremental refresh.
    It does not claim textual completeness: when the graph cannot answer, the
    caller must select a full rebuild rather than treating an empty result as
    proof that no dependents exist.
    """

    database = Path(graph_db)
    paths = tuple(dict.fromkeys(path.replace("\\", "/") for path in changed_paths if path))
    if not paths:
        return ()
    if not database.is_file():
        raise FileNotFoundError(database)
    placeholders = ",".join("?" for _ in paths)
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        rows = connection.execute(
            "SELECT DISTINCT e.source_file FROM edges e "
            "JOIN nodes target ON target.id=e.target_id "
            f"WHERE target.file_path IN ({placeholders}) "
            "AND e.source_file IS NOT NULL AND e.source_file != '' "
            "ORDER BY e.source_file LIMIT ?",
            (*paths, max(1, int(limit))),
        ).fetchall()
    finally:
        connection.close()
    changed = set(paths)
    return tuple(str(row[0]).replace("\\", "/") for row in rows if str(row[0]) not in changed)


def refresh_index_files(
    root: str | os.PathLike[str],
    graph_db: str | os.PathLike[str],
    changed_paths: tuple[str, ...],
    *,
    timeout: float = 30.0,
    source_revision: str = "",
    _lock_held: bool = False,
    _started: float | None = None,
) -> IndexBuildReceipt:
    """Atomically refresh changed indexable files in an existing graph."""

    started = _started if _started is not None else time.perf_counter()
    coverage = inspect_source_coverage(root)
    certification = _binary_certification()

    def result(
        status: IndexBuildStatus,
        *,
        graph_revision: str = "",
        graph_db_sha256: str = "",
        graph_manifest_sha256: str = "",
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
            graph_db_sha256=graph_db_sha256,
            graph_manifest_sha256=graph_manifest_sha256,
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
    if not _lock_held:
        try:
            with _graph_publication_lock(database.parent, timeout=timeout):
                return refresh_index_files(
                    root,
                    graph_db,
                    changed_paths,
                    timeout=timeout,
                    source_revision=source_revision,
                    _lock_held=True,
                    _started=started,
                )
        except TimeoutError:
            return result(
                IndexBuildStatus.BUILD_FAILED,
                error_type="graph_publication_lock_timeout",
            )
    manifest_path = database.with_suffix(".manifest.json")
    published, publication_error = _certify_published_graph(
        database,
        manifest_path,
        expected_root=root_path,
        expected_binary_sha256=certification["binary_sha256"],
        _lock_held=True,
    )
    if not published:
        return result(
            IndexBuildStatus.INVALID_DATABASE,
            error_type="certification:" + publication_error,
        )
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
        current, current_error = _certify_published_graph(
            database,
            manifest_path,
            expected_root=root_path,
            expected_source_revision=source_revision,
            expected_binary_sha256=certification["binary_sha256"],
            _lock_held=True,
        )
        if not current:
            return result(
                IndexBuildStatus.INVALID_DATABASE,
                error_type="certification:" + current_error,
            )
        schema_valid, nodes, edges, fts, check = _graph_schema_receipt(database)
        graph_db_sha256 = (
            hashlib.sha256(database.read_bytes()).hexdigest() if schema_valid else ""
        )
        return result(
            coverage.status if schema_valid else IndexBuildStatus.INVALID_DATABASE,
            graph_revision=_graph_logical_revision(database) if schema_valid else "",
            graph_db_sha256=graph_db_sha256,
            graph_manifest_sha256=(
                hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                if schema_valid and manifest_path.is_file()
                else ""
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
    with tempfile.NamedTemporaryFile(
        dir=database.parent,
        prefix=".graph.incremental.",
        suffix=".files.json",
        mode="w",
        encoding="utf-8",
        delete=False,
    ) as handle:
        json.dump(selected, handle, ensure_ascii=False, separators=(",", ":"))
        batch_manifest = Path(handle.name)
    try:
        shutil.copyfile(database, candidate)
        completed = subprocess.run(
            [
                binary,
                "-root",
                str(root_path),
                "-output",
                str(candidate),
                "-files-manifest",
                str(batch_manifest),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, float(timeout)),
            check=False,
        )
        if completed.returncode:
            return result(
                IndexBuildStatus.BUILD_FAILED,
                error_type=f"incremental_batch:{completed.returncode}",
            )
        schema_valid, nodes, edges, fts, check = _graph_schema_receipt(candidate)
        if not schema_valid:
            return result(
                IndexBuildStatus.INVALID_DATABASE,
                error_type=f"schema:{check[:80]}",
            )
        graph_db_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest()
        logical_graph_revision = _graph_logical_revision(candidate)
        manifest = {
            "schema": "gt.graph_certification.v1",
            "repository_root_sha256": hashlib.sha256(
                os.path.realpath(root_path).encode("utf-8", "surrogatepass")
            ).hexdigest(),
            "graph_sha256": graph_db_sha256,
            "logical_graph_revision": logical_graph_revision,
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
        _publish_graph_pair(
            database,
            candidate,
            manifest_bytes,
            expected_root=root_path,
            expected_source_revision=source_revision,
            expected_binary_sha256=certification["binary_sha256"],
            _lock_held=True,
        )
        return result(
            coverage.status,
            graph_revision=logical_graph_revision,
            graph_db_sha256=graph_db_sha256,
            graph_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
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
        batch_manifest.unlink(missing_ok=True)
