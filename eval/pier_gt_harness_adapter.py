"""Pier boundary for the canonical Mini-SWE-Agent 2.4.6 product."""

from __future__ import annotations

from eval.miniswe_agent import MiniSweGtAgent


class PierGtHarnessMiniSwe246Agent(MiniSweGtAgent):
    """The workflow-reachable Pier adapter; execution stays in one shared adapter."""

    MINISWE_AGENT_VERSION = "2.4.6"

    def install_spec(self):
        return None

    def network_allowlist(self):
        from pier.models.agent.network import NetworkAllowlist

        # Installation inputs are staged and verified before task startup.
        # Only the configured model transport receives network access.
        return NetworkAllowlist(domains=["api.deepseek.com"])

    def to_agent_info(self):
        # Harbor 0.20 validates TrialResult against its own result models.
        # The old Pier result classes are structurally similar but are a
        # different Pydantic type and fail validation at live job startup.
        from harbor.models.trial.result import AgentInfo, ModelInfo

        requested = str(getattr(self, "model_name", "") or "").strip()
        model_info = ModelInfo(name=requested, provider="deepseek") if requested else None
        return AgentInfo(
            name=self.name(),
            version=self.version() or "1.0.0/miniswe-2.4.6",
            model_info=model_info,
        )


__all__ = ["PierGtHarnessMiniSwe246Agent"]
