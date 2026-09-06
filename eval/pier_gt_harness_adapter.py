"""Pier boundary for the canonical Mini-SWE-Agent 2.4.6 product."""

from __future__ import annotations

from urllib.parse import urlsplit

from eval.miniswe_agent import MiniSweGtAgent


class PierGtHarnessMiniSwe246Agent(MiniSweGtAgent):
    """The workflow-reachable Pier adapter; execution stays in one shared adapter."""

    MINISWE_AGENT_VERSION = "2.4.6"

    def __init__(self, *args, synthetic_transport_url: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._synthetic_transport_url = synthetic_transport_url

    def install_spec(self):
        return None

    def _run_command(self, instruction: str, model: str, extra_args: str = "") -> str:
        if self._synthetic_transport_url:
            extra_args += " --synthetic-transport "
        return super()._run_command(instruction, model, extra_args)

    def network_allowlist(self):
        from pier.models.agent.network import NetworkAllowlist

        if self._synthetic_transport_url:
            address = urlsplit(self._synthetic_transport_url)
            if (self.model_name != "synthetic-transport" or address.scheme != "http"
                    or address.hostname not in {"host.docker.internal", "127.0.0.1"}
                    or address.username or address.password or address.port is None):
                raise ValueError("invalid synthetic transport endpoint")
            return NetworkAllowlist(domains=[address.hostname])

        # Installation inputs are staged and verified before task startup.
        # Only the configured model transport receives network access.
        return NetworkAllowlist(domains=["openrouter.ai"])

    def to_agent_info(self):
        # Pier constructs and validates its own TrialResult.  Harbor's
        # structurally similar Pydantic classes are a different type and are
        # rejected by Pier before the first task/model call.
        from pier.models.trial.result import AgentInfo, ModelInfo

        requested = str(getattr(self, "model_name", "") or "").strip()
        provider = getattr(self, "_parsed_model_provider", None)
        model = getattr(self, "_parsed_model_name", None) or requested
        model_info = ModelInfo(name=model, provider=provider) if model else None
        return AgentInfo(
            name=self.name(),
            version=self.version() or "1.0.0/miniswe-2.4.6",
            model_info=model_info,
        )


__all__ = ["PierGtHarnessMiniSwe246Agent"]
