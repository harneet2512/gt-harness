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
import subprocess
import tempfile
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
    str(Path(__file__).resolve().parents[1] / ".gt-index" / "gt-index.exe"),
    "/opt/groundtruth/gt-index/gt-index",
)

_MAX_SCAN_FILES = 50_000  # detection bound; a hit returns immediately

# HAR-6A: the runtime consumes a separately checked-out and built Groundtruth
# producer. The stale compatibility copy under vendor/ is never authoritative.
GROUNDTRUTH_REPOSITORY_URL = "https://github.com/harneet2512/groundtruth.git"
GROUNDTRUTH_PRODUCER_COMMIT = "4967e0080cef47f614b1761a3152b784c0355a30"
GROUNDTRUTH_PRODUCER_SOURCE_TREE = "d6f5ef0177ddc35c4588c919569ee918119fd0f7"
PRODUCER_BUILD_INFO_SCHEMA = "gt-index.build.v1"
PRODUCER_GRAPH_COMPLETION_SCHEMA = "gt-index.graph-completion.v1"
PRODUCER_GRAPH_SCHEMA_VERSION = "v15.2-trust-tier"
PRODUCER_RESOLUTION_CONTRACT = "gt-index.call-resolution.v2"
PRODUCER_REQUIRED_CAPABILITIES = frozenset(
    {
        "atomic_graph_publication",
        "call_resolution_v2",
        "incremental_stale_suppression",
        "parse_failure_accounting",
        "retained_call_candidates",
        "versioned_query_policy",
    }
)


class ProducerContractError(ValueError):
    """The chosen producer is not the pinned, complete graph producer."""


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


