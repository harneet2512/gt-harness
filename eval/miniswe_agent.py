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
import re
import shlex
from pathlib import Path

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    CliFlag,
    EnvVar,
    NonZeroAgentExitCodeError,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from eval._env import UTF8_ENV, provider_env
from gt_harness.product import project_task_environment
from scripts.agent_resource_evidence import capture_snapshot, write_host_interval

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REMOTE_BUNDLE_DIR = "/installed-agent/bundle"
_REMOTE_GT_BINARY = "/installed-agent/gt-index"
_REMOTE_UV_INSTALLER = "/installed-agent/uv-install.tar.gz"
_REMOTE_PYTHON_ARCHIVE = "/installed-agent/python-3.12.13.tar.gz"
_REMOTE_PYTHON_DIR = "/installed-agent/python"
_REMOTE_WHEELHOUSE = "/installed-agent/wheelhouse"
_REMOTE_DENSE_MODEL_DIR = "/installed-agent/dense-model"
_REMOTE_LSP_BIN = "/installed-agent/lsp-bin"
_VENDOR_DIR = _REPO_ROOT / "vendor"
_REMOTE_PY = "$HOME/.local/share/uv/tools/nano-harness/bin/python"
_UV_VERSION = "0.11.32"
_PYTHON_VERSION = "3.12.13"
_DEFAULT_MINISWE_AGENT_VERSION = "2.4.6"
_ALLOWED_MINISWE_AGENT_VERSIONS = frozenset({"2.4.6"})
_UV_INSTALL = f"https://astral.sh/uv/{_UV_VERSION}/install.sh"
_UV_INSTALLER_SHA256 = "aab924fd522efd06f1c5f3b93a243864fc453132c94b2dc49f1371b528a4b967"
_PYTHON_ARCHIVE_SHA256 = "5854aa6ec71cad00334d5065633c210b2e7feb40956767a59a91791cadcf0b79"
_GT_WHEEL_SHA256 = "2d0483c43cd7209d7049439af963d420666bc853854b21e8a82e07236b00ee0e"
_GT_BINARY_SHA256 = "071fd6cb941b12adf762694c4bef1fb7f126841e4e587d56fd3b90b02002ca32"
_PROVIDER_BILLING_FAILURE = re.compile(
    r"(?:insufficient[ _-]*balance|(?:http(?: status)?|status(?: code)?)\s*[:=]?\s*402\b)",
    re.IGNORECASE,
)


class ProviderBillingError(NonZeroAgentExitCodeError):
    """The provider rejected a request because the account cannot fund it."""


def _miniswe_agent_version() -> str:
    """Return the closed Mini-SWE treatment version for this execution."""
    version = os.environ.get("MINISWE_AGENT_VERSION", _DEFAULT_MINISWE_AGENT_VERSION)
    if version not in _ALLOWED_MINISWE_AGENT_VERSIONS:
        allowed = ", ".join(sorted(_ALLOWED_MINISWE_AGENT_VERSIONS))
        raise ValueError(f"MINISWE_AGENT_VERSION must be one of: {allowed}; got {version!r}")
    return version


