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
from contextlib import contextmanager
from dataclasses import dataclass, replace
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
INDEX_RESOURCE_SCHEMA = "gt.index_resource.v1"
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


def _run_index_bounded(root: str, output: Path, log_dir: Path) -> IndexProcessResult:
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
        process = subprocess.Popen(
            _index_command(binary, root, str(output)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_index_child_environment(memory_limit),
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
    """Start progressive LSP edge promotion over a freshly published graph.

    The producer ships a complete promotion subsystem whose own design note
    states the intent: gt-index publishes a usable graph immediately, then
    language servers promote edges in batches so resolution quality rises while
    the agent is already working. It was only ever started from the MCP server,
    so the benchmark harness published a graph and left the highest-precision
    edge tier -- lsp and lsp_verified, both admitted by the closure -- empty.

    Promotion is best-effort by construction. It discovers servers with
    shutil.which, so a container with none staged promotes nothing and keeps
    exactly the graph we publish today. Nothing here may fail an index that has
    already succeeded.
    """

    try:
        from groundtruth.lsp.background_promotion import (
            detect_available_servers,
            start_background_promotion,
        )
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

    try:
        start_background_promotion(str(database), str(root))
    except Exception:  # noqa: BLE001 - promotion is an optimiser, never a gate
        return {"status": "promotion_failed", "servers": servers}
    return {"status": "promotion_started", "servers": servers}


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


def _graph_state_dir(root: str | Path, state_dir: str | Path | None) -> Path:
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


def _ensure_index_unlocked(root: str, *, state_dir: str | None = None) -> str | None:
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

        gt_dir = _graph_state_dir(root, state_dir)
        if gt_dir != Path(root) / ".gt":
            gt_dir.mkdir(parents=True, exist_ok=True)
        else:
            gt_dir.mkdir(exist_ok=True)
            ignore = gt_dir / ".gitignore"
            if not ignore.exists():
                ignore.write_text("*\n", encoding="utf-8")
        db = gt_dir / "graph.db"
        identity = _execution_identity()
        reuse_key = compute_index_reuse_key(root)
        if identity["identity_scope"] == "benchmark_invalid":
            staged_evidence = gt_dir / ".index-identity-resource.json"
            refusal = IndexProcessResult(
                False, "identity_refused", "GT_INDEX_IDENTITY_INVALID"
            )
            _write_index_evidence(
                staged_evidence, root=str(root), result=refusal,
                reuse_key=reuse_key, identity=identity,
            )
            _publish_graph_failure(
                gt_dir, root=root, reuse_key=reuse_key,
                error_code=refusal.error_code, staged_evidence=staged_evidence,
                identity=identity,
            )
            return None
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
                        (gt_dir / "graph.failure.json").unlink(missing_ok=True)
                        (gt_dir / "index-failure-resource.json").unlink(missing_ok=True)
                        return str(db)
            except (OSError, ValueError, TypeError):
                pass
        with tempfile.NamedTemporaryFile(
            dir=gt_dir, prefix=".graph.", suffix=".db", delete=False
        ) as handle:
            candidate = Path(handle.name)
        candidate.unlink(missing_ok=True)
        process_result, build_attempts = _build_index_with_attempts(
            str(root), candidate, gt_dir
        )
        evidence_path = candidate.with_suffix(".resource.json")
        _write_index_evidence(
            evidence_path, root=str(root), result=process_result,
            reuse_key=reuse_key, identity=identity, attempts=build_attempts,
        )
        evidence_sha256 = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        failure_manifest = gt_dir / "graph.failure.json"
        if not process_result.success:
            _publish_graph_failure(
                gt_dir,
                root=root,
                reuse_key=reuse_key,
                error_code=process_result.error_code,
                staged_evidence=evidence_path,
                identity=identity,
            )
            candidate.unlink(missing_ok=True)
            return None
        if not candidate.is_file():
            _write_index_evidence(
                evidence_path, root=str(root),
                result=replace(
                    process_result, success=False, status="output_missing",
                    error_code="GT_INDEX_OUTPUT_MISSING",
                ),
                reuse_key=reuse_key, identity=identity,
            )
            _publish_graph_failure(
                gt_dir,
                root=root,
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
                evidence_path, root=str(root),
                result=replace(
                    process_result, success=False, status="output_invalid",
                    error_code="GT_INDEX_OUTPUT_INVALID",
                ),
                reuse_key=reuse_key, identity=identity,
            )
            _publish_graph_failure(
                gt_dir,
                root=root,
                reuse_key=reuse_key,
                error_code="GT_INDEX_OUTPUT_INVALID",
                staged_evidence=evidence_path,
                identity=identity,
            )
            candidate.unlink(missing_ok=True)
            return None
        if quick_check.lower() != "ok":
            _write_index_evidence(
                evidence_path, root=str(root),
                result=replace(
                    process_result, success=False, status="output_invalid",
                    error_code="GT_INDEX_OUTPUT_INVALID",
                ),
                reuse_key=reuse_key, identity=identity,
            )
            _publish_graph_failure(
                gt_dir,
                root=root,
                reuse_key=reuse_key,
                error_code="GT_INDEX_OUTPUT_INVALID",
                staged_evidence=evidence_path,
                identity=identity,
            )
            candidate.unlink(missing_ok=True)
            return None
        graph_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest()
        manifest = {
            "schema": "gt.graph_certification.v1",
            **identity,
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
            "indexed_file_count": _graph_scale(candidate)[0],
            "indexed_node_count": _graph_scale(candidate)[1],
            **_graph_phase_metadata(candidate),
            "index_resource_sha256": evidence_sha256,
            **_binary_certification(),
        }
        manifest["binary_certified"] = bool(manifest["binary_sha256"])
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
    except Exception:  # noqa: BLE001 - indexing failure means GT dormant, never a crash
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


def ensure_index(root: str, *, state_dir: str | None = None) -> str | None:
    """Build/reuse one graph under an inter-process publication lock.

    Correct-or-quiet for local work; fail-closed for a benchmark-bound run,
    where an absent graph is a defect rather than a degraded mode.
    """

    graph: str | None = None
    # Whether there was source to index at all. A task that starts empty has
    # nothing to build from yet and fills as the agent creates files; a task
    # holding source and producing no graph is a defect.
    indexable = bool(root and os.path.isdir(root) and is_code_repo(root))
    try:
        if indexable:
            gt_dir = _graph_state_dir(root, state_dir)
            gt_dir.mkdir(parents=True, exist_ok=True)
            with _graph_publication_lock(gt_dir / ".graph.lock"):
                graph = _ensure_index_unlocked(root, state_dir=state_dir)
    except Exception:  # noqa: BLE001 - indexing remains correct-or-quiet
        graph = None
    if (
        graph is None
        and indexable
        and _execution_identity()["identity_scope"] == "benchmark_bound"
    ):
        raise BenchmarkGraphRequired(
            "benchmark run has no graph; refusing to measure a treatment that "
            "cannot use the mechanism under test"
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

    @property
    def success(self) -> bool:
        return self.status is IndexBuildStatus.BUILT and bool(self.graph_db)

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
    resource_path = graph.with_name("index-resource.json")
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
        resource.get("status") != "completed"
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
            os.environ.get("GT_TASK_ID")
            and resource.get("task_id") != os.environ["GT_TASK_ID"]
        )
        or (
            os.environ.get("GT_PRODUCT_SOURCE_SHA")
            and resource.get("product_source_sha")
            != os.environ["GT_PRODUCT_SOURCE_SHA"]
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


def ensure_index_with_receipt(root: str | Path, *, state_dir: str | Path | None = None,
                              source_revision: str = "") -> IndexBuildReceipt:
    root_path = Path(root)
    if not root_path.is_dir() or not is_code_repo(str(root_path)):
        return IndexBuildReceipt(IndexBuildStatus.NOT_APPLICABLE, source_revision=source_revision)
    try:
        graph = ensure_index(str(root_path), state_dir=str(state_dir) if state_dir else None)
    except BenchmarkGraphRequired:
        # A benchmark without its graph is not a receipt outcome to record and
        # continue from; it stops the run.
        raise
    except Exception as exc:  # pragma: no cover - defensive boundary
        return IndexBuildReceipt(IndexBuildStatus.BUILD_FAILED, source_revision=source_revision,
                                 error_type=type(exc).__name__, error_diagnostic=str(exc)[:600])
    if not graph:
        gt_dir = _graph_state_dir(root_path, state_dir)
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
    phase = _graph_phase_metadata(graph_path)
    analysis_state = str(phase["analysis_state"])
    status = (
        IndexBuildStatus.BUILT_CORE_ONLY
        if analysis_state in {"failed", "not_run"}
        else IndexBuildStatus.BUILT
    )
    # Refresh the contract-embedding sidecar after every successful build.
    # A failure here is logged but never costs the graph — the embedding store
    # is a cache the retrieval side can degrade from with a named reason.
    if status in (IndexBuildStatus.BUILT, IndexBuildStatus.BUILT_CORE_ONLY):
        try:
            from gt_engine.contract_embeddings import refresh as _refresh_embeddings
            _refresh_embeddings(str(graph_path))
        except Exception:
            pass  # named-degraded at retrieval time

    return IndexBuildReceipt(status, graph_db=graph,
                             source_revision=source_revision, graph_revision=graph_revision,
                             analysis_state=analysis_state,
                             analysis_failure_reason=str(phase["analysis_failure_reason"]))


def refresh_index_files(root: str | Path, graph: str | Path, changed_paths: tuple[str, ...], *,
                        source_revision: str = "") -> IndexBuildReceipt:
    # The current producer has no incremental command boundary. Rebuild into a
    # temporary state directory so the previous complete graph remains readable.
    del graph, changed_paths
    return ensure_index_with_receipt(root, source_revision=source_revision)
