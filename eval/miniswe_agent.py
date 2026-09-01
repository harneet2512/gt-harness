"""Mini-SWE-Agent as a Terminal-Bench 2.0 agent (GT-off and GT-on arms).

The two Harbor agents install the same pinned treatment bundle. ``MiniSweAgent``
runs the stock loop with ``--gt-off`` and never activates or imports GT in the
runner. ``MiniSweGtAgent`` activates the advisory session and forwards only the
GT state/index configuration. This makes activation—not package drift—the A/B
treatment. The shipping product has one closed scaffold version: 2.4.6.

``uv tool install`` does not emit a ~/.local/bin/python shim; the tool venv's
interpreter lives at the layout GTNanoAgent already relies on.
"""

from __future__ import annotations

import os
import shlex
import tempfile
from pathlib import Path

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    EnvVar,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from eval._env import UTF8_ENV, provider_env
from gt_harness.product import build_product_bundle, project_task_environment

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REMOTE_BUNDLE_DIR = "/installed-agent/bundle"
_REMOTE_GT_BINARY = "/installed-agent/gt-index"
_REMOTE_UV_INSTALLER = "/installed-agent/uv-install.sh"
_VENDOR_DIR = _REPO_ROOT / "vendor"
_PRODUCT_MANIFEST = _REPO_ROOT / "config" / "deepswe_product_bundle_v1.json"
_REMOTE_PY = "$HOME/.local/share/uv/tools/nano-harness/bin/python"
_UV_VERSION = "0.11.32"
_PYTHON_VERSION = "3.12.13"
_DEFAULT_MINISWE_AGENT_VERSION = "2.4.6"
_ALLOWED_MINISWE_AGENT_VERSIONS = frozenset({"2.4.6"})
_UV_INSTALL = f"https://astral.sh/uv/{_UV_VERSION}/install.sh"
_UV_INSTALLER_SHA256 = "43aff33a967fe40e8c17949d8c85c65bc43f3b5c94742393c957f56ab5ba80f4"
_GT_WHEEL_SHA256 = "2d0483c43cd7209d7049439af963d420666bc853854b21e8a82e07236b00ee0e"
_GT_BINARY_SHA256 = "024851815218f5ade0932f4a661287c743ce20d89e8ab2d1375f05d5b0b96c8a"

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
    def _gt_wheel() -> Path:
        wheels = sorted(_VENDOR_DIR.glob("groundtruth_mcp-*.whl"))
        if not wheels:
            raise FileNotFoundError(
                f"Mini-SWE treatment bundle needs the vendored GroundTruth "
                f"wheel in {_VENDOR_DIR} (build with: pip wheel --no-deps "
                "-w vendor D:\\Groundtruth)"
            )
        wheel = wheels[-1]
        MiniSweAgent._require_digest(wheel, _GT_WHEEL_SHA256, "groundtruth_wheel")
        return wheel

    @staticmethod
    def _require_digest(path: Path, expected: str, label: str) -> None:
        import hashlib

        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"{label} digest mismatch")

    @staticmethod
    def _harness_wheel(output: Path) -> Path:
        bundle = build_product_bundle(_PRODUCT_MANIFEST, output_dir=output)
        record = bundle["python_wheel"]
        if not isinstance(record, dict):
            raise ValueError("product bundle did not build a harness wheel")
        wheel = output / "dist" / str(record["filename"])
        MiniSweAgent._require_digest(wheel, str(record["sha256"]), "harness_wheel")
        return wheel

    @staticmethod
    def _gt_binary_host() -> Path:
        override = os.environ.get("GT_INDEX_BINARY_HOST", "")
        path = Path(override) if override else _VENDOR_DIR / "gt-index-linux-amd64"
        if not path.is_file():
            raise FileNotFoundError(
                f"Mini-SWE treatment bundle needs a Linux gt-index binary at "
                f"{path} (or set GT_INDEX_BINARY_HOST)"
            )
        MiniSweAgent._require_digest(path, _GT_BINARY_SHA256, "groundtruth_producer")
        return path

    @staticmethod
    def _uv_installer_host() -> Path:
        path = Path(os.environ.get("GT_UV_INSTALLER_HOST", ""))
        if not path.is_file():
            raise FileNotFoundError(
                "Mini-SWE treatment bundle requires the pre-downloaded uv 0.11.32 "
                "installer in GT_UV_INSTALLER_HOST"
            )
        MiniSweAgent._require_digest(path, _UV_INSTALLER_SHA256, "uv_installer")
        return path

    async def install(self, environment: BaseEnvironment) -> None:
        wheel = self._gt_wheel()
        binary = self._gt_binary_host()
        uv_installer = self._uv_installer_host()
        miniswe_version = _miniswe_agent_version()
        remote_gt_wheel = f"{_REMOTE_BUNDLE_DIR}/{wheel.name}"
        # Harbor's task images do not guarantee that the treatment mount exists.
        # Create it before any upload so setup fails only on a real artifact or
        # runtime error, not because the destination directory is absent.
        await self.exec_as_root(environment, f"mkdir -p {_REMOTE_BUNDLE_DIR}")
        await environment.upload_file(wheel, remote_gt_wheel)
        with tempfile.TemporaryDirectory(prefix="gt-product-bundle-") as temporary:
            harness_wheel = self._harness_wheel(Path(temporary))
            remote_harness_wheel = f"{_REMOTE_BUNDLE_DIR}/{harness_wheel.name}"
            await environment.upload_file(harness_wheel, remote_harness_wheel)
        await environment.upload_file(binary, _REMOTE_GT_BINARY)
        await environment.upload_file(uv_installer, _REMOTE_UV_INSTALLER)
        await self.exec_as_root(environment, f"chmod 755 {_REMOTE_GT_BINARY}")
        install = (
            "set -eu; "
            f"echo '{_UV_INSTALLER_SHA256}  {_REMOTE_UV_INSTALLER}' | sha256sum -c - && "
            f"sh {_REMOTE_UV_INSTALLER} && "
            f'"$HOME/.local/bin/uv" tool install --python {_PYTHON_VERSION} '
            f'--with "mini-swe-agent=={miniswe_version}" '
            f"--with {shlex.quote(remote_gt_wheel)} --with 'numpy==2.5.1' "
            f"{shlex.quote(remote_harness_wheel)} && "
            f'"{_REMOTE_PY}" -c "import importlib.metadata as m, sys; '
            "assert sys.version_info[:3] == (3, 12, 13); "
            f"assert m.version('mini-swe-agent') == '{miniswe_version}'; "
            "assert m.version('groundtruth-mcp') == '1.0.0'; "
            "assert m.version('numpy') == '2.5.1'; "
            "import minisweagent, groundtruth, gt_engine" + '" && '
            "mkdir -p /tmp/gt-install-smoke-src && "
            "printf 'def smoke():\\n    return 1\\n' > /tmp/gt-install-smoke-src/smoke.py && "
            f'"{_REMOTE_GT_BINARY}" -root /tmp/gt-install-smoke-src '
            "-output /tmp/gt-install-smoke.db >/dev/null && "
            "test -s /tmp/gt-install-smoke.db && rm -f /tmp/gt-install-smoke.db && "
            'rm -rf "$HOME/.cache/uv/archive-v0" && '
            f"rm -rf -- {_REMOTE_BUNDLE_DIR} /tmp/gt-install-smoke-src"
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
            f'"{_REMOTE_PY}" -m scripts.miniswe_gt_run '
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
        env.update(project_task_environment(os.environ, treatment="groundtruth"))
        env.update(self.resolve_env_vars())
        env.setdefault("GT_INDEX_BINARY", _REMOTE_GT_BINARY)
        # GT state (events.jsonl) lives OUTSIDE the graded workspace AND inside
        # the captured /logs/agent/ tree so a post-run 17-feature census reads
        # the exact evidence_delivery rows instead of heuristic transcript text.
        env["GT_STATE_DIR"] = "/logs/agent/gt-state"
        # GT state (events.jsonl, graph.db) lives OUTSIDE the graded workspace.
        extra = '--state-dir "$GT_STATE_DIR" --gt-mode advisory '
        await self.exec_as_agent(
            environment,
            self._run_command(instruction, model, extra_args=extra),
            env=env,
        )
