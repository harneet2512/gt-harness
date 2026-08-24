"""Pier boundary for the released GT Harness Mini-SWE product.

DeepSWE is orchestrated by Pier rather than Harbor.  The product execution
must nevertheless remain the same ``gt-harness run`` boundary used by the
canonical Harbor product workflow.  This adapter only supplies Pier's runner
metadata and network contract; installation and execution stay inherited from
the canonical product adapter.
"""

from __future__ import annotations

from eval.harbor_gt_harness_adapter import GtHarnessMiniSwe228Agent


class PierGtHarnessMiniSwe228Agent(GtHarnessMiniSwe228Agent):
    """Expose the canonical GT Harness product through Pier's agent hooks."""

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
                "astral.sh",
                "files.pythonhosted.org",
                "openrouter.ai",
                "pypi.org",
            ]
        )

    def to_agent_info(self):
        from pier.models.trial.result import AgentInfo, ModelInfo

        requested = str(getattr(self, "model_name", "") or "").strip()
        model_info = ModelInfo(name=requested, provider="openrouter") if requested else None
        return AgentInfo(
            name=self.name(),
            version=self.version() or "0.9.0/miniswe-2.2.8",
            model_info=model_info,
        )


__all__ = ["PierGtHarnessMiniSwe228Agent"]
