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
from typing import Any

from minisweagent.environments.local import LocalEnvironment, LocalEnvironmentConfig

__all__ = [
    "CloudLocalEnvironment",
    "LocalEnvironmentConfig",
    "resolve_bash",
]


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


def _is_sensitive_env_name(name: str) -> bool:
    upper = name.upper()
    return upper in _SENSITIVE_SHELL_ENV or upper.endswith(_SENSITIVE_SUFFIXES)


def _scrub_sensitive_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _scrub_sensitive_mapping(item)
            for key, item in value.items()
            if not _is_sensitive_env_name(str(key))
        }
    if isinstance(value, list):
        return [_scrub_sensitive_mapping(item) for item in value]
    return value


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

    # -- credential isolation -------------------------------------------------

    def execution_env(self) -> dict[str, str]:
        combined = os.environ | self.config.env
        return {
            key: value
            for key, value in combined.items()
            if not _is_sensitive_env_name(key)
        }

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        return _scrub_sensitive_mapping(super().get_template_vars(**kwargs))

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
        try:
            stdout, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            stdout, _ = process.communicate()
            raise subprocess.TimeoutExpired(command, timeout, output=stdout) from exc
        return subprocess.CompletedProcess(command, process.returncode, stdout=stdout)
