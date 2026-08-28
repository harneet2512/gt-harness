"""Pier boundary for the released GT Harness Mini-SWE product.

DeepSWE is orchestrated by Pier rather than Harbor.  The product execution
must nevertheless remain the same ``gt-harness run`` boundary used by the
canonical Harbor product workflow.  This adapter only supplies Pier's runner
metadata and network contract; installation and execution stay inherited from
the canonical product adapter.
"""

from __future__ import annotations

from typing import Any

from eval.harbor_gt_harness_adapter import GtHarnessMiniSwe246Agent


class _PierAgentScopedEnvironment:
    """Apply Pier's filtered-egress variables to installed-agent commands.

    GT's shared product adapter inherits Harbor's installed-agent base so the
    same implementation remains usable by Terminal-Bench.  Pier's fork adds
    ``environment.agent_process_env`` at execution time; Harbor's base does
    not call it.  This proxy supplies that one compatibility behavior without
    forking installation or product execution.
    """

    def __init__(self, environment: Any) -> None:
        self._environment = environment

    def __getattr__(self, name: str) -> Any:
        return getattr(self._environment, name)

    async def exec(self, command: str, **kwargs: Any) -> Any:
        env = kwargs.pop("env", None)
        kwargs["env"] = self._environment.agent_process_env(env)
        return await self._environment.exec(command, **kwargs)


class PierGtHarnessMiniSwe246Agent(GtHarnessMiniSwe246Agent):
    """Expose the canonical GT Harness product through Pier's agent hooks."""

    @staticmethod
    def _scoped_environment(environment: Any) -> _PierAgentScopedEnvironment:
        if isinstance(environment, _PierAgentScopedEnvironment):
            return environment
        return _PierAgentScopedEnvironment(environment)

    async def _exec(
        self,
        environment,
        command: str,
        user=None,
        env=None,
        cwd=None,
        timeout_sec=None,
    ):
        return await super()._exec(
            self._scoped_environment(environment),
            command,
            user=user,
            env=env,
            cwd=cwd,
            timeout_sec=timeout_sec,
        )

    async def _exec_product_secret_safe(self, environment, command, env) -> None:
        await super()._exec_product_secret_safe(
            self._scoped_environment(environment),
            command,
            env,
        )

    def install_spec(self):
        # Installation is performed by the inherited, source-uploading
        # ``install`` method.  Pier needs this hook only while constructing its
        # environment contract.
        return None

    def network_allowlist(self):
        from pier.models.agent.network import NetworkAllowlist

        # The product model call runs inside the task environment.  Package
        # domains are required only during the deterministic installed-agent
        # setup; no arbitrary task egress is granted.
        return NetworkAllowlist(
            domains=[
                ".githubusercontent.com",
                "astral.sh",
                "files.pythonhosted.org",
                "github.com",
                "api.deepseek.com",
                "pypi.org",
                "releases.astral.sh",
            ]
        )

    def to_agent_info(self):
        from pier.models.trial.result import AgentInfo, ModelInfo

        requested = str(getattr(self, "model_name", "") or "").strip()
        model_info = ModelInfo(name=requested, provider="deepseek") if requested else None
        return AgentInfo(
            name=self.name(),
            version=self.version() or "0.9.0/miniswe-2.4.6",
            model_info=model_info,
        )


__all__ = ["PierGtHarnessMiniSwe246Agent"]
