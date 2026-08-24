"""Canonical Harbor adapter for the GT Harness product CLI.

Harbor owns task provisioning and grading.  This adapter owns only installation
and invocation of the released product boundary: ``gt-harness run`` with the
Mini-SWE-Agent version pinned by the package contract.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
from pathlib import Path

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    EnvVar,
    NonZeroAgentExitCodeError,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from eval._env import UTF8_ENV, clean_env_value, provider_env
from gt_harness.indexer_setup import ensure_source_indexer

CANONICAL_MINISWE_VERSION = "2.2.8"

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REMOTE_SOURCE = "/installed-agent/gt-harness"
_REMOTE_INDEXER = "/installed-agent/gt-index"
_REMOTE_DENSE_MODEL = "/installed-agent/snowflake-arctic-embed-m"
_REMOTE_PYTHON = '"$HOME/.local/share/uv/tools/gt-harness/bin/python"'
_REMOTE_CLI = '"$HOME/.local/bin/gt-harness"'
_UV_VERSION = "0.11.32"
_PYTHON_VERSION = "3.12.13"
_UV_INSTALL = f"https://astral.sh/uv/{_UV_VERSION}/install.sh"
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_ENSURE_CURL = (
    "command -v curl >/dev/null 2>&1 || { "
    "command -v apt-get >/dev/null && apt-get update && apt-get install -y curl; } || { "
    "command -v apk >/dev/null && apk add --no-cache curl bash; } || { "
    "command -v dnf >/dev/null && dnf install -y curl; } || { "
    "command -v yum >/dev/null && yum install -y curl; }"
)


def _effective_model(requested_model: str, base_url: str) -> str:
    """Return the LiteLLM route while preserving the requested ID separately."""

    requested = requested_model.strip()
    if base_url and not requested.startswith("openai/"):
        return f"openai/{requested}"
    return requested


class GtHarnessMiniSwe228Agent(BaseInstalledAgent):
    """Harbor installed-agent boundary for the production GT Harness runner."""

    ENV_VARS = [
        EnvVar(kwarg="task_id", env="GT_HARBOR_TASK_ID"),
        EnvVar(kwarg="product_source_sha", env="GT_PRODUCT_SOURCE_SHA"),
        EnvVar(kwarg="temperature", env="GT_HARBOR_TEMPERATURE", default="1.0"),
        EnvVar(kwarg="max_iterations", env="GT_HARBOR_MAX_ITERATIONS", default="100"),
        EnvVar(
            kwarg="time_budget_seconds",
            env="GT_HARBOR_TIME_BUDGET_SECONDS",
        ),
    ]

    @staticmethod
    def name() -> str:
        return "gt-harness-miniswe-2.2.8"

    def get_version_command(self) -> str | None:
        return (
            f"{_REMOTE_PYTHON} -c \"import importlib.metadata as m; "
            "print(m.version('gt-harness'), m.version('mini-swe-agent'))\""
        )

    @staticmethod
    def _indexer_host_path() -> Path:
        override = clean_env_value(os.environ.get("GT_INDEX_BINARY_HOST"))
        if override:
            path = Path(override)
        else:
            setup = ensure_source_indexer()
            path = Path(setup.binary_path) if setup.status == "READY" else Path()
        if not str(path) or not path.is_file():
            raise FileNotFoundError(
                "canonical Harbor adapter requires a source-built gt-index binary"
            )
        return path

    async def install(self, environment: BaseEnvironment) -> None:
        indexer = self._indexer_host_path()
        dense_model = Path(
            clean_env_value(os.environ.get("GT_DENSE_MODEL_DIR"))
        ).resolve()
        required_dense = tuple(dense_model / name for name in ("model.onnx", "tokenizer.json"))
        if not dense_model.is_dir() or not all(path.is_file() for path in required_dense):
            raise FileNotFoundError(
                "canonical Harbor adapter requires the provisioned Snowflake ONNX model"
            )
        for relative in ("eval", "gt_engine", "gt_harness", "src/groundtruth"):
            await environment.upload_dir(
                _REPO_ROOT / relative,
                f"{_REMOTE_SOURCE}/{relative}",
            )
        await environment.upload_file(
            _REPO_ROOT / "pyproject.toml",
            f"{_REMOTE_SOURCE}/pyproject.toml",
        )
        await environment.upload_file(indexer, _REMOTE_INDEXER)
        await environment.upload_dir(dense_model, _REMOTE_DENSE_MODEL)
        await self.exec_as_root(
            environment,
            _ENSURE_CURL,
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        await self.exec_as_root(environment, f"chmod 755 {_REMOTE_INDEXER}")
        install = (
            "set -eu; "
            f"curl -LsSf {_UV_INSTALL} | sh && "
            f'"$HOME/.local/bin/uv" tool install --python {_PYTHON_VERSION} '
            f'--with "mini-swe-agent=={CANONICAL_MINISWE_VERSION}" '
            "--with 'numpy==2.5.1' "
            '--with "onnxruntime==1.28.0" '
            '--with "tokenizers==0.23.1" '
            f"{_REMOTE_SOURCE} && "
            f"{_REMOTE_PYTHON} -c \"import importlib.metadata as m, sys; "
            f"assert sys.version_info[:3] == (3, 12, 13); "
            f"assert m.version('mini-swe-agent') == '{CANONICAL_MINISWE_VERSION}'; "
            "assert m.version('gt-harness') == '0.9.0'\" && "
            f"{_REMOTE_INDEXER} -root {_REMOTE_SOURCE}/gt_engine "
            "-output /tmp/gt-harbor-install-smoke.db >/dev/null && "
            "test -s /tmp/gt-harbor-install-smoke.db && "
            "rm -f /tmp/gt-harbor-install-smoke.db && "
            'rm -rf "$HOME/.cache/uv/archive-v0"'
        )
        await self.exec_as_agent(environment, install, env=dict(UTF8_ENV))

    def _run_command(
        self,
        instruction: str,
        *,
        requested_model: str,
        effective_model: str,
        base_url: str,
        task_id: str,
        temperature: str,
        max_iterations: str,
        time_budget_seconds: str,
        product_source_sha: str,
    ) -> str:
        identity = {
            "schema": "gt.harbor_product_adapter.v1",
            "adapter": f"{__name__}:{type(self).__name__}",
            "agent_scaffold": "mini-swe-agent",
            "agent_scaffold_version": CANONICAL_MINISWE_VERSION,
            "requested_model": requested_model,
            "effective_model": effective_model,
            "provider_route": "openrouter" if base_url == _OPENROUTER_BASE_URL else "custom",
            "task_id": task_id,
            "attempt": 1,
            "product_command": "gt-harness run",
            "product_source_sha": product_source_sha,
            "time_budget_seconds": time_budget_seconds,
        }
        receipt = json.dumps(identity, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        write_receipt = (
            "from pathlib import Path; "
            f"Path('/logs/agent/benchmark-adapter.json').write_text({receipt!r}, "
            "encoding='utf-8')"
        )
        parts = [
            f"{_REMOTE_PYTHON} -c {shlex.quote(write_receipt)}",
            "&& exec",
            _REMOTE_CLI,
            "run",
            shlex.quote(instruction),
            "--model",
            shlex.quote(effective_model),
        ]
        if base_url:
            parts.extend(("--base-url", shlex.quote(base_url)))
        parts.extend(
            (
                "--temperature",
                shlex.quote(temperature),
                "--max-iterations",
                shlex.quote(max_iterations),
                "--time-budget-seconds",
                shlex.quote(time_budget_seconds),
                "--treatment",
                "groundtruth",
                "--root",
                '"$PWD"',
                "--state-dir",
                "/logs/agent/gt-state",
                "--run-id",
                "harbor-product",
                "--task-id",
                shlex.quote(task_id),
                "--trial-id",
                "1",
                "--output",
                "/logs/agent/gt-run.json",
                "</dev/null 2>&1",
            )
        )
        return " ".join(parts)

    async def _exec_product_secret_safe(
        self,
        environment: BaseEnvironment,
        command: str,
        env: dict[str, str],
    ) -> None:
        """Execute without Harbor's inherited full-environment debug record."""

        merged_env = {**env, **self._extra_env}
        self.logger.debug(
            "Running canonical GT Harness product command",
            extra={"env_keys": sorted(merged_env), "credential_values_logged": False},
        )
        try:
            result = await environment.exec(
                command=f"set -o pipefail; {command}",
                env=merged_env,
            )
        except asyncio.CancelledError:
            try:
                await asyncio.shield(
                    self._finalize_nonterminal_product(
                        environment,
                        return_code=124,
                        supervisor="harbor_adapter_timeout",
                    )
                )
            except Exception:
                self.logger.exception(
                    "Failed to finalize product receipt after Harbor cancellation"
                )
            raise
        if result.return_code != 0:
            await self._finalize_nonterminal_product(
                environment,
                return_code=int(result.return_code),
                supervisor="harbor_adapter",
            )
            raise NonZeroAgentExitCodeError(
                f"Canonical GT Harness product command failed (exit {result.return_code})\n"
                f"stdout: {self._truncate_output(result.stdout)}\n"
                f"stderr: {self._truncate_output(result.stderr)}"
            )

    async def _finalize_nonterminal_product(
        self,
        environment: BaseEnvironment,
        *,
        return_code: int,
        supervisor: str,
    ) -> None:
        finalize = (
            f"{_REMOTE_PYTHON} -m gt_harness.supervision "
            "--receipt /logs/agent/gt-run.json "
            "--trajectory /logs/agent/gt-run.trajectory.json "
            f"--return-code {int(return_code)} "
            f"--supervisor {shlex.quote(supervisor)}"
        )
        result = await environment.exec(command=finalize, env=dict(UTF8_ENV))
        if result.return_code != 0:
            self.logger.error(
                "Failed to finalize a nonterminal product receipt",
                extra={
                    "product_return_code": return_code,
                    "finalizer_return_code": result.return_code,
                },
            )

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        requested_model = str(self.model_name or "").strip()
        if not requested_model:
            raise ValueError("Harbor must provide an explicit model identifier")
        host_env = provider_env()
        base_url = host_env.get("OPENAI_BASE_URL", "")
        effective_model = _effective_model(requested_model, base_url)
        adapter_env = self.resolve_env_vars()
        task_id = adapter_env.get("GT_HARBOR_TASK_ID", "").strip()
        if not task_id:
            raise ValueError("Harbor must provide task_id to the canonical adapter")
        product_source_sha = adapter_env.get("GT_PRODUCT_SOURCE_SHA", "").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", product_source_sha):
            raise ValueError("Harbor must provide the exact 40-character product source SHA")
        time_budget_seconds = adapter_env.get("GT_HARBOR_TIME_BUDGET_SECONDS", "").strip()
        try:
            valid_time_budget = float(time_budget_seconds) > 0
        except ValueError:
            valid_time_budget = False
        if not valid_time_budget:
            raise ValueError("Harbor must provide a positive task-derived time budget")
        environment_vars = {
            **host_env,
            **UTF8_ENV,
            **adapter_env,
            "GT_INDEX_BINARY": _REMOTE_INDEXER,
            "GT_REQUESTED_MODEL": requested_model,
            "GT_EFFECTIVE_MODEL": effective_model,
            "GT_DENSE_MODEL_DIR": _REMOTE_DENSE_MODEL,
            "GT_RETRIEVAL_MODE": "hybrid_required",
        }
        await self._exec_product_secret_safe(
            environment,
            self._run_command(
                instruction,
                requested_model=requested_model,
                effective_model=effective_model,
                base_url=base_url,
                task_id=task_id,
                temperature=adapter_env["GT_HARBOR_TEMPERATURE"],
                max_iterations=adapter_env["GT_HARBOR_MAX_ITERATIONS"],
                time_budget_seconds=time_budget_seconds,
                product_source_sha=product_source_sha,
            ),
            environment_vars,
        )


__all__ = ["CANONICAL_MINISWE_VERSION", "GtHarnessMiniSwe228Agent"]
