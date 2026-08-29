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
