"""Mini-SWE-Agent as a Terminal-Bench 2.0 agent (GT-off and GT-on arms).

The two Harbor agents install the same pinned treatment bundle. ``MiniSweAgent``
runs the stock loop with ``--gt-off`` and never activates or imports GT in the
runner. ``MiniSweGtAgent`` activates the advisory session and forwards only the
GT state/index configuration. This makes activation—not package drift—the A/B
treatment. Mini-SWE-Agent 2.4.6 is the sole released treatment version.

``uv tool install`` does not emit a ~/.local/bin/python shim; the tool venv's
    interpreter lives inside uv's managed tool environment.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    EnvVar,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from eval._env import UTF8_ENV, provider_env
from gt_harness.indexer_setup import ensure_source_indexer

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REMOTE_DIR = "/installed-agent/miniswe"
_REMOTE_RUNNER = "/installed-agent/miniswe_run.py"
_REMOTE_REPRO = "/installed-agent/miniswe_repro.py"
_REMOTE_GT_BINARY = "/installed-agent/gt-index"
_REMOTE_PY = "$HOME/.local/share/uv/tools/gt-harness/bin/python"
_UV_VERSION = "0.11.32"
_PYTHON_VERSION = "3.12.13"
_DEFAULT_MINISWE_AGENT_VERSION = "2.4.6"
_ALLOWED_MINISWE_AGENT_VERSIONS = frozenset({"2.4.6"})
_UV_INSTALL = f"https://astral.sh/uv/{_UV_VERSION}/install.sh"
# After the uv tool install the staged checkout is removed (the tool venv holds
# the installed wheel copy). Leaving it readable lets a root task model
# discover GT's gate logic with a broad `find /` and reverse-engineer the
# submit seam (observed live: modernize-scientific-stack split the submit magic
# string across adjacent literals after importing gt_engine.miniswe_evidence).
_GT_STAGED_SOURCE_CLEANUP = (
    f"cp {_REMOTE_DIR}/scripts/miniswe_gt_run.py {_REMOTE_RUNNER} && "
    f"cp {_REMOTE_DIR}/scripts/miniswe_repro.py {_REMOTE_REPRO} && "
    f"chmod +x {_REMOTE_RUNNER} && "
    f"rm -rf -- {_REMOTE_DIR}"
)

# Task images vary (debian, alpine, ...); make sure curl exists, then let uv
# bring its own Python so we never depend on the image's python3.
_ENSURE_CURL = (
    "command -v curl >/dev/null 2>&1 || { "
    "command -v apt-get >/dev/null && apt-get update && apt-get install -y curl; } || { "
    "command -v apk >/dev/null && apk add --no-cache curl bash; } || { "
    "command -v dnf >/dev/null && dnf install -y curl; } || { "
    "command -v yum >/dev/null && yum install -y curl; }"
)


def _miniswe_agent_version() -> str:
    """Return the closed Mini-SWE treatment version for this execution."""
    version = os.environ.get(
        "MINISWE_AGENT_VERSION", _DEFAULT_MINISWE_AGENT_VERSION
    )
    if version not in _ALLOWED_MINISWE_AGENT_VERSIONS:
        allowed = ", ".join(sorted(_ALLOWED_MINISWE_AGENT_VERSIONS))
        raise ValueError(
            f"MINISWE_AGENT_VERSION must be one of: {allowed}; got {version!r}"
        )
    return version


class MiniSweAgent(BaseInstalledAgent):
    """Mini-SWE-Agent, GT-off, as a Terminal-Bench 2.0 agent."""

    @staticmethod
    def name() -> str:
        return "miniswe"

    def get_version_command(self) -> str | None:
        return f'"{_REMOTE_PY}" -c "import minisweagent; print(minisweagent.__version__)"'

    @staticmethod
    def _gt_binary_host() -> Path:
        override = os.environ.get("GT_INDEX_BINARY_HOST", "")
        if override:
            path = Path(override)
        else:
            setup = ensure_source_indexer()
            path = Path(setup.binary_path) if setup.status == "READY" else Path()
        if not path.is_file() or not str(path):
            raise FileNotFoundError(
                "Mini-SWE treatment bundle could not build gt-index from the "
                "checked-in source; run `gt-harness doctor` or set "
                "GT_INDEX_BINARY_HOST to a source-built binary for the target platform"
            )
        return path

    async def install(self, environment: BaseEnvironment) -> None:
        binary = self._gt_binary_host()
        miniswe_version = _miniswe_agent_version()
        await environment.upload_dir(_REPO_ROOT / "scripts", f"{_REMOTE_DIR}/scripts")
        await environment.upload_dir(_REPO_ROOT / "eval", f"{_REMOTE_DIR}/eval")
        await environment.upload_dir(
            _REPO_ROOT / "gt_harness", f"{_REMOTE_DIR}/gt_harness"
        )
        await environment.upload_dir(
            _REPO_ROOT / "gt_engine", f"{_REMOTE_DIR}/gt_engine"
        )
        await environment.upload_dir(
            _REPO_ROOT / "src" / "groundtruth",
            f"{_REMOTE_DIR}/src/groundtruth",
        )
        await environment.upload_file(
            _REPO_ROOT / "pyproject.toml", f"{_REMOTE_DIR}/pyproject.toml"
        )
        await environment.upload_file(binary, _REMOTE_GT_BINARY)
        await self.exec_as_root(
            environment, _ENSURE_CURL, env={"DEBIAN_FRONTEND": "noninteractive"}
        )
        await self.exec_as_root(environment, f"chmod 755 {_REMOTE_GT_BINARY}")
        install = (
            "set -eu; "
            f"curl -LsSf {_UV_INSTALL} | sh && "
            f'"$HOME/.local/bin/uv" tool install --python {_PYTHON_VERSION} '
            f'--with "mini-swe-agent=={miniswe_version}" '
            f"--with 'numpy==2.5.1' "
            f"{_REMOTE_DIR} && "
            f'"{_REMOTE_PY}" -c "import importlib.metadata as m, sys; '
            "assert sys.version_info[:3] == (3, 12, 13); "
            f"assert m.version('mini-swe-agent') == '{miniswe_version}'; "
            "assert m.version('gt-harness') == '0.9.0'; "
            "assert m.version('numpy') == '2.5.1'; "
            "import minisweagent, groundtruth, gt_engine" + '" && '
            f'"{_REMOTE_GT_BINARY}" -root {_REMOTE_DIR}/gt_engine '
            "-output /tmp/gt-install-smoke.db >/dev/null && "
            "test -s /tmp/gt-install-smoke.db && rm -f /tmp/gt-install-smoke.db && "
            'rm -rf "$HOME/.cache/uv/archive-v0" && '
            f"{_GT_STAGED_SOURCE_CLEANUP}"
        )
        await self.exec_as_agent(environment, install, env=dict(UTF8_ENV))

    def _model_and_env(self) -> tuple[str, dict[str, str]]:
        model = self.model_name or "deepseek-v4-flash"
        if not os.environ.get("OPENAI_BASE_URL"):
            model = model.split("/", 1)[-1]
        env = provider_env()
        env.update(UTF8_ENV)
        return model, env

    def _run_command(self, instruction: str, model: str, extra_args: str = "") -> str:
        # T1.1: the requested model MUST reach the runner (it was silently
        # dropped before, so a non-default model fell back to deepseek-v4-flash).
        # The runner's --model + --metrics are the single source of truth.
        return (
            f'"{_REMOTE_PY}" {_REMOTE_RUNNER} '
            f"--task {shlex.quote(instruction)} --model {shlex.quote(model)} "
            f"--cwd \"$PWD\" "
            f"--output /logs/agent/miniswe_trajectory.json "
            f"--temperature 1.0 "
            f"--metrics /logs/agent/miniswe_report.json "
            f"{extra_args}"
            "</dev/null 2>&1"
        )

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        model, env = self._model_and_env()
        await self.exec_as_agent(
            environment,
            self._run_command(
                instruction,
                model,
                extra_args="--gt-off --state-dir /logs/agent/gt-state ",
            ),
            env=env,
        )


class MiniSweGtAgent(MiniSweAgent):
    """Mini-SWE-Agent + GroundTruth as a Terminal-Bench 2.0 agent."""

    ENV_VARS = [
        EnvVar(
            kwarg="gt_profile",
            env="GT_RL_PROFILE",
            default="2",
            env_fallback="GT_RL_PROFILE",
        ),
    ]

    @staticmethod
    def name() -> str:
        return "miniswe-gt"

    def get_version_command(self) -> str | None:
        return (
            f'"{_REMOTE_PY}" -c '
            '"import minisweagent, groundtruth, gt_engine; '
            "print(minisweagent.__version__)\""
        )

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        model, env = self._model_and_env()
        env.update({k: v for k, v in os.environ.items() if k.startswith("GT_")})
        env.update(self.resolve_env_vars())
        env.setdefault("GT_INDEX_BINARY", _REMOTE_GT_BINARY)
        # GT state (events.jsonl) lives OUTSIDE the graded workspace AND inside
        # the captured /logs/agent/ tree so a post-run 17-feature census reads
        # the exact evidence_delivery rows instead of heuristic transcript text.
        env["GT_STATE_DIR"] = "/logs/agent/gt-state"
        extra = '--state-dir "$GT_STATE_DIR" --gt-mode advisory '
        await self.exec_as_agent(
            environment,
            self._run_command(instruction, model, extra_args=extra),
            env=env,
        )


class MiniSweEngineAgent(MiniSweGtAgent):
    """Mini-SWE-Agent + the Inline Engine (ENGINE posture) as a TB2 agent.

    Same treatment bundle as the advisory GT arm, but the runner selects
    ``--gt-mode engine``: every selected action crosses the engine boundary,
    is normalized, decided, executed literally or deterministically, compiled
    into one canonical observation, and bound to a delivery receipt. GT-off
    (``MiniSweAgent``) remains the stock-equivalent baseline and rollback path.
    """

    @staticmethod
    def name() -> str:
        return "miniswe-engine"

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        model, env = self._model_and_env()
        env.update({k: v for k, v in os.environ.items() if k.startswith("GT_")})
        env.update(self.resolve_env_vars())
        env.setdefault("GT_INDEX_BINARY", _REMOTE_GT_BINARY)
        env["GT_STATE_DIR"] = "/logs/agent/gt-state"
        extra = '--state-dir "$GT_STATE_DIR" --gt-mode engine '
        await self.exec_as_agent(
            environment,
            self._run_command(instruction, model, extra_args=extra),
            env=env,
        )
