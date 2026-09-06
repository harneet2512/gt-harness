"""Report a local coding agent's activity to the GT cloud UI.

One dependency-free module (standard library only, Python >= 3.10). It is copied
onto a user's machine and imported by the Claude Code hook, the Codex tailer and
the generic JSONL tailer; it is never imported by ``cloud/server``.

The contract it speaks is three routes:

- ``POST {origin}/api/sessions/{sid}/external-agents`` — register, with the
  user's JWT.
- ``POST {ingest_url}`` — stream events, with the ingest token.
- ``POST {origin}/api/external-agents/{aid}/finish`` — close the card, with the
  ingest token.

Two rules shape every line below.

**It must never make the host agent fail.** Every public entry point returns a
value and swallows its own exceptions; the only place an exception is visible is
the debug log, and that is written only when ``GT_CLOUD_DEBUG=1``. Every network
call has an explicit timeout of at most ``GT_CLOUD_TIMEOUT`` seconds (default
3.0). A hook that cannot reach the server costs the user three seconds and
reports nothing; it does not break their editor.

**Paths are repo-relative or they are dropped.** The server rejects absolute
paths and ``..``. :func:`to_repo_relative` is the single place that conversion
happens, and it is applied inside :meth:`Bridge.tool_call` and
:meth:`Bridge.tool_result` so no caller can forget it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

__all__ = [
    "Bridge",
    "BridgeConfig",
    "breaker_is_open",
    "debug",
    "extract_paths_from_command",
    "read_registration",
    "record_breaker_failure",
    "record_breaker_success",
    "repo_relative_many",
    "reset_breaker",
    "to_repo_relative",
    "truncate",
]

# --- limits -----------------------------------------------------------------
# The server's documented batch ceiling. We stay under both, always.
MAX_EVENTS_PER_BATCH = 100
MAX_BATCH_BYTES = 256 * 1024

# Per-event caps applied at emit() time, so a batch can never be oversized by a
# single enormous tool output. These are display budgets, not security limits.
MAX_TEXT_CHARS = 4000
MAX_OUTPUT_CHARS = 4000
MAX_COMMAND_CHARS = 2000
MAX_NOTE_CHARS = 500
MAX_ACTIVITY_CHARS = 200
MAX_FILES_PER_EVENT = 64

DEFAULT_QUEUE_MAX = 2000
DEFAULT_FLUSH_INTERVAL = 1.5
DEFAULT_TIMEOUT = 3.0
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF = 0.4
STATE_TTL_SECONDS = 24 * 3600
LOCK_STALE_SECONDS = 15.0

# Hook mode is on the critical path of somebody else's tool call, so it gets a
# tighter budget than the tailers, which are not in anybody's way.
HOOK_TIMEOUT = 1.5
HOOK_RETRIES = 0

# The circuit breaker. A hook process is fresh every time and remembers nothing,
# so "the deployment is down" has to be written down somewhere both processes
# can see. Without it, an unreachable server costs every tool call in a local
# session the full timeout, for as long as the server stays down - and nobody
# keeps a hook installed through that.
DEFAULT_BREAKER_SECONDS = 300.0
BREAKER_FAILURE_THRESHOLD = 3
BREAKER_FATAL_MULTIPLIER = 4
# Statuses that no amount of retrying can fix: a revoked token, a deleted agent.
FATAL_STATUSES = (401, 403, 404, 410)

VALID_KINDS = ("claude-code", "codex", "other")
VALID_STATES = ("working", "idle", "done", "error")
VALID_FINISH = ("done", "error")

_USER_AGENT = "gt-cloud-bridge/1"


# --- debug log --------------------------------------------------------------

_debug_lock = threading.Lock()


def _debug_enabled() -> bool:
    return os.environ.get("GT_CLOUD_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _debug_path() -> str:
    override = os.environ.get("GT_CLOUD_DEBUG_LOG", "").strip()
    if override:
        return override
    return os.path.join(tempfile.gettempdir(), "gt-cloud-adapter.log")


def debug(message: str, exc: BaseException | None = None) -> None:
    """Append one line to the debug log. Silent unless ``GT_CLOUD_DEBUG=1``.

    This function is the bottom of every ``except`` in this package, so it must
    not raise under any circumstance.
    """
    if not _debug_enabled():
        return
    try:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        line = f"{stamp} [{os.getpid()}] {message}"
        if exc is not None:
            line += f" | {type(exc).__name__}: {exc}"
        with _debug_lock, open(_debug_path(), "a", encoding="utf-8", errors="replace") as handle:
            handle.write(line + "\n")
    except Exception:  # pragma: no cover - the log is best effort by definition
        pass


def truncate(value: Any, limit: int) -> str | None:
    """Coerce to text and cap it, marking the cut so nobody reads it as complete."""
    if value is None:
        return None
    try:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
    except Exception:
        text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [{len(text) - limit} more characters]"


# --- paths ------------------------------------------------------------------


def to_repo_relative(path: Any, cwd: Any) -> str | None:
    """Convert *path* to a forward-slash path relative to *cwd*, or ``None``.

    ``None`` means "do not report this": the path is empty, on another drive,
    outside *cwd*, or *is* *cwd*. Resolution is lexical (``os.path.normpath``),
    not filesystem-based, so a path that does not exist yet — the file an Edit is
    about to create — still converts, and a symlink is not silently followed out
    of the repository.
    """
    if not path or not cwd:
        return None
    try:
        raw = str(path).strip().strip('"').strip("'").strip()
        if not raw:
            return None
        base = os.path.normpath(str(cwd))
        target = raw if os.path.isabs(raw) else os.path.join(base, raw)
        target = os.path.normpath(target)
        try:
            rel = os.path.relpath(target, base)
        except ValueError:
            # Windows: two different drives have no relative path between them.
            return None
        if rel in (os.curdir, ""):
            return None
        rel = rel.replace("\\", "/")
        if rel == ".." or rel.startswith("../") or "/../" in rel:
            return None
        return rel
    except Exception as exc:  # pragma: no cover - defensive
        debug("to_repo_relative failed", exc)
        return None


def repo_relative_many(paths: Iterable[Any] | None, cwd: Any) -> list[str]:
    """Convert an iterable of paths, dropping the ones outside *cwd*, keeping order."""
    if not paths:
        return []
    seen: dict[str, None] = {}
    try:
        for item in paths:
            rel = to_repo_relative(item, cwd)
            if rel is not None and rel not in seen:
                seen[rel] = None
            if len(seen) >= MAX_FILES_PER_EVENT:
                break
    except Exception as exc:  # pragma: no cover - defensive
        debug("repo_relative_many failed", exc)
    return list(seen)


# Extensions that make a bare token (no separator in it) worth treating as a file.
_PATHY_SUFFIXES = (
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".yaml",
    ".yml", ".toml", ".ini", ".cfg", ".md", ".rst", ".txt", ".sh", ".bash", ".zsh",
    ".go", ".rs", ".java", ".kt", ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".cs", ".swift", ".sql", ".html", ".css", ".scss", ".lock", ".tf", ".proto",
    ".ipynb", ".gradle", ".xml", ".csv", ".tsv",
)


def _looks_like_path(token: str) -> bool:
    if not token or token.startswith("-"):
        return False
    if token[0] in "$`|&;()<>*":
        return False
    if "://" in token:
        return False
    if "/" in token or "\\" in token:
        return True
    return token.lower().endswith(_PATHY_SUFFIXES)


def extract_paths_from_command(command: Any, cwd: Any) -> list[str]:
    """Guess which repository files a shell command touches. Best effort, and wrong.

    It splits the command with :mod:`shlex`, keeps the tokens that look like paths
    (they contain a separator or end in a known source extension), and hands them
    to :func:`to_repo_relative`, which drops everything outside *cwd*. It does not
    understand the command's semantics: ``grep -r foo src/`` reports ``src`` and
    ``cat a.py > b.py`` reports both, without knowing which was read and which was
    written. Treat the result as a hint about *where* the agent is working, never
    as a record of what changed.
    """
    if not command:
        return []
    try:
        text = str(command)
        try:
            tokens = shlex.split(text, posix=True)
        except ValueError:
            tokens = text.replace("'", " ").replace('"', " ").split()
        return repo_relative_many([tok for tok in tokens if _looks_like_path(tok)], cwd)
    except Exception as exc:
        debug("extract_paths_from_command failed", exc)
        return []


# --- configuration ----------------------------------------------------------


def _env_float(source: Any, name: str, default: float) -> float:
    try:
        raw = str(source.get(name, "")).strip()
        return float(raw) if raw else default
    except Exception:
        return default


def _env_int(source: Any, name: str, default: int) -> int:
    try:
        raw = str(source.get(name, "")).strip()
        return int(raw) if raw else default
    except Exception:
        return default


@dataclass
class BridgeConfig:
    """Where to post and how hard to try. Built from the environment.

    Two ways to authenticate, in this order:

    1. ``GT_CLOUD_AGENT_TOKEN`` + ``GT_CLOUD_AGENT_ID`` — an already-registered
       agent. Registration is skipped entirely and events stream straight in.
       This is what a subagent process, or a second machine, is handed.
    2. ``GT_CLOUD_TOKEN`` (a user JWT) + ``GT_CLOUD_SESSION`` — register first,
       then stream with the ingest token the server hands back.

    ``GT_CLOUD_ORIGIN`` is required either way.
    """

    origin: str = ""
    session_id: str = ""
    user_token: str = ""
    agent_token: str = ""
    agent_id: str = ""
    timeout: float = DEFAULT_TIMEOUT
    flush_interval: float = DEFAULT_FLUSH_INTERVAL
    queue_max: int = DEFAULT_QUEUE_MAX
    retries: int = DEFAULT_RETRIES
    backoff: float = DEFAULT_BACKOFF
    state_dir: str = ""
    breaker_seconds: float = DEFAULT_BREAKER_SECONDS

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, hook_mode: bool = False) -> BridgeConfig:
        """Read the configuration. *hook_mode* tightens the budget.

        A hook runs inside somebody's tool call, so it gets one attempt and 1.5
        seconds. A tailer runs beside the session and can afford to be patient.
        """
        source = os.environ if env is None else env
        ceiling = HOOK_TIMEOUT if hook_mode else DEFAULT_TIMEOUT
        default_retries = HOOK_RETRIES if hook_mode else DEFAULT_RETRIES
        return cls(
            origin=str(source.get("GT_CLOUD_ORIGIN", "")).strip().rstrip("/"),
            session_id=str(source.get("GT_CLOUD_SESSION", "")).strip(),
            user_token=str(source.get("GT_CLOUD_TOKEN", "")).strip(),
            agent_token=str(source.get("GT_CLOUD_AGENT_TOKEN", "")).strip(),
            agent_id=str(source.get("GT_CLOUD_AGENT_ID", "")).strip(),
            # Capped, not merely defaulted: no configuration may make a network
            # call block the host agent for longer than the mode's ceiling.
            timeout=min(_env_float(source, "GT_CLOUD_TIMEOUT", ceiling), ceiling),
            flush_interval=_env_float(source, "GT_CLOUD_FLUSH_INTERVAL", DEFAULT_FLUSH_INTERVAL),
            queue_max=_env_int(source, "GT_CLOUD_QUEUE_MAX", DEFAULT_QUEUE_MAX),
            retries=_env_int(source, "GT_CLOUD_RETRIES", default_retries),
            backoff=_env_float(source, "GT_CLOUD_BACKOFF", DEFAULT_BACKOFF),
            state_dir=str(source.get("GT_CLOUD_STATE_DIR", "")).strip(),
            breaker_seconds=_env_float(
                source, "GT_CLOUD_BREAKER_SECONDS", DEFAULT_BREAKER_SECONDS
            ),
        )

    @property
    def preauthorised(self) -> bool:
        """True when we already hold an ingest token and must not register."""
        return bool(self.agent_token and self.agent_id)

    def usable(self) -> tuple[bool, str]:
        if not self.origin:
            return False, "GT_CLOUD_ORIGIN is unset"
        if self.preauthorised:
            return True, ""
        if not self.user_token:
            return False, "neither GT_CLOUD_AGENT_TOKEN nor GT_CLOUD_TOKEN is set"
        if not self.session_id:
            return False, "GT_CLOUD_SESSION is unset"
        return True, ""

    def resolved_state_dir(self) -> str:
        if self.state_dir:
            return self.state_dir
        return os.path.join(tempfile.gettempdir(), "gt-cloud-adapters")


# --- registration state file ------------------------------------------------


def _state_file(config: BridgeConfig, state_key: str) -> str:
    digest = hashlib.sha256(
        "\x00".join([config.origin, config.session_id, state_key]).encode("utf-8")
    ).hexdigest()[:32]
    return os.path.join(config.resolved_state_dir(), f"agent-{digest}.json")


def _read_state(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return None
        if time.time() - float(data.get("created_at", 0)) > STATE_TTL_SECONDS:
            return None
        if not data.get("agent_id") or not data.get("ingest_token"):
            return None
        return data
    except FileNotFoundError:
        return None
    except Exception as exc:
        debug(f"state read failed: {path}", exc)
        return None


def _write_state(path: str, data: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, path)
    except Exception as exc:
        debug(f"state write failed: {path}", exc)


def read_registration(config: BridgeConfig, state_key: str) -> dict[str, Any] | None:
    """The registration a previous process wrote for *state_key*, or ``None``.

    A hook process is short-lived and fires once per tool call, so this is how
    the twentieth invocation finds the agent the first one registered — and how a
    subagent's hook finds its parent's agent id to nest under.
    """
    if not state_key:
        return None
    try:
        return _read_state(_state_file(config, state_key))
    except Exception as exc:  # pragma: no cover - defensive
        debug("read_registration failed", exc)
        return None


# --- the circuit breaker ----------------------------------------------------
#
# Keyed by **origin**, not by agent: if the deployment is unreachable it is
# unreachable for every card, and every hook process should learn that from the
# first few that found out. The state lives beside the registrations because a
# hook process is born, reports, and dies - there is nowhere else to put it.


def _breaker_path(config: BridgeConfig) -> str:
    digest = hashlib.sha256(config.origin.encode("utf-8")).hexdigest()[:32]
    return os.path.join(config.resolved_state_dir(), f"breaker-{digest}.json")


def _read_breaker(config: BridgeConfig) -> dict[str, Any]:
    try:
        with open(_breaker_path(config), encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        # A truncated or unreadable breaker file means "no memory", never a
        # failure: a corrupt cache must not be able to stop the host agent.
        debug("breaker read failed", exc)
        return {}


def _write_breaker(config: BridgeConfig, data: dict[str, Any]) -> None:
    path = _breaker_path(config)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, path)
    except Exception as exc:
        debug("breaker write failed", exc)


def breaker_is_open(config: BridgeConfig) -> bool:
    """True when we have given up on this origin for now and must not call it.

    Reading one small file is the entire cost of a reporting attempt while the
    deployment is down. When ``open_until`` has passed, this returns ``False``
    once so the next attempt acts as a probe: if it succeeds the breaker closes,
    and if it fails the failure count is already at the threshold, so it
    re-opens immediately.
    """
    try:
        if config.breaker_seconds <= 0:
            return False
        open_until = float(_read_breaker(config).get("open_until") or 0)
        return time.time() < open_until
    except Exception as exc:  # pragma: no cover - defensive
        debug("breaker check failed", exc)
        return False


def record_breaker_success(config: BridgeConfig) -> None:
    """The origin answered. Forget every failure before it."""
    try:
        if config.breaker_seconds > 0 and _read_breaker(config):
            _write_breaker(config, {"failures": 0, "open_until": 0})
    except Exception as exc:  # pragma: no cover - defensive
        debug("breaker success failed", exc)


def record_breaker_failure(config: BridgeConfig, fatal: bool = False) -> bool:
    """The origin failed. Returns whether that opened the breaker.

    *fatal* is for the statuses retrying cannot fix - a revoked token, a deleted
    agent. Those open the breaker at once, and for longer, because there is no
    upside to asking again soon.
    """
    try:
        if config.breaker_seconds <= 0:
            return False
        state = _read_breaker(config)
        failures = int(state.get("failures") or 0) + 1
        window = config.breaker_seconds * (BREAKER_FATAL_MULTIPLIER if fatal else 1)
        if fatal or failures >= BREAKER_FAILURE_THRESHOLD:
            _write_breaker(
                config,
                {"failures": failures, "open_until": time.time() + window,
                 "reason": "fatal" if fatal else "consecutive failures"},
            )
            debug(f"circuit breaker open for {window:.0f}s after {failures} failures")
            return True
        _write_breaker(config, {"failures": failures, "open_until": 0})
        return False
    except Exception as exc:  # pragma: no cover - defensive
        debug("breaker failure failed", exc)
        return False


def reset_breaker(config: BridgeConfig) -> None:
    """Forget the breaker entirely. For tests and for a manual retry."""
    try:
        os.remove(_breaker_path(config))
    except FileNotFoundError:
        pass
    except Exception as exc:
        debug("breaker reset failed", exc)


class _RegistrationLock:
    """A directory used as a mutex, so two hook processes register once.

    ``os.mkdir`` is atomic on every filesystem we care about. A lock older than
    ``LOCK_STALE_SECONDS`` is assumed to belong to a process that died and is
    taken over: a duplicate card is a far smaller problem than a wedged hook.
    """

    def __init__(self, path: str, wait: float) -> None:
        self._path = path + ".lock"
        self._wait = wait
        self.held = False

    def __enter__(self) -> _RegistrationLock:
        deadline = time.monotonic() + self._wait
        while True:
            try:
                os.makedirs(os.path.dirname(self._path), exist_ok=True)
                os.mkdir(self._path)
                self.held = True
                return self
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self._path)
                    if age > LOCK_STALE_SECONDS:
                        os.rmdir(self._path)
                        continue
                except Exception:
                    pass
                if time.monotonic() >= deadline:
                    return self
                time.sleep(0.05)
            except Exception as exc:
                debug("registration lock failed", exc)
                return self

    def __exit__(self, *_exc: Any) -> None:
        if self.held:
            try:
                os.rmdir(self._path)
            except Exception:
                pass
            self.held = False


# --- HTTP -------------------------------------------------------------------


@dataclass
class _Response:
    status: int
    body: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> dict[str, Any]:
        try:
            data = json.loads(self.body.decode("utf-8", errors="replace"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> _Response:
    """POST JSON. Network failure comes back as status 0, never as an exception."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", _USER_AGENT)
    for key, value in headers.items():
        if value:
            request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return _Response(int(getattr(response, "status", 200) or 200), response.read())
    except urllib.error.HTTPError as exc:
        try:
            payload_bytes = exc.read()
        except Exception:
            payload_bytes = b""
        return _Response(int(exc.code), payload_bytes)
    except Exception as exc:
        debug(f"POST {url} failed", exc)
        return _Response(0, b"")


