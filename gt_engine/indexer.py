"""Auto-detect a code repository and build the gateway's graph.db.

Invokes the resolved Go ``gt-index`` binary behind a bounded child-process
boundary - NEVER the ``groundtruth index`` CLI, which builds the MCP
SymbolStore index.db, a DIFFERENT database the gateway cannot read.

Binary resolution is find_binary()'s: $GT_INDEX_BINARY -> PATH -> local build
-> release download. Because find_binary's "local build" probe is cwd-relative,
this module additionally seeds $GT_INDEX_BINARY from a known local build when
one exists and nothing else resolves.

No source files under the root -> return None: GT stays dormant for non-code
tasks (no harm, no noise).
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from .engine_state import RuntimeLayout
from .graph_coordinator import GraphBuildArtifact
from .repository_identity import RepositoryHistory, repository_history

# Extensions gt-index parses (tree-sitter structural coverage). A root with at
# least one of these is a code repository worth indexing.
SOURCE_EXTS = frozenset({
    ".py", ".pyi", ".go", ".rs", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".rb", ".java", ".kt", ".kts", ".cs", ".php", ".swift", ".scala",
    ".c", ".h", ".cc", ".hh", ".cpp", ".hpp", ".m", ".mm", ".lua", ".ex",
    ".exs", ".erl", ".hs", ".ml", ".clj", ".dart", ".zig", ".sh",
    ".bash", ".cue", ".elm", ".css", ".groovy", ".gradle", ".html", ".htm",
    ".tf", ".hcl", ".md", ".mli", ".proto", ".rake", ".sc", ".sql",
    ".svelte", ".toml", ".yml", ".yaml", ".cxx", ".hxx",
})

# Discovery and resolver inputs consumed by the pinned Go producer. These are
# not source-language extensions, but changing them changes graph semantics.
PRODUCER_CONFIG_NAMES = frozenset({
    ".gitignore", "tsconfig.json", "jsconfig.json", "package.json", "go.mod", "Cargo.toml",
})


def is_producer_input(path: str | Path) -> bool:
    candidate = Path(path)
    return candidate.suffix.lower() in SOURCE_EXTS or candidate.name in PRODUCER_CONFIG_NAMES

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
INDEX_RESOURCE_SCHEMA = "gt.index_resource.v1"
LSP_TERMINAL_SCHEMA = "gt.lsp_promotion_task.v1"
LSP_DERIVATION_SCHEMA = "gt.graph_derivation.v1"
_INDEX_GOMEMLIMIT_BYTES = 3 * 1024**3
_INDEX_RSS_LIMIT_BYTES = 4 * 1024**3
_INDEX_TIMEOUT_SECONDS = 600
_INDEX_MAX_PROCS = 2
# gt-index defaults to -max-files 10000 and silently truncates the walk at
# that point, so a large repository yields a partial graph with no signal.
# The ceiling is stated here instead of inherited.
_INDEX_MAX_FILES = 200_000
# gt-index defaults -workers to NumCPU, which oversubscribes a runtime already
# capped at GOMAXPROCS and raises peak RSS against a fixed memory ceiling.
_INDEX_WORKERS = _INDEX_MAX_PROCS
# Retained so a failing index can be read. gt-index runs with a minimal child
# environment carrying no credentials, and the tail is scrubbed regardless.
_INDEX_STDERR_TAIL_BYTES = 4096
_INDEX_BUILD_ATTEMPTS = 3
_OK = "ok"
_INDEX_TREE_TEARDOWN_SECONDS = 5
# -build-info reads the binary's own bytes and prints one JSON line. It never
# touches the repository, so a probe that has not answered in this long is a
# broken producer, not a slow one.
_INDEX_BUILD_INFO_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class IndexProcessResult:
    success: bool
    status: str
    error_code: str
    exit_code: int | None = None
    peak_rss_bytes: int | None = None
    memory_limit_bytes: int = _INDEX_RSS_LIMIT_BYTES
    elapsed_ms: int = 0
    stdout_bytes: int = 0
    stdout_sha256: str = ""
    stdout_tail: str = ""
    stderr_bytes: int = 0
    stderr_sha256: str = ""
    stderr_tail: str = ""
    cgroup_memory_current_before: int | None = None
    cgroup_memory_current_after: int | None = None
    cgroup_memory_max: int | None = None
    cgroup_memory_peak_after: int | None = None
    cgroup_oom_delta: int = 0
    cgroup_oom_kill_delta: int = 0

    @property
    def memory_evidence(self) -> bool:
        return (
            self.status in {"memory_guard_triggered", "cgroup_oom"}
            and (
                self.status == "memory_guard_triggered"
                or self.cgroup_oom_delta > 0
                or self.cgroup_oom_kill_delta > 0
            )
        )


class _ProcessGroupState(StrEnum):
    EMPTY = "empty"
    LIVE = "live"
    UNKNOWN = "unknown"


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


class SourceDiscoveryIncomplete(RuntimeError):
    pass


def _source_paths(root: Path, excluded_roots: tuple[Path, ...] = (),
                  *, scan_limit: int | None = None):
    def excluded(path: Path) -> bool:
        resolved = path.resolve()
        return any(resolved == target or target in resolved.parents for target in excluded_roots)

    seen = 0
    for directory, dirnames, filenames in os.walk(root):
        base = Path(directory)
        dirnames[:] = sorted(d for d in dirnames
                             if d not in _SKIP_DIRS and not excluded(base / d))
        seen += len(dirnames)
        if scan_limit is not None and seen > scan_limit:
            raise SourceDiscoveryIncomplete("source_discovery_limit_exceeded")
        for filename in sorted(filenames):
            path = base / filename
            if excluded(path):
                continue
            seen += 1
            if scan_limit is not None and seen > scan_limit:
                raise SourceDiscoveryIncomplete("source_discovery_limit_exceeded")
            if is_producer_input(path):
                yield path


def source_manifest_digest(root: str | Path, *, excluded_roots: tuple[Path, ...] = ()) -> str:
    """Hash sorted, length-delimited source path and file-byte identities."""
    root_path = Path(root)
    records: list[tuple[str, int, str]] = []
    for path in _source_paths(root_path, excluded_roots):
        relative = path.relative_to(root_path).as_posix()
        size, digest = _file_identity(path)
        records.append((relative, size, digest))
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
    history: RepositoryHistory = RepositoryHistory()

    def as_dict(self) -> dict:
        return {
            "source_manifest_sha256": self.source_manifest_sha256,
            "producer_binary_sha256": self.producer_binary_sha256,
            "graph_schema_version": self.graph_schema_version,
            "history": self.history.mapping(),
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


def compute_index_reuse_key(
    root: str | Path, *, graph_schema_version: str = GRAPH_SCHEMA_VERSION,
    excluded_roots: tuple[Path, ...] = (),
) -> IndexReuseKey:
    return IndexReuseKey(
        source_manifest_digest(root, excluded_roots=excluded_roots),
        _binary_certification().get("binary_sha256", ""),
        graph_schema_version,
        repository_history(Path(root)),
    )


def _freeze_history(root: Path, frozen: Path, history: RepositoryHistory) -> None:
    if not history.head:
        return
    subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout",
                    "--", str(root.resolve()), str(frozen.resolve())],
                   capture_output=True, check=True, timeout=60)
    subprocess.run(["git", "-C", str(frozen), "update-ref", "--no-deref", "HEAD", history.head],
                   capture_output=True, check=True, timeout=8)
    shallow = frozen / ".git" / "shallow"
    if history.shallow:
        shallow.write_text("\n".join(history.shallow) + "\n", encoding="ascii", newline="\n")
    else:
        shallow.unlink(missing_ok=True)
    # Pinning HEAD does not own the borrowed objects. Repack the requested
    # closure before dropping alternates so source pruning cannot change it.
    subprocess.run(["git", "-C", str(frozen), "repack", "-a", "-d"],
                   capture_output=True, check=True, timeout=60)
    (frozen / ".git" / "objects" / "info" / "alternates").unlink(missing_ok=True)
    subprocess.run(["git", "-C", str(frozen), "fsck", "--connectivity-only", "--no-dangling"],
                   capture_output=True, check=True, timeout=60)
    if repository_history(frozen) != history:
        raise ValueError("repository history changed during snapshot")


def is_code_repo(root: str, *, excluded_roots: tuple[Path, ...] = ()) -> bool:
    """True iff ``root`` contains at least one source file (bounded scan)."""
    try:
        return any(path.suffix.lower() in SOURCE_EXTS for path in _source_paths(
            Path(root), excluded_roots, scan_limit=_MAX_SCAN_FILES,
        ))
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


# The capability a producer must DECLARE before the engine will amend with it.
#
# The flag's presence proves nothing: the certified c3b9f16e accepts -file and
# its amend discarded 177,390 of 181,200 nodes for a twenty-symbol edit, while
# declaring a capability list otherwise identical to a build that amends
# correctly. Nothing observable at the command line separates them, so the
# binary has to say which one it is.
AMEND_CAPABILITY = "incremental_amend_in_place"

# Keyed on (path, content digest): a rebuilt binary at the same path is a
# different producer and must be re-probed.
_AMEND_CAPABILITY_CACHE: dict[tuple[str, str, str], bool] = {}
BATCH_AMEND_CAPABILITY = "batch_parser_node_reuse_v1"


def _producer_supports_incremental_amend() -> bool:
    return _producer_supports_amend_capability(AMEND_CAPABILITY)


def _producer_supports_amend_capability(capability: str) -> bool:
    """Whether the resolved producer declares in-place amend.

    Fails closed. Any probe that cannot be read -- missing binary, non-zero
    exit, unparseable JSON, timeout -- means a full rebuild, which is slow but
    correct, rather than an amend by a binary that may destroy the graph.
    """

    binary = _resolved_binary_path()
    if not binary:
        return False
    certification = _binary_certification()
    key = (binary, certification.get("binary_sha256", ""), capability)
    cached = _AMEND_CAPABILITY_CACHE.get(key)
    if cached is not None:
        return cached
    supported = False
    try:
        probe = subprocess.run(
            [binary, "-build-info"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=_index_child_environment(_INDEX_RSS_LIMIT_BYTES),
            timeout=_INDEX_BUILD_INFO_TIMEOUT_SECONDS,
            check=False,
        )
        if probe.returncode == 0:
            identity = json.loads(probe.stdout.decode("utf-8", "replace"))
            capabilities = identity.get("capabilities")
            supported = (
                isinstance(identity, dict)
                and isinstance(capabilities, list)
                and capability in capabilities
            )
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        supported = False
    _AMEND_CAPABILITY_CACHE[key] = supported
    return supported


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


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sealed_json(path: Path, payload: dict[str, object], digest_field: str) -> str:
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    sealed = dict(payload)
    sealed[digest_field] = digest
    _atomic_write(path, _canonical_json(sealed) + b"\n")
    return digest


def _execution_identity() -> dict[str, str]:
    task_id = os.environ.get("GT_TASK_ID", "").strip()
    product_source_sha = os.environ.get("GT_PRODUCT_SOURCE_SHA", "").strip()
    if not task_id and not product_source_sha:
        return {
            "identity_scope": "local_unbound",
            "task_id": "",
            "product_source_sha": "",
        }
    if not task_id or not re.fullmatch(r"[0-9a-f]{40}", product_source_sha):
        return {
            "identity_scope": "benchmark_invalid",
            "task_id": task_id,
            "product_source_sha": product_source_sha,
        }
    return {
        "identity_scope": "benchmark_bound",
        "task_id": task_id,
        "product_source_sha": product_source_sha,
    }


def _read_integer(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="ascii").strip()
        return None if value == "max" else int(value)
    except (OSError, ValueError):
        return None


def _cgroup_snapshot() -> dict[str, int | None]:
    root = Path("/sys/fs/cgroup")
    events: dict[str, int] = {}
    try:
        for line in (root / "memory.events").read_text(encoding="ascii").splitlines():
            name, value = line.split(maxsplit=1)
            events[name] = int(value)
    except (OSError, ValueError):
        pass
    return {
        "current": _read_integer(root / "memory.current"),
        "max": _read_integer(root / "memory.max"),
        "peak": _read_integer(root / "memory.peak"),
        "oom": events.get("oom"),
        "oom_kill": events.get("oom_kill"),
    }


def _process_rss_bytes(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _file_identity(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


# Extensions the producer registers a language spec for. `gt-index -file`
# ERRORS on an extension it cannot parse rather than skipping it, so a path
# outside this set must never be handed to an amend; it is skipped instead,
# because such a file produces no nodes and so cannot make the graph stale.
#
# Mirrored by hand from vendor/gt-index-src/internal/specs/*.go (the
# `Extensions:` field of each Register call). tests/test_index_incremental.py
# parses those files and fails if this set drifts, so the mirror cannot rot
# into a silent full rebuild on every edit.
INCREMENTAL_AMENDABLE_EXTS = frozenset({
    ".bash", ".c", ".cc", ".cjs", ".cpp", ".cs", ".css", ".cue", ".cxx",
    ".elm", ".ex", ".exs", ".go", ".gradle", ".groovy", ".h", ".hcl",
    ".hpp", ".htm", ".html", ".hxx", ".java", ".js", ".jsx", ".kt", ".kts",
    ".lua", ".md", ".mjs", ".ml", ".mli", ".php", ".proto", ".py", ".rake",
    ".rb", ".rs", ".sc", ".scala", ".sh", ".sql", ".svelte", ".swift",
    ".tf", ".toml", ".ts", ".yaml", ".yml",
})

# How many changed files an amend will absorb before a full rebuild is the
# cheaper answer.
#
# First set to 8 from an isolated producer measurement (12.0s per file against a
# ~115s full index). The live run corrected both numbers. Measured end to end on
# arktype, 2026-09-08, whole builds rather than the producer alone:
#
#     full rebuild          ~78s   (five samples, 75.6-81.6s)
#     one-file amend        ~30s   (four samples after the cold first, 7.0-32.6s)
#     of which fixed cost   ~20-31s  (git history freeze plus re-materialising
#                                     every producer-input file, paid by BOTH
#                                     modes on every build)
#
# So the amend's advantage is a fixed ~48s saving, not a per-file one, and each
# additional file spends against it. Three files is the point where a rebuild
# wins on time -- and a rebuild also restores the co-change, closure and
# candidate layers an amend cannot re-prove, so above the crossover it is better
# on both axes, not merely faster.
#
# This bound will move again when the fixed cost is removed from the amend path;
# it is calibration, not contract.
INCREMENTAL_MAX_DIRTY_PATHS = 3


def _index_command(binary: str, root: str, output: str) -> list[str]:
    """State every budget that shapes the graph rather than inheriting defaults."""

    return [
        binary,
        "-root", root,
        "-output", output,
        "-max-files", str(_INDEX_MAX_FILES),
        "-workers", str(_INDEX_WORKERS),
        "-closure=true",
    ]


def _incremental_index_command(binary: str, root: str, output: str, relpath: str) -> list[str]:
    """Amend one file into an existing graph.

    No -max-files, -workers or -closure: the producer ignores all three in
    incremental mode (it never walks the tree and never recomputes the closure
    sidecar), so passing them would state a bound that is not in force.
    """

    return [binary, "-root", root, "-output", output, "-file", relpath]


def _index_child_environment(memory_limit_bytes: int) -> dict[str, str]:
    # gt-index gets only process-launch essentials. Provider credentials and the
    # rest of the agent environment never enter this child process.
    allowed = (
        "HOME", "LANG", "LC_ALL", "PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"
    )
    child = {name: os.environ[name] for name in allowed if os.environ.get(name)}
    child["GOMAXPROCS"] = str(_INDEX_MAX_PROCS)
    go_limit = min(_INDEX_GOMEMLIMIT_BYTES, memory_limit_bytes * 3 // 4)
    child["GOMEMLIMIT"] = f"{max(48 * 1024 * 1024, go_limit)}B"
    return child


def _index_launch_environment(memory_limit_bytes: int, root: str, log_dir: Path) -> dict[str, str]:
    child = _index_child_environment(memory_limit_bytes)
    state = log_dir.parent.parent if log_dir.parent.name == "revisions" else log_dir
    cache = (state / "parse-cache").resolve()
    workspace = Path(root).resolve()
    if cache != workspace and workspace not in cache.parents:
        child["GT_PARSE_CACHE_ROOT"] = str(cache)
    return child


def _effective_index_memory_limit(snapshot: dict[str, int | None]) -> int:
    cgroup_max = snapshot.get("max")
    if cgroup_max is None:
        return _INDEX_RSS_LIMIT_BYTES
    current = snapshot.get("current")
    if current is None:
        return 0
    headroom = max(0, cgroup_max - current)
    safe_headroom = max(0, headroom - 128 * 1024 * 1024)
    # Leave half of a constrained task cgroup to the runner and its provider
    # transcript and reserve 128 MiB from currently available memory. Tiny or
    # already-pressured cgroups refuse indexing instead of risking the runner.
    return min(_INDEX_RSS_LIMIT_BYTES, cgroup_max // 2, safe_headroom)


_SECRET_RUN = re.compile(r"[A-Za-z0-9_\-]{24,}")
_SECRET_ASSIGN = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Z0-9_]*)\s*[=:]\s*\S+"
)


def scrub_index_stderr(raw: bytes) -> str:
    """Return a readable, secret-free tail of a failing index process.

    An index failure is currently unreadable: only a digest of stderr is kept,
    so a nonzero exit cannot be explained after the fact and the graph cannot
    be repaired. The text is bounded and scrubbed so retaining it does not
    widen the secret boundary.
    """

    text = raw.decode("utf-8", "replace")
    text = _SECRET_ASSIGN.sub(lambda m: f"{m.group(1)}=[redacted]", text)
    text = _SECRET_RUN.sub("[redacted]", text)
    return text


def _drain_stream(stream, result: dict[str, object], prefix: str) -> None:
    digest = hashlib.sha256()
    size = 0
    tail = bytearray()
    try:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
            tail.extend(chunk)
            del tail[:-_INDEX_STDERR_TAIL_BYTES]
    finally:
        try:
            stream.close()
        except OSError:
            pass
    result[f"{prefix}_bytes"] = size
    result[f"{prefix}_sha256"] = digest.hexdigest()
    result[f"{prefix}_tail"] = scrub_index_stderr(bytes(tail))


def _posix_process_group_state(
    process_group_id: int, proc: Path = Path("/proc")
) -> _ProcessGroupState:
    if proc.is_dir():
        try:
            entries = list(proc.iterdir())
        except OSError:
            return _ProcessGroupState.UNKNOWN
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
                state = fields[0]
                process_group = int(fields[2])
            except FileNotFoundError:
                continue
            except (IndexError, OSError, ValueError):
                return _ProcessGroupState.UNKNOWN
            if process_group == process_group_id and state != "Z":
                return _ProcessGroupState.LIVE
        return _ProcessGroupState.EMPTY
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return _ProcessGroupState.EMPTY
    except PermissionError:
        return _ProcessGroupState.LIVE
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return _ProcessGroupState.EMPTY
        if exc.errno == errno.EPERM:
            return _ProcessGroupState.LIVE
        return _ProcessGroupState.UNKNOWN
    return _ProcessGroupState.LIVE


def _kill_index_process_tree(process: subprocess.Popen[bytes]) -> bool:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        # Parent death is not proof of descendant death on Windows. The
        # production boundary refuses launch on this platform until a Job
        # Object provides verifiable kill-on-close semantics.
        return False
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        return _posix_process_group_state(process.pid) is _ProcessGroupState.EMPTY
    deadline = time.monotonic() + _INDEX_TREE_TEARDOWN_SECONDS
    while time.monotonic() < deadline:
        process.poll()
        if _posix_process_group_state(process.pid) is _ProcessGroupState.EMPTY:
            return True
        time.sleep(0.05)
    return False


def _close_pipe_descriptors(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            os.close(stream.fileno())
        except (OSError, ValueError):
            pass


def _has_verified_index_process_tree_guard() -> bool:
    # taskkill is useful as best-effort cleanup, but it cannot prove descendant
    # teardown when the command itself fails. Refuse to start the parser on
    # Windows until it is launched inside a kill-on-close Job Object.
    return os.name != "nt"


def _run_index_bounded(root: str, output: Path, log_dir: Path, *,
                       command_factory: Callable[[str, str, str], list[str]] | None = None,
                       ) -> IndexProcessResult:
    """Run one bounded producer process.

    ``command_factory`` builds the argv from the resolved binary, so binary
    resolution, the memory guard, the timeout and the process-tree teardown
    stay in one place whether the producer is indexing or amending.
    """

    if not _has_verified_index_process_tree_guard():
        return IndexProcessResult(
            False,
            "resource_guard_unavailable",
            "GT_INDEX_RESOURCE_GUARD_UNAVAILABLE",
            memory_limit_bytes=0,
        )
    before = _cgroup_snapshot()
    memory_limit = _effective_index_memory_limit(before)
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    status = "launch_failed"
    error_code = "GT_INDEX_LAUNCH_FAILED"
    exit_code: int | None = None
    peak_rss: int | None = None
    streams: dict[str, object] = {}
    drainers: list[threading.Thread] = []
    try:
        if memory_limit < 64 * 1024 * 1024:
            return IndexProcessResult(
                False,
                "memory_headroom_refused",
                "GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT",
                memory_limit_bytes=memory_limit,
                cgroup_memory_current_before=before.get("current"),
                cgroup_memory_max=before.get("max"),
            )
        binary = _resolved_binary_path()
        if not binary:
            return IndexProcessResult(False, status, error_code, memory_limit_bytes=memory_limit)
        # Resolved at call time, never bound as a default, so a test double
        # for _index_command still intercepts the full-index path.
        build_argv = command_factory or _index_command
        process = subprocess.Popen(
            build_argv(binary, root, str(output)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_index_launch_environment(memory_limit, root, log_dir),
            start_new_session=os.name != "nt",
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
        )
        assert process.stdout is not None and process.stderr is not None
        drainers = [
            threading.Thread(
                target=_drain_stream, args=(process.stdout, streams, "stdout"), daemon=True
            ),
            threading.Thread(
                target=_drain_stream, args=(process.stderr, streams, "stderr"), daemon=True
            ),
        ]
        for drainer in drainers:
            drainer.start()
        while process.poll() is None:
            rss = _process_rss_bytes(process.pid)
            if rss is not None:
                peak_rss = max(peak_rss or 0, rss)
            if rss is not None and rss > memory_limit:
                status = "memory_guard_triggered"
                error_code = "GT_INDEX_MEMORY_GUARD_TRIGGERED"
                _kill_index_process_tree(process)
                break
            if time.monotonic() - started > _INDEX_TIMEOUT_SECONDS:
                status = "timeout"
                error_code = "GT_INDEX_TIMEOUT"
                _kill_index_process_tree(process)
                break
            time.sleep(0.05)
        exit_code = process.wait(timeout=10)
        # A successful group leader is not proof that its descendants exited.
        # Tear down the session unconditionally before accepting completion;
        # redirected descendants otherwise evade the pipe-drainer check.
        teardown_verified = _kill_index_process_tree(process)
        for drainer in drainers:
            drainer.join(timeout=1)
        if any(drainer.is_alive() for drainer in drainers):
            _kill_index_process_tree(process)
            _close_pipe_descriptors(process)
            for drainer in drainers:
                drainer.join(timeout=1)
        if any(drainer.is_alive() for drainer in drainers):
            raise subprocess.SubprocessError("gt-index output drainer did not finish")
        after = _cgroup_snapshot()
        oom_delta = max(0, (after.get("oom") or 0) - (before.get("oom") or 0))
        oom_kill_delta = max(
            0, (after.get("oom_kill") or 0) - (before.get("oom_kill") or 0)
        )
        if status not in {"memory_guard_triggered", "timeout"}:
            if exit_code in {-9, 137} and (oom_delta or oom_kill_delta):
                status = "cgroup_oom"
                error_code = "GT_INDEX_CGROUP_OOM"
            elif exit_code == 0:
                status = "completed"
                error_code = ""
            elif exit_code in {-9, 137}:
                status = "signal_9_unattributed"
                error_code = "GT_INDEX_EXIT_137_UNATTRIBUTED"
            else:
                status = "nonzero_exit"
                error_code = "GT_INDEX_PROCESS_FAILED"
        if not teardown_verified:
            status = "process_tree_unverified"
            error_code = "GT_INDEX_PROCESS_TREE_UNVERIFIED"
        return IndexProcessResult(
            success=status == "completed",
            status=status,
            error_code=error_code,
            exit_code=exit_code,
            peak_rss_bytes=peak_rss,
            memory_limit_bytes=memory_limit,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            stdout_bytes=int(streams.get("stdout_bytes", 0)),
            stdout_sha256=str(streams.get("stdout_sha256", "")),
            stdout_tail=str(streams.get("stdout_tail", "")),
            stderr_bytes=int(streams.get("stderr_bytes", 0)),
            stderr_sha256=str(streams.get("stderr_sha256", "")),
            stderr_tail=str(streams.get("stderr_tail", "")),
            cgroup_memory_current_before=before.get("current"),
            cgroup_memory_current_after=after.get("current"),
            cgroup_memory_max=before.get("max"),
            cgroup_memory_peak_after=after.get("peak"),
            cgroup_oom_delta=oom_delta,
            cgroup_oom_kill_delta=oom_kill_delta,
        )
    except (OSError, subprocess.SubprocessError):
        if process is not None:
            if not _kill_index_process_tree(process):
                status = "process_tree_unverified"
                error_code = "GT_INDEX_PROCESS_TREE_UNVERIFIED"
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            _close_pipe_descriptors(process)
        for drainer in drainers:
            drainer.join(timeout=1)
        return IndexProcessResult(
            False,
            status,
            error_code,
            exit_code=exit_code,
            peak_rss_bytes=peak_rss,
            memory_limit_bytes=memory_limit,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )


def _build_index_with_attempts(
    root: str, output: Path, log_dir: Path
) -> tuple[IndexProcessResult, tuple[str, ...]]:
    """Build the graph, retrying a failed attempt before giving it up.

    A single failure previously cost the run its entire graph: the index ran
    once and a nonzero exit returned no database, so every graph-dependent
    capability degraded for the whole task. Attempts are bounded and each one
    is recorded, so a transient failure is survived and a deterministic one is
    visible as the same failure repeating rather than inferred from a single
    sample.
    """

    attempts: list[str] = []
    result: IndexProcessResult | None = None
    for attempt in range(1, _INDEX_BUILD_ATTEMPTS + 1):
        result = _run_index_bounded(root, output, log_dir)
        attempts.append(f"{attempt}:{result.status}:{result.error_code or _OK}")
        if result.success:
            break
        # A partial database from a failed attempt must never be reused.
        output.unlink(missing_ok=True)
    assert result is not None
    return result, tuple(attempts)

def _graph_scale(database: Path) -> tuple[int, int]:
    """Return (indexed files, indexed nodes) for a published graph.

    These answer different questions and must not be conflated. A repository
    that contains source has files to index, and a graph built from it owes
    nodes: files present with no nodes is a broken index, not an empty one. A
    task that starts with no source has nothing to index yet -- the graph fills
    as the agent creates files, and each edit boundary reindexes -- so an empty
    graph there is a legitimate wait state rather than a failure.

    Both counts fail closed to zero. An uncountable graph must not manufacture
    an obligation the run cannot discharge; the certification checks already
    reject such a graph on their own terms.
    """

    try:
        con = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    except (sqlite3.Error, OSError):
        return 0, 0
    try:
        files = con.execute("SELECT COUNT(*) FROM file_hashes").fetchone()
    except sqlite3.Error:
        files = None
    try:
        nodes = con.execute("SELECT COUNT(*) FROM nodes").fetchone()
    except sqlite3.Error:
        nodes = None
    finally:
        con.close()
    return (int(files[0]) if files else 0, int(nodes[0]) if nodes else 0)

def start_lsp_promotion(database: Path, root: str | Path) -> dict[str, object]:
    """Report whether progressive LSP promotion can be safely scheduled.

    The producer ships a complete promotion subsystem whose own design note
    states the intent: gt-index publishes a usable graph immediately, then
    language servers promote edges in batches so resolution quality rises while
    the agent is already working. It was only ever started from the MCP server,
    so the benchmark harness published a graph and left the highest-precision
    edge tier -- lsp and lsp_verified, both admitted by the closure -- empty.

    The current producer lacks a graph-bound scheduling receipt, so available
    servers are reported but not launched. Nothing here may mutate or fail an
    index that has already succeeded.
    """

    try:
        from groundtruth.lsp.background_promotion import detect_available_servers
    except Exception:  # noqa: BLE001 - producer package absent is not an index failure
        return {"status": "promotion_unavailable", "servers": []}

    try:
        servers = sorted(detect_available_servers())
    except Exception:  # noqa: BLE001 - discovery is advisory
        servers = []

    if not servers:
        # Nothing on PATH to promote with. Recorded rather than inferred: a
        # silent no-op is indistinguishable from success in stored evidence,
        # which is how LSP stayed nominally on while contributing nothing.
        return {"status": "promotion_no_servers", "servers": []}

    # The pinned producer exposes neither a task handle nor a graph-bound
    # scheduling receipt. Its process-global statistics can describe a prior
    # graph and are updated only after an async coroutine starts. Calling it
    # here would therefore mutate the graph without giving the harness a
    # truthful way to attest which graph was promoted. Fail closed until the
    # producer returns a graph/revision-bound scheduling receipt.
    return {
        "status": "promotion_not_scheduled",
        "servers": servers,
        "reason": "producer_scheduler_receipt_unavailable",
    }


def _write_index_evidence(
    path: Path, *, root: str, result: IndexProcessResult, reuse_key: IndexReuseKey,
    identity: dict[str, str], attempts: tuple[str, ...] = (),
) -> str:
    payload: dict[str, object] = {
        "schema": INDEX_RESOURCE_SCHEMA,
        **identity,
        "repository_root_sha256": hashlib.sha256(
            os.path.realpath(root).encode("utf-8", "surrogatepass")
        ).hexdigest(),
        "source_manifest_sha256": reuse_key.source_manifest_sha256,
        "producer_binary_sha256": reuse_key.producer_binary_sha256,
        "status": result.status,
        "error_code": result.error_code,
        "exit_code": result.exit_code,
        "memory_evidence": result.memory_evidence,
        "memory_limit_bytes": result.memory_limit_bytes,
        "peak_rss_bytes": result.peak_rss_bytes,
        "elapsed_ms": result.elapsed_ms,
        "stdout_bytes": result.stdout_bytes,
        "stdout_sha256": result.stdout_sha256,
        "stderr_bytes": result.stderr_bytes,
        "stderr_sha256": result.stderr_sha256,
        "stderr_tail": result.stderr_tail,
        "build_attempts": list(attempts),
        "build_attempt_count": len(attempts),
        "cgroup_memory_current_before": result.cgroup_memory_current_before,
        "cgroup_memory_current_after": result.cgroup_memory_current_after,
        "cgroup_memory_max": result.cgroup_memory_max,
        "cgroup_memory_peak_after": result.cgroup_memory_peak_after,
        "cgroup_oom_delta": result.cgroup_oom_delta,
        "cgroup_oom_kill_delta": result.cgroup_oom_kill_delta,
    }
    return _sealed_json(path, payload, "evidence_sha256")


def _write_graph_failure(
    gt_dir: Path,
    *,
    root: str,
    reuse_key: IndexReuseKey,
    error_code: str,
    evidence_path: Path,
    identity: dict[str, str],
) -> None:
    failure_payload: dict[str, object] = {
        "schema": "gt.graph_failure.v1",
        **identity,
        "error_code": error_code,
        "repository_root_sha256": hashlib.sha256(
            os.path.realpath(root).encode("utf-8", "surrogatepass")
        ).hexdigest(),
        "source_manifest_sha256": reuse_key.source_manifest_sha256,
        "producer_binary_sha256": reuse_key.producer_binary_sha256,
        "resource_evidence_path": evidence_path.name,
        "resource_evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
    }
    _sealed_json(gt_dir / "graph.failure.json", failure_payload, "manifest_sha256")


def _publish_graph_failure(
    gt_dir: Path,
    *,
    root: str,
    reuse_key: IndexReuseKey,
    error_code: str,
    staged_evidence: Path,
    identity: dict[str, str],
) -> None:
    evidence_path = gt_dir / "index-failure-resource.json"
    failure_path = gt_dir / "graph.failure.json"
    evidence_backup = gt_dir / ".index-failure.previous.json"
    failure_backup = gt_dir / ".graph-failure.previous.json"
    had_evidence = evidence_path.is_file()
    had_failure = failure_path.is_file()
    if had_evidence:
        shutil.copyfile(evidence_path, evidence_backup)
    if had_failure:
        shutil.copyfile(failure_path, failure_backup)
    try:
        os.replace(staged_evidence, evidence_path)
        _write_graph_failure(
            gt_dir, root=root, reuse_key=reuse_key, error_code=error_code,
            evidence_path=evidence_path, identity=identity,
        )
    except Exception:
        if had_evidence and evidence_backup.is_file():
            os.replace(evidence_backup, evidence_path)
        else:
            evidence_path.unlink(missing_ok=True)
        if had_failure and failure_backup.is_file():
            os.replace(failure_backup, failure_path)
        else:
            failure_path.unlink(missing_ok=True)
        raise
    finally:
        staged_evidence.unlink(missing_ok=True)
        evidence_backup.unlink(missing_ok=True)
        failure_backup.unlink(missing_ok=True)


# How many revision directories to keep beside the live one. Each holds a
# graph.db and its contract sidecar - 912MB and 65MB on the gate task - so the
# cost of keeping them all is ~1GB per rebuild.
#
# Nothing pruned them because until ad58b7d9 nothing ever produced a second
# revision: _frozen_graph_input raised on all 600 attempts, so every run had
# exactly one. A run that rebuilds after each of 105 edits writes ~100GB and
# fills the runner, and disk exhaustion does not present as disk exhaustion --
# it surfaces as whatever writes next, which is how it would have been read as
# a fresh unrelated defect.
#
# One superseded revision is retained because certification reads lineage
# through the graph it was built from; zero would break that, and more buys
# nothing a rebuild cannot reproduce.
RETAINED_SUPERSEDED_REVISIONS = 1


def _referenced_revisions(parent: Path) -> set[Path]:
    """Revisions some manifest still points at, which must never be pruned.

    Certification is one-level -- certify_lsp_candidate says so and resolves
    base_graph/base_manifest/base_resource/terminal_receipt against the
    revisions root without recursing. But certification TIME is not the
    artifact's lifetime: a published enrichment's manifest records portable
    references to its base (:1676-1686), and attest, gt_audit or any later
    re-read needs those files to still be there. Count-based retention cannot
    express "this old one is load-bearing", so read the references instead of
    guessing a depth.
    """
    referenced: set[Path] = set()
    for manifest in parent.glob("*/graph.manifest.json"):
        try:
            derivation = json.loads(manifest.read_text(encoding="utf-8")).get("derivation")
        except (OSError, ValueError):
            # Unreadable means unknown, and unknown must not authorise a delete.
            referenced.add(manifest.parent.resolve())
            continue
        if not isinstance(derivation, dict):
            continue
        for key in ("base_graph", "base_manifest", "base_resource", "terminal_receipt"):
            reference = str(derivation.get(key) or "")
            if not reference:
                continue
            candidate = (parent / reference).resolve() if "/" in reference else None
            if candidate is None:
                continue
            for ancestor in (candidate, *candidate.parents):
                if ancestor.parent == parent.resolve():
                    referenced.add(ancestor)
                    break
    return referenced


def _pinned_revisions(parent: Path) -> set[Path]:
    """Revisions a delivered artifact still has to be certified against.

    ``_referenced_revisions`` reads manifest derivations, which covers artifacts
    that reference each other. It cannot see the other kind of load-bearing
    reference: a semantic-localization advisory that was DELIVERED to the model
    records the graph revision it was ranked from, and ``verify_runtime_receipt``
    later demands exactly one surviving certified graph matching it.

    Measured: three localization deliveries all named the task-start revision,
    thirty-odd publications followed, retention evicted it, and receipt issuance
    raised ``semantic_localization_certified_graph_missing`` before any receipt
    existed -- 12 of 20 tasks on one run, with no product row at all. The writer
    of the advisory pins its revision; this is retention honouring the pin.
    """
    pinned: set[Path] = set()
    for marker in parent.glob("*/pinned.json"):
        pinned.add(marker.parent.resolve())
    return pinned


def _prune_superseded_revisions(live: Path) -> None:
    """Drop superseded sibling revisions that nothing still references."""
    parent = live.parent
    if parent.name != "revisions":
        return  # not the layout-bound scheme; nothing here is ours to remove
    try:
        siblings = [
            path for path in parent.iterdir()
            if path.is_dir() and path.resolve() != live.resolve()
        ]
    except OSError:
        return
    protected = _referenced_revisions(parent) | _pinned_revisions(parent)
    siblings = [path for path in siblings if path.resolve() not in protected]
    # Order by the GRAPH's mtime, not the directory's. A directory's mtime moves
    # whenever an entry is added or removed - lsp-promotion.json is written into
    # a revision after publication, and candidate cleanup touches it later - so
    # directory order can rank an older revision above a newer one and delete
    # the wrong thing. That is the proxy-for-property shape, in the one change
    # here that destroys data.
    def _published_at(path: Path) -> float:
        graph = path / "graph.db"
        try:
            return graph.stat().st_mtime
        except OSError:
            return 0.0

    siblings.sort(key=_published_at, reverse=True)
    for stale in siblings[RETAINED_SUPERSEDED_REVISIONS:]:
        # Rename out of the scheme before deleting. rmtree(ignore_errors=True)
        # can leave a revision holding graph.manifest.json without graph.db, and
        # a partial like that still answers every rglob scan - _single_optional
        # counts it and a manifest can digest-match a graph that is gone. Clean
        # absence beats a half-present revision; an interrupted rename leaves a
        # directory outside the scheme, which is discoverable and harmless.
        try:
            condemned = stale.with_name(f".pruned-{stale.name}")
            os.replace(stale, condemned)
        except OSError:
            continue
        shutil.rmtree(condemned, ignore_errors=True)


def _revision_identity(reuse_key: IndexReuseKey) -> str:
    """Directory identity for a published graph.

    The reuse key answers one question: was this built from the same source by
    the same producer. Certification asks a second one it does not cover -- that
    the execution identity sealed into the artifact is the identity now asking
    for it. A directory keyed on the reuse key alone therefore has a state no
    run can leave: the artifact fails certification, and being immutable cannot
    be rebuilt over, so every later run under a different identity refuses with
    a graph sitting right there. Keying the directory on exactly what
    certification enforces removes the state rather than handling it -- a
    different identity is a different artifact, so it gets a different path,
    and immutability holds without ever blocking a rebuild.
    """

    return hashlib.sha256(_canonical_json({
        "index_reuse_key_sha256": reuse_key.digest,
        "task_id": os.environ.get("GT_TASK_ID", ""),
        "product_source_sha": os.environ.get("GT_PRODUCT_SOURCE_SHA", ""),
    })).hexdigest()


def _graph_state_dir(root: str | Path, state_dir: str | Path | None,
                     layout: RuntimeLayout | None = None,
                     reuse_key: IndexReuseKey | None = None) -> Path:
    if layout is not None:
        key = reuse_key or compute_index_reuse_key(root, excluded_roots=layout.excluded_roots)
        return layout.graph_root / "revisions" / _revision_identity(key)
    external = str(state_dir or os.environ.get("GT_STATE_DIR") or "").strip()
    if external:
        root_key = hashlib.sha256(
            os.path.realpath(root).encode("utf-8", "surrogatepass")
        ).hexdigest()[:16]
        return Path(external) / root_key
    return Path(root) / ".gt"


def _read_sealed_json(path: Path, digest_field: str) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        supplied = payload.pop(digest_field, None)
        calculated = hashlib.sha256(_canonical_json(payload)).hexdigest()
        return payload if supplied == calculated else None
    except (OSError, ValueError, TypeError):
        return None


def _publish_candidate(
    candidate: Path, *, root: str, logical_root: str, gt_dir: Path, db: Path,
    reuse_key: IndexReuseKey, identity: dict[str, str],
    process_result: IndexProcessResult, build_attempts: tuple[str, ...],
    excluded_roots: tuple[Path, ...] = (), diagnostics: list[str] | None = None,
    build_mode: str = "full", parent_graph_sha256: str = "",
    amended_paths: tuple[str, ...] = (),
) -> str | None:
    """Certify and publish a staged graph, or record why it could not be.

    Shared by the full index and the incremental amend so both produce the same
    sealed evidence, the same certification manifest and the same atomic
    three-file swap. An amend that published through a second, similar path
    would be a graph whose certification means something slightly different
    from every other graph's, which is the kind of difference nobody reads
    until it is load-bearing.

    ``build_mode``, ``parent_graph_sha256`` and ``amended_paths`` are recorded
    in the manifest so a published graph says how it was made. They are not
    inputs to certification: certify_graph_artifact checks named keys, so a
    reader that predates them is unaffected.
    """

    evidence_path = candidate.with_suffix(".resource.json")
    _write_index_evidence(
        evidence_path, root=logical_root, result=process_result,
        reuse_key=reuse_key, identity=identity, attempts=build_attempts,
    )
    evidence_sha256 = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    failure_manifest = gt_dir / "graph.failure.json"
    if not process_result.success:
        _publish_graph_failure(
            gt_dir,
            root=logical_root,
            reuse_key=reuse_key,
            error_code=process_result.error_code,
            staged_evidence=evidence_path,
            identity=identity,
        )
        if diagnostics is not None:
            diagnostics.append(process_result.error_code)
        candidate.unlink(missing_ok=True)
        return None
    if not candidate.is_file():
        _write_index_evidence(
            evidence_path, root=logical_root,
            result=replace(
                process_result, success=False, status="output_missing",
                error_code="GT_INDEX_OUTPUT_MISSING",
            ),
            reuse_key=reuse_key, identity=identity,
        )
        _publish_graph_failure(
            gt_dir,
            root=logical_root,
            reuse_key=reuse_key,
            error_code="GT_INDEX_OUTPUT_MISSING",
            staged_evidence=evidence_path,
            identity=identity,
        )
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
        _write_index_evidence(
            evidence_path, root=logical_root,
            result=replace(
                process_result, success=False, status="output_invalid",
                error_code="GT_INDEX_OUTPUT_INVALID",
            ),
            reuse_key=reuse_key, identity=identity,
        )
        _publish_graph_failure(
            gt_dir,
            root=logical_root,
            reuse_key=reuse_key,
            error_code="GT_INDEX_OUTPUT_INVALID",
            staged_evidence=evidence_path,
            identity=identity,
        )
        if diagnostics is not None:
            diagnostics.append("GT_INDEX_OUTPUT_INVALID")
        candidate.unlink(missing_ok=True)
        return None
    if quick_check.lower() != "ok":
        _write_index_evidence(
            evidence_path, root=logical_root,
            result=replace(
                process_result, success=False, status="output_invalid",
                error_code="GT_INDEX_OUTPUT_INVALID",
            ),
            reuse_key=reuse_key, identity=identity,
        )
        _publish_graph_failure(
            gt_dir,
            root=logical_root,
            reuse_key=reuse_key,
            error_code="GT_INDEX_OUTPUT_INVALID",
            staged_evidence=evidence_path,
            identity=identity,
        )
        if diagnostics is not None:
            diagnostics.append("GT_INDEX_OUTPUT_INVALID")
        candidate.unlink(missing_ok=True)
        return None
    if compute_index_reuse_key(root, excluded_roots=excluded_roots) != reuse_key:
        candidate.unlink(missing_ok=True)
        raise ValueError("producer input superseded before publication")
    graph_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest()
    manifest = {
        "schema": "gt.graph_certification.v1",
        **identity,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "index_reuse_key": reuse_key.as_dict(),
        "index_reuse_key_sha256": reuse_key.digest,
        "source_manifest_sha256": reuse_key.source_manifest_sha256,
        "repository_root_sha256": hashlib.sha256(
            os.path.realpath(logical_root).encode("utf-8", "surrogatepass")
        ).hexdigest(),
        "graph_sha256": graph_sha256,
        "graph_bytes": candidate.stat().st_size,
        "sqlite_quick_check": "ok",
        "indexed_file_count": _graph_scale(candidate)[0],
        "indexed_node_count": _graph_scale(candidate)[1],
        **_graph_phase_metadata(candidate),
        "index_resource_sha256": evidence_sha256,
        **_binary_certification(),
    }
    manifest["binary_certified"] = bool(manifest["binary_sha256"])
    # How this graph was produced. A full index and an amend of the same source
    # can hold the same rows and are not the same claim: the amend inherits
    # every row its parent proved and re-derives only the named paths.
    manifest["build_mode"] = build_mode
    manifest["parent_graph_sha256"] = parent_graph_sha256
    manifest["amended_paths"] = list(amended_paths)
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    backup = gt_dir / ".graph.previous.db"
    manifest_path = db.with_suffix(".manifest.json")
    manifest_backup = gt_dir / ".graph.previous.manifest.json"
    canonical_evidence = gt_dir / "index-resource.json"
    evidence_backup = gt_dir / ".index-resource.previous.json"
    had_previous = db.is_file()
    had_manifest = manifest_path.is_file()
    had_evidence = canonical_evidence.is_file()
    if had_previous:
        shutil.copyfile(db, backup)
    if had_manifest:
        shutil.copyfile(manifest_path, manifest_backup)
    if had_evidence:
        shutil.copyfile(canonical_evidence, evidence_backup)
    try:
        # All readers enter through ensure_index's lock. Publish the three
        # staged files as one locked transaction and restore the prior set
        # if any swap fails.
        os.replace(candidate, db)
        os.replace(evidence_path, canonical_evidence)
        _atomic_write(manifest_path, manifest_bytes)
    except Exception:
        if had_previous and backup.is_file():
            os.replace(backup, db)
        else:
            db.unlink(missing_ok=True)
        if had_evidence and evidence_backup.is_file():
            os.replace(evidence_backup, canonical_evidence)
        else:
            canonical_evidence.unlink(missing_ok=True)
        if had_manifest and manifest_backup.is_file():
            os.replace(manifest_backup, manifest_path)
        else:
            manifest_path.unlink(missing_ok=True)
        raise
    finally:
        candidate.unlink(missing_ok=True)
        evidence_path.unlink(missing_ok=True)
        backup.unlink(missing_ok=True)
        evidence_backup.unlink(missing_ok=True)
        manifest_backup.unlink(missing_ok=True)
    failure_manifest.unlink(missing_ok=True)
    (gt_dir / "index-failure-resource.json").unlink(missing_ok=True)
    _prune_superseded_revisions(gt_dir)
    # The graph is published and usable from here; promotion only improves it.
    promotion = start_lsp_promotion(db, root)
    # Sealed beside the graph: an unrecorded promotion cannot be told apart
    # from one that never ran, and that is exactly how the highest-precision
    # edge tier stayed empty without anyone being able to see it.
    _sealed_json(
        gt_dir / "lsp-promotion.json",
        {
            "schema": "gt.lsp_promotion.v1",
            **identity,
            "graph_sha256": graph_sha256,
            "status": promotion["status"],
            "servers_detected": promotion["servers"],
            "server_count": len(promotion["servers"]),
        },
        "promotion_sha256",
    )
    return str(db)


def _ensure_index_unlocked(root: str, *, state_dir: str | None = None,
                           excluded_roots: tuple[Path, ...] = (),
                           layout: RuntimeLayout | None = None,
                           diagnostics: list[str] | None = None) -> str | None:
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
        if not is_code_repo(root, **({"excluded_roots": excluded_roots} if excluded_roots else {})):
            return None  # non-code task: GT dormant
        _seed_binary_env()

        logical_root = str(layout.workspace) if layout is not None else str(root)
        reuse_key = compute_index_reuse_key(root, excluded_roots=excluded_roots)
        gt_dir = _graph_state_dir(root, state_dir, layout, reuse_key)
        if gt_dir != Path(root) / ".gt":
            gt_dir.mkdir(parents=True, exist_ok=True)
        else:
            gt_dir.mkdir(exist_ok=True)
            ignore = gt_dir / ".gitignore"
            if not ignore.exists():
                ignore.write_text("*\n", encoding="utf-8")
        db = gt_dir / "graph.db"
        identity = _execution_identity()
        if identity["identity_scope"] == "benchmark_invalid":
            staged_evidence = gt_dir / ".index-identity-resource.json"
            refusal = IndexProcessResult(
                False, "identity_refused", "GT_INDEX_IDENTITY_INVALID"
            )
            _write_index_evidence(
                staged_evidence, root=logical_root, result=refusal,
                reuse_key=reuse_key, identity=identity,
            )
            _publish_graph_failure(
                gt_dir, root=logical_root, reuse_key=reuse_key,
                error_code=refusal.error_code, staged_evidence=staged_evidence,
                identity=identity,
            )
            if diagnostics is not None:
                diagnostics.append(refusal.error_code)
            return None
        existing_manifest = db.with_suffix(".manifest.json")
        if db.is_file() and existing_manifest.is_file():
            try:
                manifest = json.loads(existing_manifest.read_text(encoding="utf-8"))
                if (
                    manifest.get("index_reuse_key") == reuse_key.as_dict()
                    and manifest.get("index_reuse_key_sha256") == reuse_key.digest
                ):
                    valid, reason = _certify_published_graph(
                        db, existing_manifest, expected_root=Path(logical_root),
                        expected_binary_sha256=reuse_key.producer_binary_sha256,
                    )
                    if not valid and diagnostics is not None:
                        diagnostics.append(f"published_graph_uncertifiable:{reason}")
                    if valid:
                        (gt_dir / "graph.failure.json").unlink(missing_ok=True)
                        (gt_dir / "index-failure-resource.json").unlink(missing_ok=True)
                        return str(db)
            except (OSError, ValueError, TypeError):
                pass
        if layout is not None and db.exists():
            raise ValueError("immutable_graph_artifact_invalid")
        with tempfile.NamedTemporaryFile(
            dir=gt_dir, prefix=".graph.", suffix=".db", delete=False
        ) as handle:
            candidate = Path(handle.name)
        candidate.unlink(missing_ok=True)
        if excluded_roots:
            # The producer receives the same filtered, frozen input that was
            # hashed. Filtering only the reuse key would certify hidden state
            # as source, and rewriting the workspace's .gitignore is an edit.
            with tempfile.TemporaryDirectory(prefix="gt-index-input-") as staging:
                frozen = Path(staging)
                _freeze_history(Path(root), frozen, reuse_key.history)
                for source in _source_paths(Path(root), excluded_roots):
                    target = frozen / source.relative_to(root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                if source_manifest_digest(frozen) != reuse_key.source_manifest_sha256:
                    raise ValueError("producer input changed during snapshot")
                process_result, build_attempts = _build_index_with_attempts(
                    str(frozen), candidate, gt_dir,
                )
        else:
            process_result, build_attempts = _build_index_with_attempts(
                str(root), candidate, gt_dir,
            )
        published = _publish_candidate(
            candidate, root=root, logical_root=logical_root, gt_dir=gt_dir, db=db,
            reuse_key=reuse_key, identity=identity, process_result=process_result,
            build_attempts=build_attempts, excluded_roots=excluded_roots,
            diagnostics=diagnostics,
        )
        return published
    except Exception as exc:  # noqa: BLE001 - indexing failure means GT dormant, never a crash
        if diagnostics is not None:
            diagnostics.append(f"{type(exc).__name__}: {exc}")
        return None


class BenchmarkGraphRequired(RuntimeError):
    """A benchmark run reached provider work without the graph it measures.

    Outside a benchmark, a missing graph is a degraded mode: the assistant
    continues without repository intelligence and that is deliberate. Inside
    one it is not a mode at all. The graph is the product under measurement,
    so a task that proceeds without it does not produce a weaker result -- it
    produces a result about nothing, at the full price of the provider calls
    it spends getting there.

    Run 33708231670 is the case in point: 160 provider calls, a failed index,
    and not one delivered evidence type that needed a graph. Failing here
    costs one container start. Not failing here costs the run and yields a
    number that reads like a measurement of GT.
    """


def _parse_incremental_result(stdout_tail: str) -> dict[str, object]:
    """Read the producer's one-line amend result.

    The counts are the whole point of reading it. A reindex that reports only
    that it ran cannot be told apart from one that replaced the graph -- which
    is the state the amend fix exists to leave behind -- so a result line that
    cannot be parsed is reported as unparsed rather than assumed benign.
    """

    for line in reversed(stdout_tail.strip().splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{") or not candidate.endswith("}"):
            continue
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict) and (
            "file" in parsed or (parsed.get("build_mode") == "batch" and "files" in parsed)
        ):
            return parsed
    return {"result_line": "unparsed"}


def _amendable_paths(root: Path, changed_paths: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
    """Split the changed set into paths to amend, or name why none can be.

    Two outcomes, kept apart on purpose. A path the producer has no parser for
    is SKIPPED: it contributes no nodes, so it cannot have made the graph stale.
    A producer config file REFUSES the whole amend: it changes how every other
    file resolves, so re-deriving one file would leave the rest of the graph
    describing the old configuration.

    A path that no longer exists on disk is passed through, not refused. The
    producer treats a missing file as a deletion and reconciles its node set to
    empty. Refusing here cost more than anything else the amend declined: on the
    2026-09-08 run, deletions were five of eleven full rebuilds, because the
    agent repeatedly created scratch test files and removed them. A rename
    arrives as a deletion of the old path beside a creation of the new one, so
    both halves are now amendable in the same pass.
    """

    amendable: list[str] = []
    for raw in changed_paths:
        relative = str(raw).replace("\\", "/").strip()
        if not relative:
            continue
        if Path(relative).name in PRODUCER_CONFIG_NAMES:
            return (), f"config_input_changed:{Path(relative).name}"
        if Path(relative).suffix.lower() not in INCREMENTAL_AMENDABLE_EXTS:
            continue
        amendable.append(relative)
    ordered = tuple(sorted(dict.fromkeys(amendable)))
    if not ordered:
        return (), "no_amendable_paths"
    if len(ordered) > INCREMENTAL_MAX_DIRTY_PATHS:
        return (), f"dirty_paths_exceed_limit:{len(ordered)}"
    return ordered, ""


def _copy_graph_for_amend(parent: Path, candidate: Path) -> None:
    """Copy a published graph so the copy holds everything the parent commits.

    A published graph is left in WAL mode, so readers opening it create a
    ``-wal`` and a ``-shm`` beside it. Their mere PRESENCE says nothing: on the
    live run the ``-wal`` was 0 bytes and the ``-shm`` 32 KiB, both created by
    readers, with no unflushed frame anywhere. Refusing on their existence
    refused every amend a real run would ever attempt -- the first edit of the
    2026-09-08 smoke reported exactly that, `mode=full
    reason=parent_graph_has_wal_sidecar`, at a cost of 104.6s.

    What actually matters is whether the log holds committed frames the main
    file does not. If it does, it is copied beside the candidate under the
    candidate's own name, and SQLite recovers it when the producer opens the
    copy. The parent is still never written to, so its certificate stands.

    A plain file copy is used rather than the backup API on purpose: the graph
    is ~900 MB and the amend it serves takes ~11s, so a page-by-page copy
    through Python would cost more than the operation it exists to accelerate.
    """

    shutil.copyfile(parent, candidate)
    log = parent.with_name(parent.name + "-wal")
    try:
        pending = log.stat().st_size
    except OSError:
        pending = 0
    if pending:
        shutil.copyfile(log, candidate.with_name(candidate.name + "-wal"))


def _ensure_index_incremental_unlocked(
    root: str, *, layout: RuntimeLayout, parent_graph: Path,
    changed_paths: tuple[str, ...], excluded_roots: tuple[Path, ...] = (),
    diagnostics: list[str] | None = None,
) -> tuple[str | None, str, tuple[dict[str, object], ...]]:
    """Amend a copy of the published graph and publish it as a new revision.

    Returns ``(graph_path, reason, per_path_results)``. ``reason`` is empty on
    success and names the refusal otherwise; the caller rebuilds in full on any
    non-empty reason, so a refusal here is an answer rather than an error.

    The parent graph is never opened for writing. A published graph is
    immutable and its manifest pins its exact bytes, so amending in place would
    invalidate the certificate of the graph readers hold right now. The copy is
    what gets amended, and it is published as its own revision through the same
    certification the full build uses; the parent stays certifiable, and
    retention keeps it as the one superseded revision.
    """

    results: tuple[dict[str, object], ...] = ()
    batch = _producer_supports_amend_capability(BATCH_AMEND_CAPABILITY)
    if not batch and not _producer_supports_incremental_amend():
        return None, "producer_lacks_amend_capability", results
    if not parent_graph.is_file():
        return None, "parent_graph_missing", results
    parent_manifest = parent_graph.with_suffix(".manifest.json")
    if not parent_manifest.is_file():
        return None, "parent_manifest_missing", results
    certification = _binary_certification()
    valid, certification_reason = _certify_published_graph(
        parent_graph, parent_manifest, expected_root=Path(layout.workspace),
        expected_binary_sha256=certification.get("binary_sha256", ""),
    )
    if not valid:
        # An uncertifiable parent is not a base to build authority on, and it
        # is the shape a producer swap takes: the binary that would amend is
        # not the binary the parent was certified against.
        return None, f"incremental_parent_uncertifiable:{certification_reason}", results

    root_path = Path(root)
    if batch:
        amendable = tuple(sorted(set(changed_paths)))
        refusal = "" if amendable else "no_amendable_paths"
        for relative in amendable:
            path = (root_path / relative).resolve()
            if path == root_path.resolve() or root_path.resolve() not in path.parents:
                refusal = "changed_path_outside_repository"
    else:
        amendable, refusal = _amendable_paths(root_path, changed_paths)
    if refusal:
        return None, refusal, results

    reuse_key = compute_index_reuse_key(root, excluded_roots=excluded_roots)
    gt_dir = _graph_state_dir(root, None, layout, reuse_key)
    gt_dir.mkdir(parents=True, exist_ok=True)
    db = gt_dir / "graph.db"
    existing_manifest = db.with_suffix(".manifest.json")
    if db.is_file() and existing_manifest.is_file():
        try:
            manifest = json.loads(existing_manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {}
        if (
            manifest.get("index_reuse_key") == reuse_key.as_dict()
            and manifest.get("index_reuse_key_sha256") == reuse_key.digest
        ):
            reusable, _reason = _certify_published_graph(
                db, existing_manifest, expected_root=Path(layout.workspace),
                expected_binary_sha256=reuse_key.producer_binary_sha256,
            )
            if reusable:
                return str(db), "", results
    if db.exists():
        # Same contract as the full path: a published revision is not rewritten.
        return None, "immutable_graph_artifact_invalid", results

    identity = _execution_identity()
    if identity["identity_scope"] == "benchmark_invalid":
        return None, "GT_INDEX_IDENTITY_INVALID", results

    with tempfile.NamedTemporaryFile(
        dir=gt_dir, prefix=".graph.", suffix=".db", delete=False
    ) as handle:
        candidate = Path(handle.name)
    parent_sha256 = hashlib.sha256(parent_graph.read_bytes()).hexdigest()
    collected: list[dict[str, object]] = []
    attempts: list[str] = []
    process_result: IndexProcessResult | None = None
    total_elapsed_ms = 0
    try:
        if not batch:
            _copy_graph_for_amend(parent_graph, candidate)
        for ordinal, relative in enumerate(("batch",) if batch else amendable, start=1):
            def build_argv(binary: str, argv_root: str, output: str,
                           _relative: str = relative) -> list[str]:
                if batch:
                    return _index_command(binary, argv_root, output) + ["-amend-parent", str(parent_graph)]
                return _incremental_index_command(binary, argv_root, output, _relative)

            # No retry. A failed amend leaves the copy in an unknown state, and
            # the honest recovery is the caller's full rebuild rather than a
            # second amend onto the same bytes.
            process_result = _run_index_bounded(
                root, candidate, gt_dir, command_factory=build_argv,
            )
            total_elapsed_ms += process_result.elapsed_ms
            row = _parse_incremental_result(process_result.stdout_tail)
            row["path"] = relative
            if batch:
                row["paths"] = list(amendable)
                row["mode"] = "batch"
            row["status"] = process_result.status
            collected.append(row)
            attempts.append(
                f"{ordinal}:{relative}:{process_result.status}:"
                f"{process_result.error_code or _OK}:"
                f"inserted={row.get('inserted', '?')},updated={row.get('updated', '?')},"
                f"removed={row.get('removed', '?')},"
                f"symbols_reminted={row.get('symbols_reminted', '?')},"
                f"short_circuited={row.get('short_circuited', '?')}"
            )
            if not process_result.success:
                results = tuple(collected)
                return None, f"amend_failed:{process_result.error_code or process_result.status}", results
        results = tuple(collected)
        assert process_result is not None
        published = _publish_candidate(
            candidate,
            root=root, logical_root=str(layout.workspace), gt_dir=gt_dir, db=db,
            reuse_key=reuse_key, identity=identity,
            process_result=replace(process_result, elapsed_ms=total_elapsed_ms),
            build_attempts=tuple(attempts), excluded_roots=excluded_roots,
            diagnostics=diagnostics, build_mode="incremental",
            parent_graph_sha256=parent_sha256, amended_paths=amendable,
        )
        if published is None:
            return None, "incremental_publication_failed", results
        return published, "", results
    finally:
        candidate.unlink(missing_ok=True)
        # The producer opens the copy in WAL mode and checkpoints before it
        # exits, so these are empty by now -- but only the main file is
        # published, and an unswept sidecar accumulates once per amend in a
        # directory whose disk cost already runs to hundreds of megabytes.
        for sidecar in ("-wal", "-shm"):
            candidate.with_name(candidate.name + sidecar).unlink(missing_ok=True)


def ensure_index(root: str, *, state_dir: str | None = None,
                 excluded_roots: tuple[Path, ...] = (),
                 layout: RuntimeLayout | None = None) -> str | None:
    """Build/reuse one graph under an inter-process publication lock.

    Correct-or-quiet for local work; fail-closed for a benchmark-bound run,
    where an absent graph is a defect rather than a degraded mode.
    """

    graph: str | None = None
    # A refusal that cannot name its cause is how one poisoned revision
    # directory cost a run and read as an environment problem.
    diagnostics: list[str] = []
    if layout is not None:
        excluded_roots = tuple(dict.fromkeys((*excluded_roots, *layout.excluded_roots)))
    # Whether there was source to index at all. A task that starts empty has
    # nothing to build from yet and fills as the agent creates files; a task
    # holding source and producing no graph is a defect.
    indexable = bool(root and os.path.isdir(root) and is_code_repo(
        root, **({"excluded_roots": excluded_roots} if excluded_roots else {})
    ))
    try:
        if indexable:
            gt_dir = layout.graph_root if layout is not None else _graph_state_dir(root, state_dir)
            gt_dir.mkdir(parents=True, exist_ok=True)
            lock_root = layout.graph_root if layout is not None else gt_dir
            with _graph_publication_lock(lock_root / ".graph.lock"):
                graph = _ensure_index_unlocked(root, state_dir=state_dir, diagnostics=diagnostics, **(
                    {"excluded_roots": excluded_roots} if excluded_roots else {}
                ), **({"layout": layout} if layout is not None else {}))
    except Exception as exc:  # noqa: BLE001 - indexing remains correct-or-quiet
        diagnostics.append(f"{type(exc).__name__}: {exc}")
        graph = None
    if (
        graph is None
        and indexable
        and _execution_identity()["identity_scope"] == "benchmark_bound"
    ):
        raise BenchmarkGraphRequired(
            "benchmark run has no graph; refusing to measure a treatment that "
            "cannot use the mechanism under test"
            + (f" ({'; '.join(diagnostics)})" if diagnostics else "")
        )
    return graph


class IndexBuildStatus(StrEnum):
    BUILT = "built"
    BUILT_CORE_ONLY = "built_core_only"
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
    resource_evidence_path: str = ""
    resource_evidence_sha256: str = ""
    memory_evidence: bool = False
    exit_code: int | None = None
    attempts: tuple[str, ...] = ()
    analysis_state: str = "unrecorded"
    analysis_failure_reason: str = ""
    embedding_state: str = "not_requested"
    embedding_failure_reason: str = ""
    # What the refresh actually achieved, when it ran. Kept apart from
    # embedding_failure_reason because a name that outlives its meaning is this
    # ticket's most repeated defect, and "the rate we measured" is not a failure.
    embedding_measurement: str = ""
    # How the graph behind this receipt was produced, and why.
    #
    # A full rebuild and an amend are different claims about the same file set,
    # and "incremental" with an empty reason reads very differently from
    # "full" with reason "producer_lacks_amend_capability". Naming the reason
    # is what turns a silent permanent fallback -- the failure mode that kept
    # the amend path dead while looking healthy -- into something a journal
    # shows on the first edit.
    build_mode: str = "full"
    build_mode_reason: str = ""
    incremental_results: tuple[Mapping[str, object], ...] = ()

    @property
    def success(self) -> bool:
        return self.status in {
            IndexBuildStatus.BUILT,
            IndexBuildStatus.BUILT_CORE_ONLY,
        } and bool(self.graph_db)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value, "graph_db": self.graph_db,
            "source_revision": self.source_revision, "graph_revision": self.graph_revision,
            "error_type": self.error_type, "error_diagnostic": self.error_diagnostic,
            "resource_evidence_path": self.resource_evidence_path,
            "resource_evidence_sha256": self.resource_evidence_sha256,
            "memory_evidence": self.memory_evidence, "exit_code": self.exit_code,
            "attempts": self.attempts,
            "analysis_state": self.analysis_state,
            "analysis_failure_reason": self.analysis_failure_reason,
            "embedding_state": self.embedding_state,
            "embedding_failure_reason": self.embedding_failure_reason,
            "embedding_measurement": self.embedding_measurement,
            "build_mode": self.build_mode,
            "build_mode_reason": self.build_mode_reason,
            "incremental_results": [dict(row) for row in self.incremental_results],
        }


def _resolved_binary_path() -> str:
    try:
        from groundtruth._binary import find_binary

        candidate = find_binary()
    except (ImportError, RuntimeError, OSError):
        candidate = os.environ.get("GT_INDEX_BINARY") or shutil.which("gt-index") or ""
    return str(Path(candidate).resolve()) if candidate else ""


@contextmanager
def _graph_publication_lock(path: Path):
    """Serialize graph/evidence publication across threads and processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + _INDEX_TIMEOUT_SECONDS + 30
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("gt-index publication lock timed out") from None
                    time.sleep(0.05)
        else:
            import fcntl

            deadline = time.monotonic() + _INDEX_TIMEOUT_SECONDS + 30
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("gt-index publication lock timed out") from None
                    time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _graph_phase_metadata(graph: Path) -> dict[str, object]:
    """Read and verify producer phase state while accepting pre-contract graphs."""
    con = sqlite3.connect(f"file:{graph.resolve().as_posix()}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )}
        meta_columns = {row[1] for row in con.execute("PRAGMA table_info(project_meta)")}
        rows = (
            dict(con.execute("SELECT key,value FROM project_meta"))
            if {"key", "value"}.issubset(meta_columns) else {}
        )
        keys = {
            "core_phase_state", "core_phase_receipt", "core_phase_receipt_sha256",
            "analysis_state", "analysis_failure_reason", "analysis_phase_receipt",
            "analysis_phase_receipt_sha256",
        }
        present = keys.intersection(rows)
        if present and present != keys:
            raise ValueError("phase_receipt_incomplete")
        if present:
            expected_schemas = {
                "core": "gt-index.core-phase.v1",
                "analysis": "gt-index.analysis-phase.v1",
            }
            for phase in ("core", "analysis"):
                payload = str(rows[f"{phase}_phase_receipt"])
                expected = str(rows[f"{phase}_phase_receipt_sha256"])
                if hashlib.sha256(payload.encode("utf-8")).hexdigest() != expected:
                    raise ValueError(f"{phase}_phase_receipt_sha256_mismatch")
                try:
                    receipt = json.loads(payload)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"{phase}_phase_receipt_invalid") from exc
                state_key = "core_phase_state" if phase == "core" else "analysis_state"
                if not isinstance(receipt, dict) or receipt.get("state") != rows[state_key]:
                    raise ValueError(f"{phase}_phase_receipt_state_mismatch")
                if receipt.get("schema") != expected_schemas[phase]:
                    raise ValueError(f"{phase}_phase_receipt_schema_mismatch")
            if rows["core_phase_state"] != "committed":
                raise ValueError("core_phase_state_invalid")
            if rows["analysis_state"] not in {"complete", "failed", "not_run"}:
                raise ValueError("analysis_state_invalid")
            if rows["analysis_state"] == "complete" and rows["analysis_failure_reason"]:
                raise ValueError("complete_analysis_has_failure_reason")
            if rows["analysis_state"] != "complete" and not rows["analysis_failure_reason"]:
                raise ValueError("incomplete_analysis_missing_failure_reason")
            analysis_receipt = json.loads(str(rows["analysis_phase_receipt"]))
            if analysis_receipt.get("failure_reason", "") != rows["analysis_failure_reason"]:
                raise ValueError("analysis_failure_reason_mismatch")
        cochange_rows = (
            int(con.execute("SELECT COUNT(*) FROM cochanges").fetchone()[0])
            if "cochanges" in tables else 0
        )
        # Derived-layer state keys written by Pass 4g (the wiring commit).
        # On a pre-wiring graph every key is absent; "unrecorded" is the
        # honest default rather than pretending nothing ran.
        derived_state_keys = (
            "derived_layers_state", "derived_layers_degraded",
            "derived_cochange_state", "derived_cochange_pairs",
            "derived_cochange_window_start", "derived_cochange_window_end",
            "derived_community_state", "derived_community_count",
            "derived_community_members", "derived_community_cohesion",
            "derived_process_state", "derived_process_count",
            "derived_process_steps",
        )
        derived = {k: str(rows.get(k, "unrecorded")) for k in derived_state_keys}
        # Derived-table row counts (absent on pre-wiring graphs).
        for tbl, key in (
            ("communities", "community_rows"),
            ("community_members", "community_member_rows"),
            ("processes", "process_rows"),
            ("process_steps", "process_step_rows"),
        ):
            derived[key] = (
                int(con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0])
                if tbl in tables else 0
            )
    finally:
        con.close()
    return {
        "core_phase_state": str(rows.get("core_phase_state", "unrecorded")),
        "analysis_state": str(rows.get("analysis_state", "unrecorded")),
        "analysis_failure_reason": str(rows.get("analysis_failure_reason", "")),
        "cochange_rows": cochange_rows,
        **derived,
    }


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
        if missing:
            return False, f"missing_tables:{','.join(missing)}"
        _graph_phase_metadata(graph)
        return True, "ok"
    except (sqlite3.Error, OSError, ValueError) as exc:
        if isinstance(exc, ValueError):
            return False, str(exc)
        return False, f"{type(exc).__name__}:{exc}"


