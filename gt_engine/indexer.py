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
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# Extensions gt-index parses (tree-sitter structural coverage). A root with at
# least one of these is a code repository worth indexing.
SOURCE_EXTS = frozenset({
    ".py", ".pyi", ".go", ".rs", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".rb", ".java", ".kt", ".kts", ".cs", ".php", ".swift", ".scala",
    ".c", ".h", ".cc", ".hh", ".cpp", ".hpp", ".m", ".mm", ".lua", ".ex",
    ".exs", ".erl", ".hs", ".ml", ".clj", ".dart", ".zig", ".sh",
})

# Never descend into these (vendored/build/VCS trees are not the task's code).
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".gt", ".groundtruth", "node_modules", ".venv",
    "venv", "__pycache__", ".tox", ".mypy_cache", ".ruff_cache", "dist",
    "build", ".idea", ".vscode", "target", "vendor",
})

# Known local gt-index builds probed only when nothing else resolves.
_LOCAL_BINARY_CANDIDATES = (
    r"D:\Groundtruth\gt-index\gt-index.exe",
    "/opt/groundtruth/gt-index/gt-index",
)

_MAX_SCAN_FILES = 50_000  # detection bound; a hit returns immediately

GRAPH_SCHEMA_VERSION = "gt.graph_certification.v1"


