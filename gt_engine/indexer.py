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
import platform
import shutil
import sqlite3
import subprocess
import tempfile
import time
import sys
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

# The producer is built from this checked-in source only.  A machine-global
# binary can be used solely when an operator explicitly selects it via
# GT_INDEX_BINARY or PATH; it is never silently mixed into a normal run.
_INDEX_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "gt-index-src"
_INDEX_BUILD_TAGS = "sqlite_fts5"
_INDEX_BUILD_CONTRACT = "gt.source_bound_index_binary.v1"
_INTERNAL_BINARY_MARKER = "GT_INDEX_SELECTED_SOURCE_BOUND"
_MAX_BINARY_DIAGNOSTIC = 160

# Bounded, process-local reason for a failed source-bound selection.  It is
# copied into the graph manifest, never used to make a fallback acceptable.
_last_binary_diagnostic = ""

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


def _binary_cache_root() -> Path:
    configured = str(os.environ.get("GT_INDEX_BUILD_CACHE") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        local = str(os.environ.get("LOCALAPPDATA") or "").strip()
        if local:
            return Path(local) / "gt-harness" / "index-binaries"
    xdg = str(os.environ.get("XDG_CACHE_HOME") or "").strip()
    if xdg:
        return Path(xdg).expanduser() / "gt-harness" / "index-binaries"
    return Path.home() / ".cache" / "gt-harness" / "index-binaries"


def _go_toolchain_identity(go_binary: str) -> str:
    environment = os.environ.copy()
    environment["CGO_ENABLED"] = "1"
    try:
        version = subprocess.run(
            [go_binary, "version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10.0,
            check=False, env=environment,
        )
        go_env = subprocess.run(
            [go_binary, "env", "GOOS", "GOARCH", "CC"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10.0, check=False, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if version.returncode or go_env.returncode:
        return ""
    return "\n".join((
        _INDEX_BUILD_CONTRACT, version.stdout.strip(), go_env.stdout.strip(),
        os.name, sys.platform, platform.machine(),
        f"tags={_INDEX_BUILD_TAGS}", "cgo=1", "trimpath=1",
    ))


def _index_source_fingerprint(source_root: Path, toolchain_identity: str) -> str:
    """Hash every producer input and the exact toolchain/build contract."""
    if not toolchain_identity or not source_root.is_dir():
        return ""
    # The Go package can compile cgo and native support files as well as Go
    # sources. Hash the complete checked-in build context so C/C++ headers,
    # linker inputs, and module metadata cannot silently reuse an old binary.
    inputs = sorted(path for path in source_root.rglob("*") if path.is_file())
    if not inputs:
        return ""
    digest = hashlib.sha256()
    digest.update(toolchain_identity.encode("utf-8", "surrogatepass"))
    digest.update(b"\0")
    try:
        for path in inputs:
            digest.update(path.relative_to(source_root).as_posix().encode("utf-8", "surrogatepass"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    except OSError:
        return ""
    return digest.hexdigest()


def _build_source_compatible_binary() -> str:
    """Build and atomically cache a binary bound to source and toolchain."""
    global _last_binary_diagnostic
    _last_binary_diagnostic = ""
    go_binary = shutil.which("go") or ""
    if not go_binary:
        _last_binary_diagnostic = "go_missing"
        return ""
    toolchain = _go_toolchain_identity(go_binary)
    fingerprint = _index_source_fingerprint(_INDEX_SOURCE_ROOT, toolchain)
    if not fingerprint:
        _last_binary_diagnostic = "toolchain_or_source_fingerprint_unavailable"
        return ""
    target_dir = _binary_cache_root() / fingerprint
    target = target_dir / ("gt-index.exe" if os.name == "nt" else "gt-index")
    sidecar = target.with_suffix(target.suffix + ".json")
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        _last_binary_diagnostic = "cache_unwritable"
        return ""
    # A cache hit is valid only if its sidecar binds the bytes to the exact
    # source/toolchain fingerprint.  This detects tampering and stale files.
    if target.is_file() and target.stat().st_size > 0 and sidecar.is_file():
        try:
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            if (
                metadata.get("source_fingerprint") == fingerprint
                and metadata.get("toolchain_identity") == toolchain
                and metadata.get("binary_sha256") == hashlib.sha256(target.read_bytes()).hexdigest()
            ):
                return str(target.resolve())
        except (OSError, ValueError, TypeError):
            _last_binary_diagnostic = "cache_metadata_invalid"
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    metadata_temp: Path | None = None
    environment = os.environ.copy()
    environment["CGO_ENABLED"] = "1"
    try:
        completed = subprocess.run(
            [go_binary, "build", "-trimpath", "-tags", _INDEX_BUILD_TAGS,
             "-o", str(temporary), "./cmd/gt-index/"],
            cwd=_INDEX_SOURCE_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300.0,
            check=False, env=environment,
        )
        if completed.returncode:
            _last_binary_diagnostic = f"build_failed:exit={completed.returncode}"
            return ""
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            _last_binary_diagnostic = "build_failed:artifact_missing"
            return ""
        if os.name != "nt":
            temporary.chmod(0o755)
        os.replace(temporary, target)
        payload = {
            "schema": _INDEX_BUILD_CONTRACT,
            "source_fingerprint": fingerprint,
            "toolchain_identity": toolchain,
            "binary_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
        metadata_temp = sidecar.with_name(f".{sidecar.name}.{os.getpid()}.{time.time_ns()}.tmp")
        metadata_temp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(metadata_temp, sidecar)
        return str(target.resolve())
    except subprocess.TimeoutExpired:
        _last_binary_diagnostic = "build_timeout"
        return ""
    except OSError:
        _last_binary_diagnostic = "build_os_error"
        return ""
    finally:
        temporary.unlink(missing_ok=True)
        if metadata_temp is not None:
            metadata_temp.unlink(missing_ok=True)


def _seed_binary_env() -> str:
    """Select an explicit operator binary or build from checked-in source."""
    global _last_binary_diagnostic
    configured = str(os.environ.get("GT_INDEX_BINARY") or "").strip()
    if configured:
        internal_selected = os.environ.get(_INTERNAL_BINARY_MARKER) == "1"
        if internal_selected:
            try:
                internal_selected = _binary_cache_root().resolve() in Path(configured).resolve().parents
            except OSError:
                internal_selected = False
        if internal_selected:
            # Revalidate an internally selected cache on every call. This is
            # what makes an in-process source edit select a new fingerprint
            # instead of silently reusing the previous binary.
            refreshed = _build_source_compatible_binary()
            if refreshed:
                os.environ["GT_INDEX_BINARY"] = refreshed
                return refreshed
            return ""
        if Path(configured).is_file():
            return configured
        _last_binary_diagnostic = "operator_binary_missing"
        return ""
    discovered = shutil.which("gt-index") or ""
    if discovered:
        return discovered
    built = _build_source_compatible_binary()
    if built:
        os.environ["GT_INDEX_BINARY"] = built
        os.environ[_INTERNAL_BINARY_MARKER] = "1"
    return built


def _binary_certification() -> dict[str, str]:
    candidate = os.environ.get("GT_INDEX_BINARY") or shutil.which("gt-index") or ""
    path = Path(candidate).resolve() if candidate else None
    if path is None or not path.is_file():
        return {
            "path_sha256": "", "binary_sha256": "", "selection": "missing",
            "selection_error": _last_binary_diagnostic[:_MAX_BINARY_DIAGNOSTIC],
        }
    selection = "operator_override" if os.environ.get("GT_INDEX_BINARY") else "path_override"
    try:
        cache_root = _binary_cache_root().resolve()
        if cache_root in path.parents:
            selection = "source_bound_cache"
    except OSError:
        pass
    return {
        "path_sha256": hashlib.sha256(str(path).encode("utf-8")).hexdigest(),
        "binary_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "selection": selection,
        "selection_error": "",
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
        selected_binary = _seed_binary_env()
        # Do not allow groundtruth._binary.find_binary() to silently fall back
        # to its release cache when source-bound selection failed.
        if not selected_binary:
            return None
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
