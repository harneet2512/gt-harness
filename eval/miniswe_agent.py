"""Mini-SWE-Agent 2.2.8 as a Terminal-Bench 2.0 agent (GT-OFF arm).

Mirrors eval/tb_agent.py's NanoAgent: uploads scripts/ + eval/ + pyproject.toml
(NOT gt_engine/ - this arm is GT-off), installs mini-swe-agent==2.2.8 into the
tool venv, and runs scripts/miniswe_gt_run.py --gt-off. run() resolves the
tool python at the exact path GTNanoAgent uses (uv tool install does NOT emit a
~/.local/bin/python shim).

The GT-on arm reuses this class; that session adds the gt_engine upload and
drops the --gt-off flag.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from eval._env import UTF8_ENV, provider_env

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REMOTE_DIR = "/installed-agent/miniswe"
# uv tool install does not create a ~/.local/bin/python shim; the tool venv's
# interpreter is at the layout GTNanoAgent already relies on.
_REMOTE_PY = "$HOME/.local/share/uv/tools/nano-harness/bin/python"

# Task images vary (debian, alpine, ...); make sure curl exists, then let uv
# bring its own Python so we never depend on the image's python3.
_ENSURE_CURL = (
    "command -v curl >/dev/null 2>&1 || { "
    "command -v apt-get >/dev/null && apt-get update && apt-get install -y curl; } || { "
    "command -v apk >/dev/null && apk add --no-cache curl bash; } || { "
    "command -v dnf >/dev/null && dnf install -y curl; } || { "
    "command -v yum >/dev/null && yum install -y curl; }"
)


class MiniSweAgent(BaseInstalledAgent):
    """Mini-SWE-Agent 2.2.8, GT-off, as a Terminal-Bench 2.0 agent."""

    @staticmethod
    def name() -> str:
        return "miniswe"

    def get_version_command(self) -> str | None:
        return f'"{_REMOTE_PY}" -c "import minisweagent; print(minisweagent.__version__)"'

    async def install(self, environment: BaseEnvironment) -> None:
        # GT-off: skip gt_engine/ upload entirely - the container must be able
        # to run with no groundtruth package and no GT code present.
        await environment.upload_dir(_REPO_ROOT / "scripts", f"{_REMOTE_DIR}/scripts")
        await environment.upload_dir(_REPO_ROOT / "eval", f"{_REMOTE_DIR}/eval")
        await environment.upload_file(
            _REPO_ROOT / "pyproject.toml", f"{_REMOTE_DIR}/pyproject.toml"
        )
        await self.exec_as_root(
            environment, _ENSURE_CURL, env={"DEBIAN_FRONTEND": "noninteractive"}
        )
        # uv tool install has no --extra; --with pins the miniswe extra's single
        # dependency (mini-swe-agent==2.2.8) into the tool venv the runner uses.
        install = (
            "set -eu; "
            "curl -LsSf https://astral.sh/uv/install.sh | sh && "
            f'"$HOME/.local/bin/uv" tool install --python 3.12 '
            f'--with "mini-swe-agent==2.2.8" {_REMOTE_DIR} && '
            f'"{_REMOTE_PY}" -c "import minisweagent"'
        )
        await self.exec_as_agent(environment, install, env=dict(UTF8_ENV))

    def _model_and_env(self) -> tuple[str, dict[str, str]]:
        # Harbor model names look like "provider/name"; miniswe's litellm wants
        # the bare model name. Exception: routing through an OpenAI-compatible
        # gateway (OPENAI_BASE_URL set) keeps the string verbatim - it IS the
        # gateway's model id. provider_env()/UTF8_ENV: same as NanoAgent.
        model = self.model_name or "deepseek-v4-flash"
        if not os.environ.get("OPENAI_BASE_URL"):
            model = model.split("/", 1)[-1]
        env = provider_env()
        env.update(UTF8_ENV)
        return model, env

    def _run_command(self, instruction: str, model: str) -> str:
        # --cwd "$PWD": expands IN the container to the task's working
        # directory (harbor execs through sh -c with no explicit cwd, so the
        # image WORKDIR wins). `|| true`: a partial run may still pass the
        # grader - never let the agent's exit code abort the trial.
        return (
            f'"{_REMOTE_PY}" {_REMOTE_DIR}/scripts/miniswe_gt_run.py '
            f'--task {shlex.quote(instruction)} --cwd "$PWD" '
            "--output /logs/agent/miniswe_trajectory.json "
            "--temperature 1.0 --gt-off "
            "</dev/null 2>&1 | tee /logs/agent/miniswe.txt || true"
        )

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        model, env = self._model_and_env()
        await self.exec_as_agent(environment, self._run_command(instruction, model), env=env)