def _certify_published_graph(graph: Path, manifest_path: Path, *, expected_root: Path,
                             expected_source_revision: str = "",
                             expected_binary_sha256: str = "") -> tuple[bool, str]:
    return certify_graph_artifact(
        graph, manifest_path,
        expected_root_sha256=hashlib.sha256(
            os.path.realpath(expected_root).encode("utf-8", "surrogatepass")
        ).hexdigest(),
        expected_source_revision=expected_source_revision,
        expected_binary_sha256=expected_binary_sha256,
        expected_task_id=os.environ.get("GT_TASK_ID", ""),
        expected_product_source_sha=os.environ.get("GT_PRODUCT_SOURCE_SHA", ""),
    )


def _lsp_failure(error: str) -> GraphBuildArtifact:
    return GraphBuildArtifact(False, "", "", error)


def _lineage_file(root: Path, parent: Path, value: object) -> Path | None:
    """Resolve one relocatable reference within its declared graph root."""

    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return None
    relative = Path(value)
    if value in {".", ".."}:
        return None
    unresolved = parent / relative
    if unresolved.is_symlink():
        return None
    resolved = unresolved.resolve()
    resolved_root = root.resolve()
    return resolved if resolved_root in resolved.parents else None


def _portable_lineage_reference(root: Path, parent: Path, target: Path) -> str:
    parent_parts = parent.resolve().relative_to(root.resolve()).parts
    target_parts = target.resolve().relative_to(root.resolve()).parts
    return "/".join((*(("..",) * len(parent_parts)), *target_parts))