# --- the bridge -------------------------------------------------------------


class Bridge:
    """Register one external agent, then stream its events until :meth:`finish`.

    Lifecycle::

        bridge = Bridge(agent_kind="claude-code", label="claude-code @ my-repo",
                        cwd=os.getcwd(), state_key=session_id)
        if bridge.start():
            bridge.tool_call("Edit", files=["/abs/path/in/repo.py"])
            bridge.finish("done", "3 files changed")

    Everything above returns ``False``/``None`` rather than raising when the
    server is unreachable, misconfigured or hostile. ``bridge.enabled`` says
    whether anything is actually being reported.
    """

    def __init__(
        self,
        agent_kind: str = "other",
        label: str = "external agent",
        task: str | None = None,
        cwd: str | None = None,
        parent_agent_id: str | None = None,
        state_key: str = "",
        config: BridgeConfig | None = None,
        background: bool = True,
    ) -> None:
        self.config = config or BridgeConfig.from_env()
        self.agent_kind = agent_kind if agent_kind in VALID_KINDS else "other"
        self.label = str(label or "external agent")[:200]
        self.task = truncate(task, 2000)
        self.cwd = os.path.normpath(cwd) if cwd else None
        self.parent_agent_id = parent_agent_id or None
        self.state_key = state_key or ""
        self.agent_id: str | None = None
        self.ingest_url: str | None = None
        self.ingest_token: str | None = None
        self.dropped = 0
        self.posted_batches = 0
        self.reused_registration = False
        self.enabled = False
        self._background = background
        self._queue: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        # One POST in flight at a time. finish() flushes on the caller's thread
        # while the flush thread may still be sending; without this the two
        # batches race and the server sees them out of order - which showed up
        # as an apparently non-monotonic token count on a real Codex session.
        self._send_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._unreported_drops = 0
        self._gave_up = False
        self._tokens: int | None = None
        self._last_activity: str | None = None
        self.breaker_open = False

    # -- public API ----------------------------------------------------------

    def start(self) -> bool:
        """Register (or reuse a registration) and start the flush thread.

        Returns ``True`` when the bridge will report. Never raises.
        """
        try:
            usable, why = self.config.usable()
            if not usable:
                debug(f"bridge disabled: {why}")
                return False
            if breaker_is_open(self.config):
                # The whole point: this costs one small file read, not a timeout.
                self.breaker_open = True
                debug("circuit breaker open; reporting nothing this time")
                return False
            if self.config.preauthorised:
                self.agent_id = self.config.agent_id
                self.ingest_token = self.config.agent_token
                self.ingest_url = self._events_url(self.agent_id)
            elif not self._register():
                return False
            self.enabled = True
            if self._background:
                self._thread = threading.Thread(
                    target=self._run, name="gt-cloud-bridge", daemon=True
                )
                self._thread.start()
            return True
        except Exception as exc:
            debug("bridge.start failed", exc)
            self.enabled = False
            return False

    def emit(self, event: dict[str, Any]) -> bool:
        """Queue one contract event. Drops the oldest when the queue is full."""
        try:
            if not self.enabled or self._gave_up:
                return False
            shrunk = self._shrink(event)
            if shrunk is None:
                return False
            with self._lock:
                while len(self._queue) >= max(1, self.config.queue_max):
                    self._queue.popleft()
                    self.dropped += 1
                    self._unreported_drops += 1
                self._queue.append(shrunk)
            self._wake.set()
            return True
        except Exception as exc:
            debug("bridge.emit failed", exc)
            return False

    def assistant(self, text: Any) -> bool:
        return self.emit({"type": "assistant", "text": text})

    def tool_call(
        self,
        name: Any,
        command: Any = None,
        files: Iterable[Any] | None = None,
        activity: Any = None,
    ) -> bool:
        return self.emit(
            {
                "type": "tool_call",
                "name": name,
                "command": command,
                "files": list(files or []),
                "activity": activity,
            }
        )

    def tool_result(
        self,
        name: Any,
        ok: bool = True,
        output: Any = None,
        files: Iterable[Any] | None = None,
    ) -> bool:
        return self.emit(
            {
                "type": "tool_result",
                "name": name,
                "ok": bool(ok),
                "output": output,
                "files": list(files or []),
            }
        )

    def status(
        self,
        state: str,
        note: Any = None,
        activity: Any = None,
        tokens: Any = None,
    ) -> bool:
        return self.emit(
            {
                "type": "status",
                "state": state,
                "note": note,
                "activity": activity,
                "tokens": tokens,
            }
        )

    def flush(self, deadline: float | None = None) -> int:
        """Post everything queued now, ignoring the coalescing interval.

        Returns the number of events posted. Bounded by *deadline* seconds so a
        hook can never hang on it.
        """
        posted = 0
        try:
            limit = time.monotonic() + (deadline if deadline is not None else self.config.timeout * 2)
            while self.enabled and not self._gave_up:
                sent = self._take_and_send()
                if sent <= 0:  # nothing left, or the send failed
                    break
                posted += sent
                if time.monotonic() >= limit:
                    break
        except Exception as exc:
            debug("bridge.flush failed", exc)
        return posted

    def finish(self, status: str = "done", summary: Any = None, deadline: float = 5.0) -> bool:
        """Flush, then close the card. Safe to call twice; safe to call never."""
        try:
            if not self.enabled:
                self.close()
                return False
            self._stop.set()
            self._wake.set()
            self.flush(deadline=deadline)
            state = status if status in VALID_FINISH else "done"
            note = truncate(summary, MAX_TEXT_CHARS)
            if self.dropped:
                dropped_note = f"[gt-cloud-bridge dropped {self.dropped} events]"
                # The warning outranks the prose: room is reserved for it, and
                # the summary is cut to fit around it. A reader who is told
                # nothing was lost when something was is worse off than one who
                # gets a shorter summary.
                room = max(0, MAX_TEXT_CHARS - len(dropped_note) - 1)
                head = (note or "")[:room]
                note = f"{head}\n{dropped_note}" if head else dropped_note
            # A hard cap on the composed string. `truncate` marks its cut by
            # appending "... [N more characters]" **past** the limit, so a long
            # summary at the limit and then adding a line put the payload one
            # line over, the server rejected it, and because finish is the only
            # thing that settles a card the agent stayed "running" for ever with
            # its last activity reading "Finished". Found on a live run.
            note = note[:MAX_TEXT_CHARS] if note else note
            if breaker_is_open(self.config):
                debug("circuit breaker open; not posting finish")
                self.breaker_open = True
                self.close()
                return False
            url = f"{self.config.origin}/api/external-agents/{self.agent_id}/finish"
            response = _post_json(
                url,
                {"status": state, "summary": note},
                self._ingest_headers(),
                self.config.timeout,
            )
            self._note_outcome(response)
            if not response.ok:
                debug(f"finish returned {response.status}")
            self._clear_state()
            self.close()
            return response.ok
        except Exception as exc:
            debug("bridge.finish failed", exc)
            self.close()
            return False

    def close(self) -> None:
        """Stop the flush thread without closing the card on the server."""
        try:
            self._stop.set()
            self._wake.set()
            thread = self._thread
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=self.config.timeout)
            self._thread = None
            self.enabled = False
        except Exception as exc:  # pragma: no cover - defensive
            debug("bridge.close failed", exc)

    def child_env(self) -> dict[str, str]:
        """Environment a child process needs to stream into *this* agent's card."""
        if not (self.agent_id and self.ingest_token):
            return {}
        return {
            "GT_CLOUD_ORIGIN": self.config.origin,
            "GT_CLOUD_AGENT_ID": self.agent_id,
            "GT_CLOUD_AGENT_TOKEN": self.ingest_token,
        }

    # -- registration --------------------------------------------------------

    def _events_url(self, agent_id: str | None) -> str:
        return f"{self.config.origin}/api/external-agents/{agent_id}/events"

    def _register(self) -> bool:
        state_path = _state_file(self.config, self.state_key) if self.state_key else ""
        if state_path:
            cached = _read_state(state_path)
            if cached:
                self._adopt(cached)
                self.reused_registration = True
                return True
        with _RegistrationLock(state_path, wait=2.0) if state_path else _NullLock():
            if state_path:
                cached = _read_state(state_path)
                if cached:
                    self._adopt(cached)
                    self.reused_registration = True
                    return True
            payload = {
                "agent_kind": self.agent_kind,
                "label": self.label,
                "task": self.task,
                "cwd": self.cwd,
                "parent_agent_id": self.parent_agent_id,
            }
            url = f"{self.config.origin}/api/sessions/{self.config.session_id}/external-agents"
            response = _post_json(url, payload, self._user_headers(), self.config.timeout)
            self._note_outcome(response)
            if not response.ok:
                debug(f"registration returned {response.status}")
                return False
            data = response.json()
            agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
            record = {
                "agent_id": agent.get("id") or data.get("agent_id"),
                "ingest_token": data.get("ingest_token"),
                "ingest_url": data.get("ingest_url"),
                "origin": self.config.origin,
                "session_id": self.config.session_id,
                "created_at": time.time(),
            }
            if not record["agent_id"] or not record["ingest_token"]:
                debug("registration response missing agent id or ingest token")
                return False
            self._adopt(record)
            if state_path:
                _write_state(state_path, record)
            return True

    def _adopt(self, record: dict[str, Any]) -> None:
        self.agent_id = str(record.get("agent_id"))
        self.ingest_token = str(record.get("ingest_token"))
        raw_url = str(record.get("ingest_url") or "")
        if raw_url.startswith("http://") or raw_url.startswith("https://"):
            self.ingest_url = raw_url
        elif raw_url:
            self.ingest_url = urljoin(self.config.origin + "/", raw_url.lstrip("/"))
        else:
            self.ingest_url = self._events_url(self.agent_id)

    def _clear_state(self) -> None:
        if not self.state_key:
            return
        try:
            os.remove(_state_file(self.config, self.state_key))
        except FileNotFoundError:
            pass
        except Exception as exc:
            debug("state clear failed", exc)

    def _user_headers(self) -> dict[str, str]:
        # The server accepts either credential and prefers the header; the cookie
        # is sent as well so a deployment that only reads the cookie still works.
        return {
            "Authorization": f"Bearer {self.config.user_token}",
            "Cookie": f"session={self.config.user_token}",
        }

    def _ingest_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.ingest_token or ''}"}

    # -- queue and transport -------------------------------------------------

    def _monotonic_tokens(self, value: Any) -> int | None:
        """A cumulative token count, or ``None``. Never decreases, never invented."""
        if value is None:
            return None
        try:
            count = int(value)
        except (TypeError, ValueError):
            return None
        if count < 0:
            return None
        if self._tokens is not None and count < self._tokens:
            # The server ignores a decrease; do not spend a byte sending one.
            return None
        self._tokens = count
        return count

    def _shrink(self, event: Any) -> dict[str, Any] | None:
        """Validate the event against the contract and cap its fields."""
        if not isinstance(event, dict):
            return None
        kind = event.get("type")
        if kind == "assistant":
            text = truncate(event.get("text"), MAX_TEXT_CHARS)
            return {"type": "assistant", "text": text or ""}
        if kind == "tool_call":
            shrunk = {
                "type": "tool_call",
                "name": str(event.get("name") or "tool")[:120],
                "command": truncate(event.get("command"), MAX_COMMAND_CHARS),
                "files": repo_relative_many(event.get("files"), self.cwd),
                "activity": truncate(event.get("activity"), MAX_ACTIVITY_CHARS),
            }
            self._last_activity = shrunk["activity"] or self._last_activity
            return shrunk
        if kind == "tool_result":
            return {
                "type": "tool_result",
                "name": str(event.get("name") or "tool")[:120],
                "ok": bool(event.get("ok", True)),
                "output": truncate(event.get("output"), MAX_OUTPUT_CHARS),
                "files": repo_relative_many(event.get("files"), self.cwd),
            }
        if kind == "status":
            state = event.get("state")
            if state not in VALID_STATES:
                state = "working"
            shrunk = {
                "type": "status",
                "state": state,
                "note": truncate(event.get("note"), MAX_NOTE_CHARS),
                "activity": truncate(event.get("activity"), MAX_ACTIVITY_CHARS),
            }
            # `tokens` is omitted, never guessed: a host that reports no usage
            # must not produce a number the UI would show as if it were measured.
            tokens = self._monotonic_tokens(event.get("tokens"))
            if tokens is not None:
                shrunk["tokens"] = tokens
            return shrunk
        debug(f"dropping event with unknown type: {kind!r}")
        return None

    def _take_batch(self) -> list[dict[str, Any]]:
        """Pop up to one batch, honouring both the count and the byte ceiling."""
        batch: list[dict[str, Any]] = []
        size = 32  # {"events": []} plus slack
        with self._lock:
            if self._unreported_drops:
                notice = {
                    "type": "status",
                    "state": "working",
                    "note": f"gt-cloud-bridge dropped {self._unreported_drops} events (queue full)",
                }
                self._unreported_drops = 0
                batch.append(notice)
                size += len(json.dumps(notice)) + 1
            while self._queue and len(batch) < MAX_EVENTS_PER_BATCH:
                candidate = self._queue[0]
                try:
                    candidate_size = len(json.dumps(candidate)) + 1
                except Exception:
                    self._queue.popleft()
                    continue
                if batch and size + candidate_size > MAX_BATCH_BYTES:
                    break
                self._queue.popleft()
                batch.append(candidate)
                size += candidate_size
        return batch

    def _take_and_send(self) -> int:
        """Pop one batch and post it, as one atomic step. Returns events posted.

        Taking and sending must happen under the same lock. ``finish()`` flushes
        on the caller's thread while the flush thread may still be working; if
        the two only serialised the POST, the second thread could still take the
        *later* batch and win the race to send it. That reordering showed up on a
        real Codex session as an apparently non-monotonic token count.
        """
        with self._send_lock:
            batch = self._take_batch()
            if not batch:
                return 0
            return len(batch) if self._send(batch) else -1

    def _note_outcome(self, response: _Response) -> None:
        """Feed one completed operation's result to the breaker.

        Recorded per *operation*, not per retry attempt: three attempts at one
        batch is one piece of evidence that the origin is down, not three.
        A plain 4xx (a malformed batch, say) is our bug and not the deployment's,
        so it neither opens the breaker nor resets it.
        """
        if response.ok:
            record_breaker_success(self.config)
        elif response.status in FATAL_STATUSES:
            record_breaker_failure(self.config, fatal=True)
        elif response.status == 0 or response.status >= 500:
            record_breaker_failure(self.config)

    def _send(self, batch: list[dict[str, Any]]) -> bool:
        """POST one batch, retrying 5xx and network failures, giving up on 4xx."""
        if breaker_is_open(self.config):
            debug("circuit breaker open; dropping batch without a request")
            self.breaker_open = True
            self.dropped += len(batch)
            return False
        url = self.ingest_url or self._events_url(self.agent_id)
        headers = self._ingest_headers()
        attempts = max(0, self.config.retries) + 1
        for attempt in range(attempts):
            response = _post_json(url, {"events": batch}, headers, self.config.timeout)
            if response.ok:
                self.posted_batches += 1
                self._note_outcome(response)
                return True
            if 400 <= response.status < 500:
                # Bad token, closed agent, malformed batch: retrying cannot help.
                debug(f"ingest refused with {response.status}; giving up quietly")
                self._note_outcome(response)
                if response.status in FATAL_STATUSES:
                    self._gave_up = True
                    with self._lock:
                        self.dropped += len(self._queue)
                        self._queue.clear()
                self.dropped += len(batch)
                return False
            if attempt < attempts - 1:
                self._sleep(self.config.backoff * (2**attempt))
        debug(f"ingest failed after {attempts} attempts (last status {response.status})")
        self._note_outcome(response)
        self.dropped += len(batch)
        return False

    def _sleep(self, seconds: float) -> None:
        # Interruptible: a finish() during backoff should not wait it out.
        self._stop.wait(min(seconds, 5.0))

    def _run(self) -> None:
        """The flush thread: at most one POST per ``flush_interval``."""
        try:
            while not self._stop.is_set():
                self._wake.wait(self.config.flush_interval)
                self._wake.clear()
                if self._stop.is_set():
                    break
                self._take_and_send()
        except Exception as exc:  # pragma: no cover - defensive
            debug("bridge flush thread died", exc)


class _NullLock:
    def __enter__(self) -> _NullLock:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None