def _binary_certification() -> dict[str, str]:
    candidate = _resolved_binary_path()
    path = Path(candidate).resolve() if candidate else None
    if path is None or not path.is_file():
        return {"path": "", "path_sha256": "", "binary_sha256": ""}
    return {
        "path": str(path),
        "path_sha256": hashlib.sha256(str(path).encode("utf-8")).hexdigest(),
        "binary_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _is_vendor_path(path: str | os.PathLike[str]) -> bool:
    normalized = os.path.normcase(os.fspath(path)).replace("\\", "/")
    return any(part == "vendor" for part in normalized.split("/"))


def _run_producer_build_info(binary_path: str) -> dict[str, object]:
    try:
        result = subprocess.run(
            [binary_path, "-build-info"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProducerContractError(
            f"build-info probe failed: {type(exc).__name__}"
        ) from exc
    if result.returncode != 0:
        diagnostic = " ".join((result.stderr or result.stdout).split())[:240]
        raise ProducerContractError(
            f"build-info probe exited {result.returncode}: {diagnostic}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProducerContractError("build-info probe returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ProducerContractError("build-info probe returned a non-object")
    return payload


def _validate_producer_binary(
    binary_path: str | os.PathLike[str],
) -> dict[str, object]:
    """Return exact producer identity or fail closed before indexing."""

    path = Path(binary_path).expanduser().resolve()
    if _is_vendor_path(path):
        raise ProducerContractError("vendor producer is forbidden")
    if not path.is_file():
        raise ProducerContractError(f"producer binary missing: {path}")
    info = _run_producer_build_info(str(path))
    if info.get("schema") != PRODUCER_BUILD_INFO_SCHEMA:
        raise ProducerContractError("producer build-info schema mismatch")
    if info.get("complete") is not True:
        raise ProducerContractError("producer build identity is incomplete")
    if str(info.get("git_commit") or "").lower() != GROUNDTRUTH_PRODUCER_COMMIT:
        raise ProducerContractError("producer source commit mismatch")
    source_fingerprint = str(info.get("source_fingerprint") or "")
    if not source_fingerprint or source_fingerprint == "unknown":
        raise ProducerContractError("producer source fingerprint is incomplete")
    if source_fingerprint != GROUNDTRUTH_PRODUCER_SOURCE_TREE:
        raise ProducerContractError("producer source tree fingerprint mismatch")
    build_tags = {
        tag.strip()
        for tag in str(info.get("build_tags") or "").replace(",", " ").split()
    }
    if "sqlite_fts5" not in build_tags:
        raise ProducerContractError("producer build is missing sqlite_fts5")
    if info.get("graph_schema_version") != PRODUCER_GRAPH_SCHEMA_VERSION:
        raise ProducerContractError("producer graph schema version mismatch")
    capabilities = {str(value) for value in info.get("capabilities", ())}
    missing = PRODUCER_REQUIRED_CAPABILITIES - capabilities
    if missing:
        raise ProducerContractError(
            "producer capabilities missing: " + ",".join(sorted(missing))
        )
    binary_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    if str(info.get("executable_sha256") or "") != binary_sha256:
        raise ProducerContractError("producer executable hash mismatch")
    source_dir = str(os.environ.get("GT_INDEX_SOURCE_DIR") or "").strip()
    if source_dir:
        if _is_vendor_path(source_dir):
            raise ProducerContractError("vendor producer source is forbidden")
        result = subprocess.run(
            ["git", "-C", source_dir, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0 or result.stdout.strip().lower() != GROUNDTRUTH_PRODUCER_COMMIT:
            raise ProducerContractError("producer source checkout commit mismatch")
        tree = subprocess.run(
            ["git", "-C", source_dir, "rev-parse", "HEAD:gt-index"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if tree.returncode != 0 or tree.stdout.strip().lower() != GROUNDTRUTH_PRODUCER_SOURCE_TREE:
            raise ProducerContractError("producer source tree fingerprint mismatch")
        clean = subprocess.run(
            ["git", "-C", source_dir, "status", "--porcelain", "--untracked-files=all", "--", "."],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if clean.returncode != 0 or clean.stdout.strip():
            raise ProducerContractError("producer source checkout is dirty")
    return {
        "path": str(path),
        "path_sha256": hashlib.sha256(str(path).encode("utf-8")).hexdigest(),
        "binary_sha256": binary_sha256,
        **info,
    }


def _validate_graph_completion(
    database: Path, producer: dict[str, object]
) -> dict[str, object]:
    """Validate the producer-bound schema-v2 graph completion receipt."""

    try:
        con = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
        try:
            values = {
                str(key): str(value or "")
                for key, value in con.execute("SELECT key,value FROM project_meta").fetchall()
            }
        finally:
            con.close()
    except (sqlite3.Error, OSError) as exc:
        raise ProducerContractError(
            f"graph completion metadata unavailable: {type(exc).__name__}"
        ) from exc
    if values.get("graph_resolution_schema_version") != "2":
        raise ProducerContractError("graph resolution schema-v2 metadata missing")
    if values.get("graph_resolution_complete") != "1":
        raise ProducerContractError("graph resolution completion metadata missing")
    if values.get("graph_completion_schema") != PRODUCER_GRAPH_COMPLETION_SCHEMA:
        raise ProducerContractError("graph completion schema mismatch")
    receipt = values.get("graph_completion_receipt", "")
    digest = hashlib.sha256(receipt.encode("utf-8")).hexdigest()
    if not receipt or values.get("graph_completion_receipt_sha256") != digest:
        raise ProducerContractError("graph completion receipt hash mismatch")
    try:
        identity = json.loads(receipt)
    except json.JSONDecodeError as exc:
        raise ProducerContractError("graph completion receipt is invalid JSON") from exc
    if not isinstance(identity, dict) or identity.get("complete") is not True:
        raise ProducerContractError("graph completion identity is incomplete")
    if identity.get("schema") != PRODUCER_GRAPH_COMPLETION_SCHEMA:
        raise ProducerContractError("graph completion identity schema mismatch")
    if identity.get("build_info_schema") != PRODUCER_BUILD_INFO_SCHEMA:
        raise ProducerContractError("graph build-info schema mismatch")
    if identity.get("git_commit") != producer.get("git_commit"):
        raise ProducerContractError("graph producer commit mismatch")
    if identity.get("source_fingerprint") != producer.get("source_fingerprint"):
        raise ProducerContractError("graph producer source fingerprint mismatch")
    if identity.get("executable_sha256") != producer.get("executable_sha256"):
        raise ProducerContractError("graph producer executable mismatch")
    if identity.get("graph_schema_version") != PRODUCER_GRAPH_SCHEMA_VERSION:
        raise ProducerContractError("graph schema-v2 producer version mismatch")
    if identity.get("resolution_contract") != PRODUCER_RESOLUTION_CONTRACT:
        raise ProducerContractError("graph resolution contract mismatch")
    required_metadata = (
        "graph_producer_build_id",
        "graph_producer_source_fingerprint",
        "graph_producer_executable_sha256",
        "graph_producer_git_commit",
        "graph_producer_build_time_utc",
        "graph_producer_go_toolchain",
        "graph_producer_build_tags",
    )
    for key in required_metadata:
        if not values.get(key) or values[key] == "unknown":
            raise ProducerContractError(f"graph producer receipt field missing: {key}")
    if values["graph_producer_build_id"] != str(identity.get("build_id") or ""):
        raise ProducerContractError("graph producer build id mismatch")
    if values["graph_producer_source_fingerprint"] != str(identity.get("source_fingerprint") or ""):
        raise ProducerContractError("graph producer source metadata mismatch")
    return identity


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

        binary_path = _resolved_binary_path()
        producer: dict[str, object] | None = None
        if getattr(run_index, "__module__", "") != "groundtruth._binary":
            # Unit tests replace the runtime entry point with a tiny graph
            # fixture; that seam is intentionally outside the producer gate.
            producer = None
        elif binary_path:
            producer = _validate_producer_binary(binary_path)
        else:
            # The installed runtime has no certifiable executable; keep the
            # historical dormant behavior and never invoke an unbound producer.
            return None

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
        if producer is not None:
            _validate_graph_completion(candidate, producer)
        graph_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest()
        manifest = {
            "schema": "gt.graph_certification.v1",
            "repository_root_sha256": hashlib.sha256(
                os.path.realpath(root).encode("utf-8", "surrogatepass")
            ).hexdigest(),
            "graph_sha256": graph_sha256,
            "graph_bytes": candidate.stat().st_size,
            "sqlite_quick_check": "ok",
            **(producer or _binary_certification()),
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