def certify_lsp_candidate(
    base_graph: str | Path,
    candidate_graph: str | Path,
    terminal_receipt: Mapping[str, object],
    *,
    expected_source_revision: str,
    expected_repository_root_sha256: str,
    expected_repository_snapshot_sha256: str,
    layout: RuntimeLayout,
    expected_root_sha256: str,
    expected_binary_sha256: str = "",
    expected_task_id: str = "",
    expected_product_source_sha: str = "",
) -> GraphBuildArtifact:
    """Publish a one-level LSP derivative with independently checked lineage."""

    base_input = Path(base_graph)
    candidate_input = Path(candidate_graph)
    if base_input.is_symlink() or candidate_input.is_symlink():
        return _lsp_failure("lsp_layout_symlink_forbidden")
    base = base_input.resolve()
    candidate = candidate_input.resolve()
    graph_root = layout.graph_root.resolve()
    try:
        base_relative = base.relative_to(graph_root)
        candidate_relative = candidate.relative_to(graph_root)
    except ValueError:
        return _lsp_failure("lsp_layout_invalid")
    if (
        not expected_source_revision
        or not re.fullmatch(r"[0-9a-f]{64}", expected_repository_root_sha256)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_repository_snapshot_sha256)
        or base == candidate
        or not base_relative.parts
        or base_relative.parts[0] != "revisions"
        or len(candidate_relative.parts) != 3
        or candidate_relative.parts[0] != "enrichments"
        or not base.is_file()
        or not candidate.is_file()
    ):
        return _lsp_failure("lsp_layout_invalid")
    base_manifest_path = base.with_suffix(".manifest.json")
    manifest_path = candidate.with_suffix(".manifest.json")
    receipt_path = candidate.with_suffix(".lsp-terminal.json")
    if manifest_path.exists() or receipt_path.exists():
        return _lsp_failure("lsp_certification_already_exists")
    valid, reason = certify_graph_artifact(
        base,
        base_manifest_path,
        expected_root_sha256=expected_root_sha256,
        expected_source_revision="",
        expected_binary_sha256=expected_binary_sha256,
        expected_task_id=expected_task_id,
        expected_product_source_sha=expected_product_source_sha,
    )
    if not valid:
        return _lsp_failure(f"lsp_base_invalid:{reason}")
    try:
        base_manifest = json.loads(base_manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _lsp_failure("lsp_base_manifest_unreadable")
    if base_manifest.get("derivation") is not None:
        return _lsp_failure("lsp_nested_derivation_forbidden")
    base_sha = hashlib.sha256(base.read_bytes()).hexdigest()
    candidate_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
    base_revision = str(
        base_manifest.get("graph_revision") or base_manifest.get("graph_sha256") or ""
    )
    receipt = dict(terminal_receipt)
    if (
        receipt.get("schema") != LSP_TERMINAL_SCHEMA
        or receipt.get("terminal") is not True
        or receipt.get("status") != "succeeded"
        or receipt.get("publishable") is not True
        or receipt.get("source_revision") != expected_source_revision
        or receipt.get("repository_root_sha256")
        != expected_repository_root_sha256
        or receipt.get("repository_snapshot_sha256")
        != expected_repository_snapshot_sha256
        or receipt.get("input_graph_revision") != base_revision
        or receipt.get("input_graph_sha256") != base_sha
        or receipt.get("output_graph_sha256") != candidate_sha
    ):
        return _lsp_failure("lsp_terminal_receipt_identity_mismatch")
    try:
        if Path(str(receipt.get("candidate_path") or "")).resolve() != candidate:
            return _lsp_failure("lsp_terminal_receipt_candidate_mismatch")
        schema_valid, schema_reason = _graph_schema_receipt(candidate)
    except (OSError, ValueError):
        return _lsp_failure("lsp_candidate_unreadable")
    if not schema_valid:
        return _lsp_failure(f"lsp_candidate_schema_invalid:{schema_reason}")

    receipt_seal = _sealed_json(receipt_path, receipt, "receipt_sha256")
    receipt_file_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    manifest = dict(base_manifest)
    manifest.update({
        "source_revision": expected_source_revision,
        "graph_revision": candidate_sha,
        "graph_sha256": candidate_sha,
        "graph_bytes": candidate.stat().st_size,
        "indexed_file_count": _graph_scale(candidate)[0],
        "indexed_node_count": _graph_scale(candidate)[1],
        **_graph_phase_metadata(candidate),
        "derivation": {
            "schema": LSP_DERIVATION_SCHEMA,
            "phase": "lsp",
            "graph_root": "../..",
            "base_graph": _portable_lineage_reference(
                graph_root, candidate.parent, base
            ),
            "base_manifest": _portable_lineage_reference(
                graph_root, candidate.parent, base_manifest_path
            ),
            "base_resource": _portable_lineage_reference(
                graph_root, candidate.parent, base.with_name("index-resource.json")
            ),
            "base_graph_sha256": base_sha,
            "base_graph_revision": base_revision,
            "repository_snapshot_root_sha256": expected_repository_root_sha256,
            "terminal_receipt": receipt_path.name,
            "terminal_receipt_sha256": receipt_file_sha,
            "terminal_receipt_seal": receipt_seal,
        },
    })
    _atomic_write(manifest_path, _canonical_json(manifest))
    valid, reason = certify_graph_artifact(
        candidate,
        manifest_path,
        expected_root_sha256=expected_root_sha256,
        expected_source_revision=expected_source_revision,
        expected_binary_sha256=expected_binary_sha256,
        expected_task_id=expected_task_id,
        expected_product_source_sha=expected_product_source_sha,
    )
    if not valid:
        manifest_path.unlink(missing_ok=True)
        receipt_path.unlink(missing_ok=True)
        return _lsp_failure(f"lsp_candidate_invalid:{reason}")
    return GraphBuildArtifact(True, str(candidate), candidate_sha)


def certify_graph_artifact(
    graph: Path, manifest_path: Path, *, expected_root_sha256: str,
    expected_source_revision: str = "", expected_binary_sha256: str = "",
    expected_task_id: str = "", expected_product_source_sha: str = "",
) -> tuple[bool, str]:
    """Validate the same producer certificate before and after collection."""
    if graph.is_symlink() or manifest_path.is_symlink():
        return False, "graph_artifact_symlink_forbidden"
    if manifest_path.resolve() != graph.with_suffix(".manifest.json").resolve():
        return False, "manifest_path_mismatch"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "manifest_unreadable"
    if manifest.get("schema") != "gt.graph_certification.v1":
        return False, "manifest_schema_mismatch"
    root_sha = expected_root_sha256
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
    derivation = manifest.get("derivation")
    resource_path = graph.with_name("index-resource.json")
    if derivation is not None:
        if not isinstance(derivation, dict):
            return False, "derivation_invalid"
        if (
            derivation.get("schema") != LSP_DERIVATION_SCHEMA
            or derivation.get("phase") != "lsp"
        ):
            return False, "derivation_unknown"
        parent = manifest_path.resolve().parent
        if derivation.get("graph_root") != "../..":
            return False, "derivation_root_invalid"
        lineage_root = (parent / "../..").resolve()
        try:
            candidate_relative = graph.resolve().relative_to(lineage_root)
        except ValueError:
            return False, "derivation_root_invalid"
        if (
            len(candidate_relative.parts) != 3
            or candidate_relative.parts[0] != "enrichments"
        ):
            return False, "derivation_root_invalid"
        base_graph = _lineage_file(
            lineage_root, parent, derivation.get("base_graph")
        )
        base_manifest_path = _lineage_file(
            lineage_root, parent, derivation.get("base_manifest")
        )
        base_resource = _lineage_file(
            lineage_root, parent, derivation.get("base_resource")
        )
        receipt_path = _lineage_file(
            lineage_root, parent, derivation.get("terminal_receipt")
        )
        if None in {base_graph, base_manifest_path, base_resource, receipt_path}:
            return False, "derivation_reference_invalid"
        assert base_graph is not None
        assert base_manifest_path is not None
        assert base_resource is not None
        assert receipt_path is not None
        if base_graph == graph.resolve() or base_manifest_path == manifest_path.resolve():
            return False, "derivation_cycle"
        if base_manifest_path != base_graph.with_suffix(".manifest.json"):
            return False, "derivation_base_manifest_path_mismatch"
        try:
            if base_graph.relative_to(lineage_root).parts[0] != "revisions":
                return False, "derivation_base_location_invalid"
        except (IndexError, ValueError):
            return False, "derivation_base_location_invalid"
        try:
            base_manifest = json.loads(base_manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False, "derivation_base_manifest_unreadable"
        if base_manifest.get("derivation") is not None:
            return False, "derivation_nested"
        base_valid, base_reason = certify_graph_artifact(
            base_graph,
            base_manifest_path,
            expected_root_sha256=expected_root_sha256,
            expected_source_revision="",
            expected_binary_sha256=expected_binary_sha256,
            expected_task_id=expected_task_id,
            expected_product_source_sha=expected_product_source_sha,
        )
        if not base_valid:
            return False, f"derivation_base_invalid:{base_reason}"
        base_sha = hashlib.sha256(base_graph.read_bytes()).hexdigest()
        base_revision = str(
            base_manifest.get("graph_revision")
            or base_manifest.get("graph_sha256")
            or ""
        )
        if (
            derivation.get("base_graph_sha256") != base_sha
            or derivation.get("base_graph_revision") != base_revision
            or base_resource != base_graph.with_name("index-resource.json")
            or manifest.get("index_resource_sha256")
            != hashlib.sha256(base_resource.read_bytes()).hexdigest()
        ):
            return False, "derivation_base_identity_mismatch"
        if (
            not receipt_path.is_file()
            or derivation.get("terminal_receipt_sha256")
            != hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        ):
            return False, "derivation_terminal_receipt_mismatch"
        receipt = _read_sealed_json(receipt_path, "receipt_sha256")
        if (
            receipt is None
            or derivation.get("terminal_receipt_seal")
            != hashlib.sha256(_canonical_json(receipt)).hexdigest()
        ):
            return False, "derivation_terminal_receipt_seal_invalid"
        if (
            receipt.get("schema") != LSP_TERMINAL_SCHEMA
            or receipt.get("terminal") is not True
            or receipt.get("status") != "succeeded"
            or receipt.get("publishable") is not True
            or receipt.get("source_revision") != manifest.get("source_revision")
            or receipt.get("repository_root_sha256")
            != derivation.get("repository_snapshot_root_sha256")
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(receipt.get("repository_snapshot_sha256") or ""),
            )
            or receipt.get("input_graph_revision") != base_revision
            or receipt.get("input_graph_sha256") != base_sha
            or receipt.get("output_graph_sha256") != manifest.get("graph_sha256")
        ):
            return False, "derivation_terminal_receipt_identity_mismatch"
        inherited = (
            "identity_scope", "task_id", "product_source_sha",
            "repository_root_sha256", "source_manifest_sha256",
            "binary_sha256", "binary_certified",
            "index_resource_sha256",
        )
        if any(manifest.get(key) != base_manifest.get(key) for key in inherited):
            return False, "derivation_producer_identity_mismatch"
        resource_path = base_resource
    if (
        not resource_path.is_file()
        or manifest.get("index_resource_sha256")
        != hashlib.sha256(resource_path.read_bytes()).hexdigest()
    ):
        return False, "index_resource_mismatch"
    resource = _read_sealed_json(resource_path, "evidence_sha256")
    if resource is None:
        return False, "index_resource_seal_invalid"
    if (
        resource.get("schema") != INDEX_RESOURCE_SCHEMA
        or type(resource.get("exit_code")) is not int
        or resource.get("exit_code") != 0
        or resource.get("status") != "completed"
        or resource.get("error_code") != ""
        or resource.get("memory_evidence") is not False
        or resource.get("repository_root_sha256") != root_sha
        or resource.get("source_manifest_sha256")
        != manifest.get("source_manifest_sha256")
        or resource.get("producer_binary_sha256") != manifest.get("binary_sha256")
        or resource.get("task_id") != manifest.get("task_id")
        or resource.get("product_source_sha") != manifest.get("product_source_sha")
        or resource.get("identity_scope") != manifest.get("identity_scope")
        or (
            expected_task_id and resource.get("task_id") != expected_task_id
        )
        or (
            expected_product_source_sha
            and resource.get("product_source_sha") != expected_product_source_sha
        )
    ):
        return False, "index_resource_identity_mismatch"
    valid, reason = _graph_schema_receipt(graph)
    if not valid:
        return False, f"graph_schema_invalid:{reason}"
    phase = _graph_phase_metadata(graph)
    for key, value in phase.items():
        if manifest.get(key) != value:
            return False, f"graph_metadata_mismatch:{key}"
    return True, "ok"


def _receipt_for_published_graph(
    graph: str, *, source_revision: str = "",
    embedding_budget_seconds: float | None = None,
    contract_store_path: Path | None = None,
    layout: RuntimeLayout | None = None,
    build_mode: str = "full", build_mode_reason: str = "",
    incremental_results: tuple[Mapping[str, object], ...] = (),
) -> IndexBuildReceipt:
    """Certify a published graph and describe it as a receipt.

    Split out of ensure_index_with_receipt so an amended graph is certified by
    the same code that certifies a fully built one. Two certification paths for
    two build modes is how a graph ends up trusted on terms nobody stated.
    """

    graph_path = Path(graph)
    valid, reason = _graph_schema_receipt(graph_path)
    if not valid:
        return IndexBuildReceipt(IndexBuildStatus.INVALID_DATABASE, graph_db=graph, build_mode=build_mode,
                                 build_mode_reason=build_mode_reason,
                                 source_revision=source_revision, error_type=reason,
                                 error_diagnostic=reason)
    manifest = graph_path.with_suffix(".manifest.json")
    if not manifest.is_file():
        return IndexBuildReceipt(IndexBuildStatus.INVALID_DATABASE, graph_db=graph, build_mode=build_mode,
                                 build_mode_reason=build_mode_reason,
                                 source_revision=source_revision, error_type="manifest_missing",
                                 error_diagnostic="graph certification manifest missing")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        graph_revision = str(payload.get("graph_revision", payload.get("graph_sha256", "")))
    except (OSError, ValueError):
        graph_revision = ""
    phase = _graph_phase_metadata(graph_path)
    analysis_state = str(phase["analysis_state"])
    status = (
        IndexBuildStatus.BUILT_CORE_ONLY
        if analysis_state in {"failed", "not_run"}
        else IndexBuildStatus.BUILT
    )
    # Refresh the contract-embedding sidecar after every successful build.
    # A failure here is logged but never costs the graph â€” the embedding store
    # is a cache the retrieval side can degrade from with a named reason.
    embedding_state = "unconfigured"
    embedding_failure = ""
    embedding_measurement = ""
    model_dir = os.environ.get("GT_DENSE_MODEL_DIR", "").strip()
    if model_dir and status in (IndexBuildStatus.BUILT, IndexBuildStatus.BUILT_CORE_ONLY):
        try:
            from gt_engine.contract_embeddings import (
                DEFAULT_BATCH_SIZE,
                ContractEmbeddingStore,
                EmbeddingBudgetExhausted,
                EmbeddingBudgetInsufficient,
                default_store_path,
                onnx_embedder,
                onnx_token_lengths,
            )

            # The layout decides when the caller does not, because a caller
            # that forgets is not hypothetical: three call sites needed this
            # path, two passed it and the rebuild in GraphBuildCoordinator
            # simply omitted it. It fell through to default_store_path, which
            # is keyed on the graph and therefore empty at exactly the moment
            # the store matters - after a republication. Run 34095557374
            # re-planned the whole corpus sixteen times
            # (`planned=3809..3822, estimated=913s, budget=60s`) and never
            # refreshed dense retrieval once in 78 minutes.
            #
            # An explicit argument still wins, so an override stays possible;
            # what is no longer possible is a caller holding a layout and
            # silently getting the graph-keyed store.
            store_path = (
                contract_store_path
                or (layout.contract_store_path if layout is not None else None)
                or os.environ.get("GT_CONTRACT_EMBEDDING_INDEX")
                or default_store_path(graph_path)
            )
            store = ContractEmbeddingStore(store_path)
            # The refresh is a cache the retrieval side degrades from, but it
            # is CPU-bound ONNX inference over every moved contract and it runs
            # inside agent construction, ahead of the session journal and the
            # first provider call.  Left unbounded it spends the run's whole
            # wall budget and the run dies with no evidence at all.  The caller
            # owns the bound; the state below is what the receipt reports.
            deadline = (
                time.monotonic() + float(embedding_budget_seconds)
                if embedding_budget_seconds and float(embedding_budget_seconds) > 0
                else None
            )
            started = time.monotonic()
            try:
                receipt = store.refresh(
                    graph_path, embed_fn=onnx_embedder(model_dir), deadline=deadline,
                    length_fn=onnx_token_lengths(model_dir),
                )
            finally:
                store.close()
            # Report the rate the run actually achieved. The a priori estimate
            # is one constant derived from two runs; without the run stating its
            # own numbers, a constant that is too HIGH silently skips plans that
            # would have fitted and nothing ever contradicts it. With them,
            # estimate and outcome are comparable in the receipt and the
            # accumulated figures are the argument for building this store at
            # bundle time instead of inside the timed window.
            elapsed = time.monotonic() - started
            embedded = (
                int(receipt.get("embedded") or 0) if isinstance(receipt, dict) else 0
            )
            batches = -(-embedded // DEFAULT_BATCH_SIZE) if embedded else 0
            embedding_state = "refreshed"
            embedding_measurement = (
                f"embedded={embedded}:batches={batches}:elapsed={elapsed:.0f}s:"
                f"observed_seconds_per_batch={elapsed / batches:.2f}"
                if batches
                else f"embedded=0:elapsed={elapsed:.0f}s"
            )
        except EmbeddingBudgetInsufficient as exc:
            # One honest state, nothing spent. The numbers are the case for
            # building this store at bundle time instead.
            embedding_state = "budget_insufficient"
            embedding_failure = (
                f"planned={exc.planned}:estimated={exc.estimated_seconds:.0f}s:"
                f"budget={exc.budget_seconds:.0f}s"
            )
        except EmbeddingBudgetExhausted as exc:
            embedding_state = "budget_exhausted"
            embedding_failure = f"{exc.embedded}/{exc.planned}"
        except Exception as exc:
            embedding_state = "failed"
            embedding_failure = type(exc).__name__

    return IndexBuildReceipt(status, graph_db=graph,
                             build_mode=build_mode, build_mode_reason=build_mode_reason,
                             incremental_results=tuple(incremental_results),
                             source_revision=source_revision, graph_revision=graph_revision,
                             analysis_state=analysis_state,
                             embedding_state=embedding_state,
                             embedding_failure_reason=embedding_failure,
                             embedding_measurement=embedding_measurement,
                             analysis_failure_reason=str(phase["analysis_failure_reason"]))


def ensure_index_with_receipt(root: str | Path, *, state_dir: str | Path | None = None,
                              source_revision: str = "",
                              excluded_roots: tuple[Path, ...] = (),
                              embedding_budget_seconds: float | None = None,
                              contract_store_path: Path | None = None,
                              layout: RuntimeLayout | None = None) -> IndexBuildReceipt:
    root_path = Path(root)
    if layout is not None:
        excluded_roots = tuple(dict.fromkeys((*excluded_roots, *layout.excluded_roots)))
    try:
        indexable = root_path.is_dir() and is_code_repo(
            str(root_path), **({"excluded_roots": excluded_roots} if excluded_roots else {})
        )
    except SourceDiscoveryIncomplete as exc:
        if _execution_identity()["identity_scope"] == "benchmark_bound":
            raise BenchmarkGraphRequired(str(exc)) from exc
        return IndexBuildReceipt(IndexBuildStatus.BUILD_FAILED, source_revision=source_revision,
                                 error_type="source_discovery_incomplete", error_diagnostic=str(exc))
    if not indexable:
        return IndexBuildReceipt(IndexBuildStatus.NOT_APPLICABLE, source_revision=source_revision)
    try:
        graph = ensure_index(str(root_path), state_dir=str(state_dir) if state_dir else None,
                             **({"excluded_roots": excluded_roots} if excluded_roots else {}),
                             **({"layout": layout} if layout is not None else {}))
    except BenchmarkGraphRequired:
        # A benchmark without its graph is not a receipt outcome to record and
        # continue from; it stops the run.
        raise
    except Exception as exc:  # pragma: no cover - defensive boundary
        return IndexBuildReceipt(IndexBuildStatus.BUILD_FAILED, source_revision=source_revision,
                                 error_type=type(exc).__name__, error_diagnostic=str(exc)[:600])
    if not graph:
        gt_dir = _graph_state_dir(root_path, state_dir, layout)
        failure = _read_sealed_json(gt_dir / "graph.failure.json", "manifest_sha256")
        evidence_path = gt_dir / "index-failure-resource.json"
        evidence = _read_sealed_json(evidence_path, "evidence_sha256")
        evidence_file_sha = (
            hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            if evidence is not None
            else ""
        )
        status_by_code = {
            "GT_INDEX_CGROUP_OOM": "cgroup_oom",
            "GT_INDEX_EXIT_137_UNATTRIBUTED": "signal_9_unattributed",
            "GT_INDEX_IDENTITY_INVALID": "identity_refused",
            "GT_INDEX_LAUNCH_FAILED": "launch_failed",
            "GT_INDEX_MEMORY_GUARD_TRIGGERED": "memory_guard_triggered",
            "GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT": "memory_headroom_refused",
            "GT_INDEX_OUTPUT_INVALID": "output_invalid",
            "GT_INDEX_OUTPUT_MISSING": "output_missing",
            "GT_INDEX_PROCESS_FAILED": "nonzero_exit",
            "GT_INDEX_PROCESS_TREE_UNVERIFIED": "process_tree_unverified",
            "GT_INDEX_RESOURCE_GUARD_UNAVAILABLE": "resource_guard_unavailable",
            "GT_INDEX_TIMEOUT": "timeout",
        }
        evidence_code = str(evidence.get("error_code") or "") if evidence else ""
        memory_codes = {"GT_INDEX_CGROUP_OOM", "GT_INDEX_MEMORY_GUARD_TRIGGERED"}
        evidence_exit = evidence.get("exit_code") if evidence else None
        if evidence_code in {
            "GT_INDEX_IDENTITY_INVALID",
            "GT_INDEX_LAUNCH_FAILED",
            "GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT",
            "GT_INDEX_RESOURCE_GUARD_UNAVAILABLE",
        }:
            exit_valid = evidence_exit is None
        elif evidence_code in {"GT_INDEX_OUTPUT_INVALID", "GT_INDEX_OUTPUT_MISSING"}:
            exit_valid = evidence_exit == 0
        elif evidence_code == "GT_INDEX_PROCESS_TREE_UNVERIFIED":
            exit_valid = type(evidence_exit) is int
        elif evidence_code in {
            "GT_INDEX_CGROUP_OOM",
            "GT_INDEX_EXIT_137_UNATTRIBUTED",
            "GT_INDEX_MEMORY_GUARD_TRIGGERED",
            "GT_INDEX_TIMEOUT",
        }:
            exit_valid = evidence_exit in {-9, 137}
        else:
            exit_valid = type(evidence_exit) is int and evidence_exit not in {0, -9, 137}
        bound = bool(
            failure is not None
            and evidence is not None
            and failure.get("schema") == "gt.graph_failure.v1"
            and evidence.get("schema") == INDEX_RESOURCE_SCHEMA
            and failure.get("resource_evidence_sha256") == evidence_file_sha
            and failure.get("resource_evidence_path") == evidence_path.name
            and failure.get("error_code") == evidence.get("error_code")
            and evidence.get("status") == status_by_code.get(evidence_code)
            and evidence.get("memory_evidence") is (evidence_code in memory_codes)
            and exit_valid
            and (
                evidence_code != "GT_INDEX_CGROUP_OOM"
                or (
                    type(evidence.get("cgroup_oom_delta")) is int
                    and type(evidence.get("cgroup_oom_kill_delta")) is int
                    and (
                        evidence.get("cgroup_oom_delta", 0) > 0
                        or evidence.get("cgroup_oom_kill_delta", 0) > 0
                    )
                )
            )
            and (
                evidence_code != "GT_INDEX_MEMORY_GUARD_TRIGGERED"
                or (
                    type(evidence.get("peak_rss_bytes")) is int
                    and type(evidence.get("memory_limit_bytes")) is int
                    and evidence.get("peak_rss_bytes", 0)
                    > evidence.get("memory_limit_bytes", 0) > 0
                )
            )
            and type(evidence.get("elapsed_ms")) is int
            and type(evidence.get("stdout_bytes")) is int
            and type(evidence.get("stderr_bytes")) is int
            and failure.get("error_code")
            in {
                "GT_INDEX_CGROUP_OOM",
                "GT_INDEX_EXIT_137_UNATTRIBUTED",
                "GT_INDEX_IDENTITY_INVALID",
                "GT_INDEX_LAUNCH_FAILED",
                "GT_INDEX_MEMORY_GUARD_TRIGGERED",
                "GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT",
                "GT_INDEX_OUTPUT_INVALID",
                "GT_INDEX_OUTPUT_MISSING",
                "GT_INDEX_PROCESS_FAILED",
                "GT_INDEX_PROCESS_TREE_UNVERIFIED",
                "GT_INDEX_RESOURCE_GUARD_UNAVAILABLE",
                "GT_INDEX_TIMEOUT",
            }
            and all(
                failure.get(field) == evidence.get(field)
                for field in (
                    "task_id",
                    "product_source_sha",
                    "identity_scope",
                    "repository_root_sha256",
                    "source_manifest_sha256",
                    "producer_binary_sha256",
                )
            )
            and (
                not os.environ.get("GT_TASK_ID")
                or failure.get("task_id") == os.environ["GT_TASK_ID"]
            )
            and (
                not os.environ.get("GT_PRODUCT_SOURCE_SHA")
                or failure.get("product_source_sha")
                == os.environ["GT_PRODUCT_SOURCE_SHA"]
            )
        )
        return IndexBuildReceipt(
            IndexBuildStatus.BUILD_FAILED,
            source_revision=source_revision,
            error_type=(
                str(failure.get("error_code") or "GT_INDEX_PROCESS_FAILED")
                if bound and failure is not None
                else "index_failure_evidence_invalid"
            ),
            error_diagnostic=(
                str(evidence.get("status") or "index build failed")
                if bound and evidence is not None
                else "gt-index failed without valid sealed evidence"
            ),
            resource_evidence_path=str(evidence_path) if bound else "",
            resource_evidence_sha256=evidence_file_sha if bound else "",
            memory_evidence=bool(evidence.get("memory_evidence")) if bound and evidence else False,
            exit_code=(
                int(evidence["exit_code"])
                if bound and evidence and isinstance(evidence.get("exit_code"), int)
                else None
            ),
        )
    return _receipt_for_published_graph(
        graph, source_revision=source_revision,
        embedding_budget_seconds=embedding_budget_seconds,
        contract_store_path=contract_store_path, layout=layout,
    )


def refresh_index_files(root: str | Path, graph: str | Path, changed_paths: tuple[str, ...], *,
                        source_revision: str = "",
                        excluded_roots: tuple[Path, ...] = (),
                        embedding_budget_seconds: float | None = None,
                        contract_store_path: Path | None = None,
                        layout: RuntimeLayout | None = None) -> IndexBuildReceipt:
    """Amend ``changed_paths`` into a copy of ``graph``, or rebuild in full.

    This is the seam blocker 7c named. The producer has had a per-file amend
    boundary since 25a37a5f and nothing called it, so every edit paid for a
    whole re-index: on arktype ~115s against an edit interval near 50s, which
    never converges. Publications froze at 14 across 40 invalidations while the
    agent edited continuously, and every read after the first edit reported
    caller_coverage unavailable.

    A refusal is not an error here. Every reason the amend declines -- an
    uncertifiable parent, a deleted path, a producer that does not declare the
    capability -- falls back to the full rebuild that was the only behaviour
    before, and the reason is carried on the receipt so a permanent silent
    fallback is visible on the first edit rather than at the end of a run.
    """

    root_path = Path(root)
    if layout is not None:
        excluded_roots = tuple(dict.fromkeys((*excluded_roots, *layout.excluded_roots)))

    reason = "layout_required"
    results: tuple[Mapping[str, object], ...] = ()
    published: str | None = None
    if layout is not None and graph and changed_paths:
        try:
            with _graph_publication_lock(layout.graph_root / ".graph.lock"):
                published, reason, results = _ensure_index_incremental_unlocked(
                    str(root_path), layout=layout, parent_graph=Path(graph),
                    changed_paths=tuple(changed_paths), excluded_roots=excluded_roots,
                )
        except Exception as exc:  # noqa: BLE001 - a refused amend is a full rebuild
            published, reason = None, f"{type(exc).__name__}: {exc}"[:200]
    elif not graph:
        reason = "no_parent_graph"
    elif not changed_paths:
        reason = "no_changed_paths"

    if published:
        return _receipt_for_published_graph(
            published, source_revision=source_revision,
            embedding_budget_seconds=embedding_budget_seconds,
            contract_store_path=contract_store_path, layout=layout,
            build_mode="incremental", incremental_results=results,
        )
    # Module-level lookup on purpose: the existing test doubles replace this
    # name, and a fallback they cannot intercept is a fallback nobody notices.
    receipt = ensure_index_with_receipt(
        root_path, source_revision=source_revision, excluded_roots=excluded_roots,
        embedding_budget_seconds=embedding_budget_seconds,
        contract_store_path=contract_store_path, layout=layout,
    )
    return replace(receipt, build_mode="full", build_mode_reason=reason,
                   incremental_results=results)