class MiniSweAgent(BaseInstalledAgent):
    """Mini-SWE-Agent, GT-off, as a Terminal-Bench 2.0 agent."""

    # Pier forwards workflow agent kwargs to the installed-agent constructor.
    # Declare the runner limit here so Harbor retains it instead of silently
    # dropping the unknown ``max_iterations`` kwarg at the base-class boundary.
    CLI_FLAGS = [
        CliFlag(
            kwarg="max_iterations",
            cli="--step-limit",
            type="int",
            default=100,
        ),
        CliFlag(kwarg="task_id", cli="--task-id", type="str", default=""),
        CliFlag(
            kwarg="product_source_sha",
            cli="--product-source-sha",
            type="str",
            default="",
        ),
        CliFlag(
            kwarg="time_budget_seconds",
            cli="--time-budget-seconds",
            type="int",
            default=1,
        ),
    ]

    def _classify_exec_error(self, command: str, result):
        """Keep monetary rejection distinct from transient rate limiting.

        Harbor 0.20 classifies by scanning combined terminal output. A wrapper
        can print ``ApiRateLimitError`` after DeepSeek's HTTP 402 payload and
        thereby overwrite the provider's actual ``Insufficient Balance``
        reason. Billing is terminal for an approved run and must never enter a
        rate-limit retry policy.
        """
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
        if _PROVIDER_BILLING_FAILURE.search(output):
            detail = (
                f"Command failed (exit {result.return_code}): {command}\n"
                f"stdout: {self._truncate_output(result.stdout)}\n"
                f"stderr: {self._truncate_output(result.stderr)}"
            )
            return ProviderBillingError(detail)
        return super()._classify_exec_error(command, result)

    async def exec_as_agent(
        self,
        environment: BaseEnvironment,
        command: str,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ):
        """Apply Pier's filtered-egress proxy at the Harbor execution seam.

        Harbor 0.20's ``BaseInstalledAgent`` calls ``environment.exec``
        directly and does not invoke Pier's ``agent_process_env`` hook.  Without
        this bridge, the task container has the proxy sidecar but the model
        process attempts direct DNS and fails closed.
        """
        process_env = environment.agent_process_env(env)
        return await super().exec_as_agent(
            environment,
            command,
            env=process_env,
            cwd=cwd,
            timeout_sec=timeout_sec,
        )

    @staticmethod
    def name() -> str:
        return "miniswe"

    def get_version_command(self) -> str | None:
        return f'"{_REMOTE_PY}" -c "import minisweagent; print(minisweagent.__version__)"'

    @staticmethod
    def _gt_wheel() -> Path:
        configured = os.environ.get("GT_GROUNDTRUTH_WHEEL_HOST", "")
        if configured:
            wheels = [Path(configured)]
        else:
            wheels = sorted(_VENDOR_DIR.glob("groundtruth_mcp-*.whl"))
        if not wheels:
            raise FileNotFoundError(
                "Mini-SWE treatment bundle needs GT_GROUNDTRUTH_WHEEL_HOST "
                f"or a vendored GroundTruth wheel in {_VENDOR_DIR}"
            )
        wheel = wheels[-1]
        if not wheel.is_file():
            raise FileNotFoundError(f"GroundTruth wheel is missing: {wheel}")
        MiniSweAgent._require_digest(wheel, _GT_WHEEL_SHA256, "groundtruth_wheel")
        return wheel

    @staticmethod
    def _require_digest(path: Path, expected: str, label: str) -> None:
        import hashlib

        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"{label} digest mismatch")

    @staticmethod
    def _harness_wheel() -> Path:
        wheel = Path(os.environ.get("GT_HARNESS_WHEEL_HOST", ""))
        expected = os.environ.get("GT_HARNESS_WHEEL_SHA256", "")
        if not wheel.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise FileNotFoundError(
                "Mini-SWE treatment bundle needs GT_HARNESS_WHEEL_HOST and "
                "GT_HARNESS_WHEEL_SHA256 from the verified product bundle"
            )
        MiniSweAgent._require_digest(wheel, expected, "harness_wheel")
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
    def _lsp_bin_host() -> Path | None:
        """Locate host-staged language servers, if any were provisioned.

        LSP promotion discovers servers with shutil.which, and the task
        container reaches only the model transport -- nothing can be installed
        at task time. Servers therefore arrive the way every other execution
        input does: resolved on the host, uploaded, and put on PATH.

        Staging is optional. With none provisioned the runtime records
        promotion_no_servers, which is a visible no-op rather than a silent
        one, and the graph is exactly what it is today.
        """

        configured = os.environ.get("GT_LSP_BIN_HOST", "").strip()
        if not configured:
            return None
        path = Path(configured)
        if not path.is_dir():
            raise FileNotFoundError(
                f"GT_LSP_BIN_HOST is set but no directory exists at {path}"
            )
        return path

    @staticmethod
    def _dense_model_host() -> Path | None:
        """Locate the pinned retrieval model on the host, if one is provisioned.

        Dense retrieval is optional: launchers that provision no model leave
        GT_DENSE_MODEL_DIR unset and the runtime records the capability as
        unavailable.  What must not happen is a provisioned model that never
        reaches the task environment, because GT reads GT_DENSE_MODEL_DIR
        inside the container while the workflow exports a host path.
        """

        configured = os.environ.get("GT_DENSE_MODEL_DIR", "").strip()
        if not configured:
            return None
        path = Path(configured)
        if not path.is_dir():
            raise FileNotFoundError(
                f"GT_DENSE_MODEL_DIR is set but no directory exists at {path}"
            )
        return path

    @staticmethod
    def _uv_installer_host() -> Path:
        path = Path(os.environ.get("GT_UV_INSTALLER_HOST", ""))
        if not path.is_file():
            raise FileNotFoundError(
                "Mini-SWE treatment bundle requires the pre-downloaded uv 0.11.32 "
                "archive in GT_UV_INSTALLER_HOST"
            )
        MiniSweAgent._require_digest(path, _UV_INSTALLER_SHA256, "uv_installer")
        return path

    @staticmethod
    def _python_archive_host() -> Path:
        path = Path(os.environ.get("GT_PYTHON_ARCHIVE_HOST", ""))
        if not path.is_file():
            raise FileNotFoundError(
                "Mini-SWE treatment bundle requires the pre-downloaded Python "
                "3.12.13 archive in GT_PYTHON_ARCHIVE_HOST"
            )
        MiniSweAgent._require_digest(path, _PYTHON_ARCHIVE_SHA256, "python_archive")
        return path

    @staticmethod
    def _wheelhouse_host() -> Path:
        path = Path(os.environ.get("GT_WHEELHOUSE_HOST", ""))
        if not path.is_dir() or not any(path.iterdir()):
            raise FileNotFoundError(
                "Mini-SWE treatment bundle requires the pre-downloaded dependency "
                "wheelhouse in GT_WHEELHOUSE_HOST"
            )
        return path

    async def install(self, environment: BaseEnvironment) -> None:
        wheel = self._gt_wheel()
        binary = self._gt_binary_host()
        uv_installer = self._uv_installer_host()
        python_archive = self._python_archive_host()
        wheelhouse = self._wheelhouse_host()
        miniswe_version = _miniswe_agent_version()
        remote_gt_wheel = f"{_REMOTE_BUNDLE_DIR}/{wheel.name}"
        # Harbor's task images do not guarantee that the treatment mount exists.
        # Create it before any upload so setup fails only on a real artifact or
        # runtime error, not because the destination directory is absent.
        await self.exec_as_root(environment, f"mkdir -p {_REMOTE_BUNDLE_DIR}")
        await environment.upload_file(wheel, remote_gt_wheel)
        harness_wheel = self._harness_wheel()
        remote_harness_wheel = f"{_REMOTE_BUNDLE_DIR}/{harness_wheel.name}"
        await environment.upload_file(harness_wheel, remote_harness_wheel)
        await environment.upload_file(binary, _REMOTE_GT_BINARY)
        await environment.upload_file(uv_installer, _REMOTE_UV_INSTALLER)
        await environment.upload_file(python_archive, _REMOTE_PYTHON_ARCHIVE)
        await environment.upload_dir(wheelhouse, _REMOTE_WHEELHOUSE)
        dense_model = self._dense_model_host()
        if dense_model is not None:
            await environment.upload_dir(dense_model, _REMOTE_DENSE_MODEL_DIR)
        lsp_bin = self._lsp_bin_host()
        if lsp_bin is not None:
            await environment.upload_dir(lsp_bin, _REMOTE_LSP_BIN)
            await self.exec_as_root(
                environment, f"chmod -R 755 {_REMOTE_LSP_BIN}"
            )
        await self.exec_as_root(environment, f"chmod 755 {_REMOTE_GT_BINARY}")
        install = (
            "set -eu; "
            f"echo '{_UV_INSTALLER_SHA256}  {_REMOTE_UV_INSTALLER}' | sha256sum -c - && "
            'mkdir -p /tmp/uv-extract "$HOME/.local/bin" && '
            f"tar -xzf {_REMOTE_UV_INSTALLER} -C /tmp/uv-extract && "
            'cp /tmp/uv-extract/uv-x86_64-unknown-linux-gnu/uv "$HOME/.local/bin/uv" && '
            'chmod 755 "$HOME/.local/bin/uv" && '
            f"mkdir -p {_REMOTE_PYTHON_DIR} && tar -xzf {_REMOTE_PYTHON_ARCHIVE} "
            f"-C {_REMOTE_PYTHON_DIR} --strip-components=1 && "
            f'"$HOME/.local/bin/uv" tool install --offline --no-index '
            f"--find-links {_REMOTE_WHEELHOUSE} --python {_REMOTE_PYTHON_DIR}/bin/python3.12 "
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
            'rm -rf "$HOME/.cache/uv/archive-v0" /tmp/uv-extract && '
            f"rm -rf -- {_REMOTE_BUNDLE_DIR} /tmp/gt-install-smoke-src"
        )
        await self.exec_as_agent(environment, install, env=dict(UTF8_ENV))

    def _model_and_env(self) -> tuple[str, dict[str, str]]:
        model = str(self.model_name or "").strip()
        if not model:
            raise ValueError("model_name is required by the provider route")
        if not os.environ.get("OPENAI_BASE_URL"):
            model = model.split("/", 1)[-1]
        env = provider_env()
        env.update(UTF8_ENV)
        return model, env

    def _run_command(self, instruction: str, model: str, extra_args: str = "") -> str:
        # T1.1: the requested model MUST reach the runner (it was silently
        # dropped before, so a non-default model fell back to a stale default).
        # The runner's --model + --metrics are the single source of truth.
        return (
            # Staged language servers must be discoverable by shutil.which,
            # which is how LSP promotion finds them. Prepending keeps the
            # image PATH intact and simply wins for these four names.
            f'PATH="{_REMOTE_LSP_BIN}:$PATH" '
            f'exec "{_REMOTE_PY}" -m scripts.miniswe_gt_run '
            f"--task {shlex.quote(instruction)} --model {shlex.quote(model)} "
            f'--cwd "$PWD" '
            f"--output /logs/agent/miniswe_trajectory.json "
            f"--temperature 1.0 "
            f"--metrics /logs/agent/miniswe_report.json "
            f"--product-receipt /logs/agent/gt-run.json "
            f"--adapter-receipt /logs/agent/benchmark-adapter.json "
            f"{self.build_cli_flags()} "
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
            'print(minisweagent.__version__)"'
        )

    @staticmethod
    async def _resource_snapshot(
        environment: BaseEnvironment, task_id: str, product_source_sha: str
    ) -> dict[str, object]:
        snapshotter = getattr(environment, "agent_resource_snapshot", None)
        if not callable(snapshotter):
            raise RuntimeError("environment lacks host cgroup snapshot support")
        cgroup = await snapshotter()
        if not isinstance(cgroup, dict):
            raise RuntimeError("host cgroup snapshot is malformed")
        return capture_snapshot(cgroup, task_id=task_id, product_source_sha=product_source_sha)

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        model, env = self._model_and_env()
        env.update(project_task_environment(os.environ, treatment="groundtruth"))
        env.update(self.resolve_env_vars())
        env.setdefault("GT_INDEX_BINARY", _REMOTE_GT_BINARY)
        if self._dense_model_host() is not None:
            env["GT_DENSE_MODEL_DIR"] = _REMOTE_DENSE_MODEL_DIR
        env["GT_TASK_ID"] = str(self._resolved_flags.get("task_id", ""))
        env["GT_PRODUCT_SOURCE_SHA"] = str(self._resolved_flags.get("product_source_sha", ""))
        task_id = env["GT_TASK_ID"].strip()
        product_source_sha = env["GT_PRODUCT_SOURCE_SHA"].strip()
        if not task_id or not re.fullmatch(r"[0-9a-f]{40}", product_source_sha):
            raise ValueError("benchmark agent resource identity is incomplete")
        attestation_key = os.environ.get("GT_RESOURCE_ATTESTATION_KEY", "").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", attestation_key):
            raise ValueError("GT_RESOURCE_ATTESTATION_KEY must be 64 lowercase hex characters")
        # GT state (events.jsonl) lives OUTSIDE the graded workspace AND inside
        # the captured /logs/agent/ tree so a post-run 17-feature census reads
        # the exact evidence_delivery rows instead of heuristic transcript text.
        env["GT_STATE_DIR"] = "/logs/agent/gt-state"
        # GT state (events.jsonl, graph.db) lives OUTSIDE the graded workspace.
        extra = '--state-dir "$GT_STATE_DIR" --gt-mode advisory '
        resource_path = Path(self.logs_dir) / "agent-resource.json"
        resource_path.unlink(missing_ok=True)
        before = await self._resource_snapshot(environment, task_id, product_source_sha)
        try:
            await self.exec_as_agent(
                environment,
                self._run_command(instruction, model, extra_args=extra),
                env=env,
            )
        except NonZeroAgentExitCodeError as exc:
            # Remove anything the task may have written at the canonical name;
            # only the host adapter is permitted to publish this attestation.
            resource_path.unlink(missing_ok=True)
            match = re.search(r"Command failed \(exit (-?\d+)\)", str(exc))
            if match and int(match.group(1)) == 137:
                try:
                    after = await self._resource_snapshot(environment, task_id, product_source_sha)
                    write_host_interval(
                        resource_path,
                        before=before,
                        after=after,
                        task_id=task_id,
                        product_source_sha=product_source_sha,
                        exit_code=137,
                        attestation_key=attestation_key,
                    )
                except Exception:
                    # Resource finalization cannot replace the exact runner error.
                    resource_path.unlink(missing_ok=True)
                    pass
            raise
        except BaseException:
            resource_path.unlink(missing_ok=True)
            raise
        else:
            resource_path.unlink(missing_ok=True)
