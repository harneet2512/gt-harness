from __future__ import annotations

from types import SimpleNamespace

import pytest

from eval.miniswe_agent import MiniSweAgent, ProviderBillingError


@pytest.mark.parametrize(
    "provider_message",
    [
        "OpenAIException - Insufficient Balance",
        "provider request failed with HTTP status 402",
    ],
)
def test_deepseek_billing_failure_never_maps_to_rate_limit(
    provider_message: str,
) -> None:
    agent = object.__new__(MiniSweAgent)
    result = SimpleNamespace(
        return_code=4,
        stdout=(
            f"{provider_message}\n"
            "Harbor wrapper guessed ApiRateLimitError; retrying rate limit"
        ),
        stderr="",
    )

    error = agent._classify_exec_error("mini-swe-agent run", result)

    assert type(error) is ProviderBillingError
    assert "Insufficient Balance" in str(error) or "402" in str(error)
