"""Hardened local execution environment for cloud sessions.

Two guarantees on top of mini-swe-agent's stock ``LocalEnvironment``:

1. **Credential isolation.** Provider / cloud / GitHub credentials stay
   available to the model client process but never enter model-executed shell
   commands or the template variables rendered into the prompt. The rules are
   ported verbatim from ``scripts/miniswe_gt_run.py``'s
   ``CredentialIsolatedLocalEnvironment`` (that module is not imported directly
   because importing it has heavy side effects).

2. **Real bash.** Stock ``LocalEnvironment`` runs commands with ``shell=True``,
   which is ``cmd.exe`` on Windows. The agent prompt and every mini-swe action
   template assume POSIX shell semantics, so commands are instead handed to
   ``bash -c``.

The output dict contract is identical to ``LocalEnvironment.execute``:
``{"output", "returncode", "exception_info"}`` plus an ``extra`` key on failure,
and ``_check_finished`` is called before returning so ``Submitted`` still
propagates.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from typing import Any

from minisweagent.environments.local import LocalEnvironment, LocalEnvironmentConfig

__all__ = [
    "INTERRUPT_MESSAGE",
    "INTERRUPT_RETURNCODE",
    "CloudLocalEnvironment",
    "InterruptGuard",
    "LocalEnvironmentConfig",
    "interrupted_observation",
    "is_sensitive_env_name",
    "kill_process_tree",
    "resolve_bash",
    "scrub_sensitive_mapping",
]

#: 128 + SIGKILL, the shell's own encoding of "this was killed".
INTERRUPT_RETURNCODE = 137
INTERRUPT_MESSAGE = "interrupted by user stop"


_SENSITIVE_SHELL_ENV = {
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "HF_TOKEN",
    "OPENAI_API_KEY",
}

_SENSITIVE_SUFFIXES = (
    "_API_KEY",
    "_ACCESS_TOKEN",
    "_AUTH_TOKEN",
    "_PASSWORD",
    "_SECRET",
)

_WINDOWS_BASH_FALLBACKS = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


def is_sensitive_env_name(name: str) -> bool:
    upper = name.upper()
    return upper in _SENSITIVE_SHELL_ENV or upper.endswith(_SENSITIVE_SUFFIXES)


def scrub_sensitive_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: scrub_sensitive_mapping(item)
            for key, item in value.items()
            if not is_sensitive_env_name(str(key))
        }
    if isinstance(value, list):
        return [scrub_sensitive_mapping(item) for item in value]
    return value


def interrupted_observation(partial: str) -> dict[str, Any]:
    """The observation a command killed by :meth:`interrupt` leaves behind.

    Shaped exactly like a normal failure so the transcript stays well formed:
    the agent sees a killed command, and ``run_turn`` ends the turn at the next
    step boundary because ``_stop_event`` is already set.
    """
    return {
        "output": partial,
        "returncode": INTERRUPT_RETURNCODE,
        "exception_info": INTERRUPT_MESSAGE,
    }


def kill_process_tree(process: subprocess.Popen) -> None:
    """Kill ``process`` and everything it spawned. Never raises."""
    try:
        if os.name == "posix":
            # start_new_session=True makes the child its own group leader.
            os.killpg(process.pid, signal.SIGKILL)
        else:
            subprocess.run(  # noqa: S603 - fixed binary, list argv
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True,
                timeout=15,
                check=False,
            )
    except Exception:  # noqa: BLE001 - best effort; the fallback follows
        pass
    try:
        process.kill()
    except Exception:  # noqa: BLE001 - already gone
        pass


class InterruptGuard:
    """Tracks the command in flight so a stop can kill it mid-run.

    One command runs per environment at a time (the agent loop is serial), so a
    single slot is enough. ``arm`` is called at the top of ``execute``, ``adopt``
    as soon as a process exists, and ``disarm`` reports whether an interrupt
    landed anywhere in between — including in the window before the process was
    created, which is why the flag is checked again in ``adopt``.
    """

    def __init__(self, on_interrupt: Any = None) -> None:
        self._lock = threading.Lock()
        self._requested = False
        self._process: subprocess.Popen | None = None
        self._on_interrupt = on_interrupt

    def arm(self) -> None:
        with self._lock:
            self._requested = False
            self._process = None

    def adopt(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._process = process
            pending = self._requested
        if pending:
            self._kill(process)

    def disarm(self) -> bool:
        """Detach and report whether this command was interrupted."""
        with self._lock:
            self._process = None
            return self._requested

    def request(self) -> None:
        with self._lock:
            self._requested = True
            process = self._process
        self._kill(process)

    def _kill(self, process: subprocess.Popen | None) -> None:
        if process is not None:
            kill_process_tree(process)
        if self._on_interrupt is not None:
            try:
                self._on_interrupt()
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass


def resolve_bash() -> str:
    """Absolute path to a POSIX bash, or ``"bash"`` if none can be located."""
    found = shutil.which("bash")
    if found:
        return found
    if os.name == "nt":
        for candidate in _WINDOWS_BASH_FALLBACKS:
            if os.path.isfile(candidate):
                return candidate
    return "bash"


class CloudLocalEnvironment(LocalEnvironment):
    """``LocalEnvironment`` with scrubbed credentials and bash execution."""

    def __init__(
        self,
        *,
        config_class: type = LocalEnvironmentConfig,
        bash_path: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config_class=config_class, **kwargs)
        self._bash = bash_path or resolve_bash()
        self._guard = InterruptGuard()

    # -- stop -----------------------------------------------------------------

    def interrupt(self) -> None:
        """Kill the command in flight, if any. Safe to call at any time."""
        self._guard.request()

    # -- credential isolation -------------------------------------------------

    def execution_env(self) -> dict[str, str]:
        combined = os.environ | self.config.env
        return {
            key: value
            for key, value in combined.items()
            if not is_sensitive_env_name(key)
        }

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        return scrub_sensitive_mapping(super().get_template_vars(**kwargs))

    # -- execution ------------------------------------------------------------

    def execute(
        self,
        action: dict,
        cwd: str = "",
        *,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        command = action.get("command", "")
        cwd = cwd or self.config.cwd or os.getcwd()
        self._guard.arm()
        try:
            result = self._run(
                command,
                cwd,
                self.execution_env(),
                timeout or self.config.timeout,
            )
            output = {
                "output": result.stdout,
                "returncode": result.returncode,
                "exception_info": "",
            }
        except Exception as exc:
            raw_output = getattr(exc, "output", None)
            raw_output = (
                raw_output.decode("utf-8", errors="replace")
                if isinstance(raw_output, bytes)
                else (raw_output or "")
            )
            output = {
                "output": raw_output,
                "returncode": -1,
                "exception_info": (
                    f"An error occurred while executing the command: {exc}"
                ),
                "extra": {
                    "exception_type": type(exc).__name__,
                    "exception": str(exc),
                },
            }
        finally:
            interrupted = self._guard.disarm()
        if interrupted:
            # A stop killed this command. Report the partial output rather than
            # a bash-shaped failure, so the transcript reads honestly.
            output = interrupted_observation(str(output.get("output") or ""))
        self._check_finished(output)
        return output

    def _run(
        self,
        command: str,
        cwd: str,
        env: dict[str, str],
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``command`` under bash, killing the whole group on timeout."""
        process = subprocess.Popen(
            [self._bash, "-c", command],
            shell=False,
            text=True,
            cwd=cwd,
            env=env,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
        self._guard.adopt(process)
        try:
            stdout, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            kill_process_tree(process)
            stdout, _ = process.communicate()
            raise subprocess.TimeoutExpired(command, timeout, output=stdout) from exc
        return subprocess.CompletedProcess(command, process.returncode, stdout=stdout)
