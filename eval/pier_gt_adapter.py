"""Thin Pier boundary adapter for the runner-neutral central GT agent.

The central runtime deliberately depends only on Harbor's agent contract.  Pier
has equivalent lifecycle hooks and Pydantic identity classes, but those are
runner concerns.  This module is the only place where Pier-specific types are
translated when the DeepSWE v1.1 workflow uses Pier.
"""

from __future__ import annotations

from eval.gt_central_agent import MiniSweCentralAgent


class PierMiniSweCentralAgent(MiniSweCentralAgent):
    """Expose the minimal Pier hooks without changing GT execution semantics."""

    def install_spec(self):
        # GT is host-owned; there is no agent package to install in the task
        # environment.
        return None

    def network_allowlist(self):
        # Provider calls stay in the host process, so the task agent needs no
        # network domains.  The import is isolated to this runner adapter.
        from pier.models.agent.network import NetworkAllowlist

        return NetworkAllowlist()

    def to_agent_info(self):
        # Pier and Harbor use distinct but equivalent Pydantic classes.  Keep
        # this conversion at the runner boundary, never in central runtime.
        from pier.models.trial.result import AgentInfo, ModelInfo

        model_info = None
        if getattr(self, "_parsed_model_name", None):
            model_info = ModelInfo(
                name=self._parsed_model_name,
                provider=getattr(self, "_parsed_model_provider", None),
            )
        return AgentInfo(
            name=self.name(),
            version=self.version() or "unknown",
            model_info=model_info,
        )