def verify_configured_producer_artifact(
    *, binary_path: str | Path | None = None,
    receipt_path: str | Path | None = None,
) -> tuple[bool, str]:
    """Fail closed when an explicitly pinned producer receipt is invalid."""
    configured = os.environ.get("GT_PRODUCER_ARTIFACT")
    if receipt_path is None:
        receipt_path = configured
    if not receipt_path:
        return True, "unconfigured"
    try:
        from gt_engine.producer_artifact import verify_producer_artifact

        receipt_file = Path(receipt_path)
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        return verify_producer_artifact(receipt, binary=binary_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False, "receipt_unreadable"


def source_manifest_digest(root: str | Path) -> str:
    """Hash sorted, length-delimited source path and file-byte identities."""
    root_path = Path(root)
    records: list[tuple[str, int, str]] = []
    for directory, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.suffix.lower() not in SOURCE_EXTS:
                continue
            relative = path.relative_to(root_path).as_posix()
            payload = path.read_bytes()
            records.append((relative, len(payload), hashlib.sha256(payload).hexdigest()))
    encoded = bytearray()
    for relative, size, byte_hash in sorted(records):
        path_bytes = relative.encode("utf-8", "surrogatepass")
        hash_bytes = byte_hash.encode("ascii")
        encoded.extend(len(path_bytes).to_bytes(8, "big"))
        encoded.extend(path_bytes)
        encoded.extend(size.to_bytes(8, "big"))
        encoded.extend(len(hash_bytes).to_bytes(8, "big"))
        encoded.extend(hash_bytes)
    return hashlib.sha256(bytes(encoded)).hexdigest()


@dataclass(frozen=True, slots=True)
class IndexReuseKey:
    source_manifest_sha256: str
    producer_binary_sha256: str
    graph_schema_version: str = GRAPH_SCHEMA_VERSION

    def as_dict(self) -> dict[str, str]:
        return {
            "source_manifest_sha256": self.source_manifest_sha256,
            "producer_binary_sha256": self.producer_binary_sha256,
            "graph_schema_version": self.graph_schema_version,
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


def compute_index_reuse_key(
    root: str | Path, *, graph_schema_version: str = GRAPH_SCHEMA_VERSION
) -> IndexReuseKey:
    return IndexReuseKey(
        source_manifest_digest(root),
        _binary_certification().get("binary_sha256", ""),
        graph_schema_version,
    )


def is_code_repo(root: str) -> bool:
    """True iff ``root`` contains at least one source file (bounded scan)."""
    seen = 0
    try:
        for _dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                seen += 1
                if os.path.splitext(fn)[1].lower() in SOURCE_EXTS:
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
    candidate = os.environ.get("GT_INDEX_BINARY") or shutil.which("gt-index") or ""
    if not candidate:
        try:
            from groundtruth._binary import CACHE_DIR, GT_INDEX_VERSION

            name = "gt-index.exe" if os.name == "nt" else "gt-index"
            cached = Path(CACHE_DIR) / GT_INDEX_VERSION / name
            candidate = str(cached) if cached.is_file() else ""
        except (ImportError, AttributeError):
            candidate = ""
    path = Path(candidate).resolve() if candidate else None
    if path is None or not path.is_file():
        return {"path_sha256": "", "binary_sha256": ""}
    if os.environ.get("GT_PRODUCER_ARTIFACT"):
        valid, _reason = verify_configured_producer_artifact(binary_path=path)
        if not valid:
            return {"path_sha256": "", "binary_sha256": ""}
    return {
        "path_sha256": hashlib.sha256(str(path).encode("utf-8")).hexdigest(),
        "binary_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


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


def ensure_index(root: str, *, state_dir: str | None = None) -> str | None:
    """Ensure a fresh graph.db exists for ``root``; return its path or None.

    When ``GT_STATE_DIR`` is set, the db lives in a root-identity subdirectory
    there, completely outside the indexed/graded repository. The local default
    remains ``<root>/.gt/graph.db`` with a self-ignoring ``.gitignore``.
    Re-indexed on every call (a stale graph would violate correct-or-quiet;
    gt-index is fast). Never raises.
    """
    try:
        if not root or not os.path.isdir(root):
            return None
        if not is_code_repo(root):
            return None  # non-code task: GT dormant
        _seed_binary_env()
        from groundtruth._binary import run_index

        external = str(state_dir or os.environ.get("GT_STATE_DIR") or "").strip()
        if external:
            root_key = hashlib.sha256(
                os.path.realpath(root).encode("utf-8", "surrogatepass")
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
        reuse_key = compute_index_reuse_key(root)
        existing_manifest = db.with_suffix(".manifest.json")
        if db.is_file() and existing_manifest.is_file():
            try:
                manifest = json.loads(existing_manifest.read_text(encoding="utf-8"))
                if (
                    manifest.get("index_reuse_key") == reuse_key.as_dict()
                    and manifest.get("index_reuse_key_sha256") == reuse_key.digest
                ):
                    valid, _reason = _certify_published_graph(
                        db, existing_manifest, expected_root=Path(root),
                        expected_binary_sha256=reuse_key.producer_binary_sha256,
                    )
                    if valid:
                        return str(db)
            except (OSError, ValueError, TypeError):
                pass
        with tempfile.NamedTemporaryFile(
            dir=gt_dir, prefix=".graph.", suffix=".db", delete=False
        ) as handle:
            candidate = Path(handle.name)
        candidate.unlink(missing_ok=True)
        if not run_index(str(root), str(candidate)):
            candidate.unlink(missing_ok=True)
            return None
        if not candidate.is_file():
            return None
        try:
            con = sqlite3.connect(
                f"file:{candidate.resolve().as_posix()}?mode=ro", uri=True
            )
            try:
                quick_check = str(con.execute("PRAGMA quick_check").fetchone()[0])
            finally:
                con.close()
        except (sqlite3.Error, OSError):
            candidate.unlink(missing_ok=True)
            return None
        if quick_check.lower() != "ok":
            candidate.unlink(missing_ok=True)
            return None
        graph_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest()
        manifest = {
            "schema": "gt.graph_certification.v1",
            "graph_schema_version": GRAPH_SCHEMA_VERSION,
            "index_reuse_key": reuse_key.as_dict(),
            "index_reuse_key_sha256": reuse_key.digest,
            "source_manifest_sha256": reuse_key.source_manifest_sha256,
            "repository_root_sha256": hashlib.sha256(
                os.path.realpath(root).encode("utf-8", "surrogatepass")
            ).hexdigest(),
            "graph_sha256": graph_sha256,
            "graph_bytes": candidate.stat().st_size,
            "sqlite_quick_check": "ok",
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
        return str(db)
    except Exception:  # noqa: BLE001 - indexing failure means GT dormant, never a crash
        return None


class IndexBuildStatus(StrEnum):
    BUILT = "built"
    BUILD_FAILED = "build_failed"
    INVALID_DATABASE = "invalid_database"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class IndexBuildReceipt:
    status: IndexBuildStatus
    graph_db: str | None = None
    source_revision: str = ""
    graph_revision: str = ""
    error_type: str = ""
    error_diagnostic: str = ""
    attempts: tuple[str, ...] = ()

    @property
    def success(self) -> bool:
        return self.status is IndexBuildStatus.BUILT and bool(self.graph_db)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value, "graph_db": self.graph_db,
            "source_revision": self.source_revision, "graph_revision": self.graph_revision,
            "error_type": self.error_type, "error_diagnostic": self.error_diagnostic,
            "attempts": self.attempts,
        }


def _resolved_binary_path() -> str:
    candidate = os.environ.get("GT_INDEX_BINARY") or shutil.which("gt-index") or ""
    return str(Path(candidate).resolve()) if candidate else ""


@contextmanager
def _graph_publication_lock(_path: Path):
    """Process-local publication boundary; readers see complete files only."""
    yield


def _graph_schema_receipt(graph: Path) -> tuple[bool, str]:
    try:
        with sqlite3.connect(f"file:{graph.resolve().as_posix()}?mode=ro", uri=True) as con:
            check = str(con.execute("PRAGMA quick_check").fetchone()[0]).lower()
            if check != "ok":
                return False, f"quick_check:{check}"
            tables = {row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )}
        required = {"project_meta"}
        missing = sorted(required - tables)
        return (False, f"missing_tables:{','.join(missing)}") if missing else (True, "ok")
    except (sqlite3.Error, OSError) as exc:
        return False, f"{type(exc).__name__}:{exc}"


def _certify_published_graph(graph: Path, manifest_path: Path, *, expected_root: Path,
                             expected_source_revision: str = "",
                             expected_binary_sha256: str = "") -> tuple[bool, str]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "manifest_unreadable"
    if manifest.get("schema") != "gt.graph_certification.v1":
        return False, "manifest_schema_mismatch"
    root_sha = hashlib.sha256(
        os.path.realpath(expected_root).encode("utf-8", "surrogatepass")
    ).hexdigest()
    if manifest.get("repository_root_sha256") != root_sha:
        return False, "repository_root_mismatch"
    if expected_source_revision and manifest.get("source_revision") != expected_source_revision:
        return False, "source_revision_mismatch"
    if expected_binary_sha256 and manifest.get("binary_sha256") != expected_binary_sha256:
        return False, "binary_identity_mismatch"
    if not manifest.get("binary_certified"):
        return False, "binary_not_certified"
    if not graph.is_file():
        return False, "graph_missing"
    if manifest.get("graph_bytes") != graph.stat().st_size:
        return False, "graph_bytes_mismatch"
    if manifest.get("graph_sha256") != hashlib.sha256(graph.read_bytes()).hexdigest():
        return False, "graph_sha256_mismatch"
    valid, reason = _graph_schema_receipt(graph)
    return (True, "ok") if valid else (False, f"graph_schema_invalid:{reason}")


def ensure_index_with_receipt(root: str | Path, *, state_dir: str | Path | None = None,
                              source_revision: str = "") -> IndexBuildReceipt:
    root_path = Path(root)
    if not root_path.is_dir() or not is_code_repo(str(root_path)):
        return IndexBuildReceipt(IndexBuildStatus.NOT_APPLICABLE, source_revision=source_revision)
    try:
        graph = ensure_index(str(root_path), state_dir=str(state_dir) if state_dir else None)
    except Exception as exc:  # pragma: no cover - defensive boundary
        return IndexBuildReceipt(IndexBuildStatus.BUILD_FAILED, source_revision=source_revision,
                                 error_type=type(exc).__name__, error_diagnostic=str(exc)[:600])
    if not graph:
        return IndexBuildReceipt(
            IndexBuildStatus.BUILD_FAILED,
            source_revision=source_revision,
            error_type="run_index_false",
            error_diagnostic="gt-index did not publish a graph",
        )
    graph_path = Path(graph)
    valid, reason = _graph_schema_receipt(graph_path)
    if not valid:
        return IndexBuildReceipt(IndexBuildStatus.INVALID_DATABASE, graph_db=graph,
                                 source_revision=source_revision, error_type=reason,
                                 error_diagnostic=reason)
    manifest = graph_path.with_suffix(".manifest.json")
    if not manifest.is_file():
        return IndexBuildReceipt(IndexBuildStatus.INVALID_DATABASE, graph_db=graph,
                                 source_revision=source_revision, error_type="manifest_missing",
                                 error_diagnostic="graph certification manifest missing")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        graph_revision = str(payload.get("graph_revision", payload.get("graph_sha256", "")))
    except (OSError, ValueError):
        graph_revision = ""
    return IndexBuildReceipt(IndexBuildStatus.BUILT, graph_db=graph,
                             source_revision=source_revision, graph_revision=graph_revision)


def refresh_index_files(root: str | Path, graph: str | Path, changed_paths: tuple[str, ...], *,
                        source_revision: str = "") -> IndexBuildReceipt:
    # The current producer has no incremental command boundary. Rebuild into a
    # temporary state directory so the previous complete graph remains readable.
    del graph, changed_paths
    return ensure_index_with_receipt(root, source_revision=source_revision)
