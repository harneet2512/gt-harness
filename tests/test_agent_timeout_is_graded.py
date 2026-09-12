"""A benchmark timeout must reach the official verifier, not become an exception.

Both frozen GT-off controls grade their timeouts: Terminal-Bench 2.0 is 89/89
graded with 4 AgentTimeoutError trials counted as non-solves inside its 66/89,
and the DeepSWE 10-task control is 10/10 graded with no censoring. If the GT arm
raises instead, its timeouts leave the population entirely -- Pier records status
ERROR with reward null and graded false -- so GT is scored on a strictly easier
subset than the baseline it is compared against. These tests pin the asymmetry
shut.
"""

from __future__ import annotations

import pytest
from harbor.agents.installed.base import NonZeroAgentExitCodeError

from eval.miniswe_agent import (
    SUPERVISOR_TIMEOUT_EXIT_CODE,
    MiniSweAgent,
    MiniSweGtAgent,
)


def _exc(code: int) -> NonZeroAgentExitCodeError:
    """Harbor's own message shape; the exit code is only recoverable from it."""
    return NonZeroAgentExitCodeError(
        f"Command failed (exit {code}): exec python -m scripts.miniswe_supervisor"
    )


def test_timeout_exit_code_still_matches_the_supervisor_contract():
    """Drift guard: the absorbed code must stay the one the supervisor emits.

    If conserve_failure ever renumbers its terminals, absorbing 3 would start
    swallowing a real fault, so read the mapping rather than trusting a comment.
    """
    import inspect

    from scripts import miniswe_supervisor

    source = inspect.getsource(miniswe_supervisor.conserve_failure)
    assert 'exit_code = 3 if terminal == "timeout" else 5' in source
    assert '{3: "timeout", 4: "provider_failed", 5: "internal_error", 6: "setup_error", 7: "churn_abort"}' in source
    assert SUPERVISOR_TIMEOUT_EXIT_CODE == 3


def test_exit_code_is_recovered_from_harbor_message():
    assert MiniSweAgent._agent_exit_code(_exc(3)) == 3
    assert MiniSweAgent._agent_exit_code(_exc(137)) == 137
    assert MiniSweAgent._agent_exit_code(_exc(-9)) == -9


def test_unparseable_message_is_never_treated_as_a_timeout():
    # Fail closed: an unrecognised message must keep the original error.
    unparseable = NonZeroAgentExitCodeError("the runner died in some new way")
    assert MiniSweAgent._agent_exit_code(unparseable) is None
    with pytest.raises(NonZeroAgentExitCodeError):
        MiniSweAgent._absorb_agent_timeout(unparseable)


def test_exhausted_budget_returns_so_the_verifier_can_grade():
    # No exception: run() falls through and Harbor proceeds to the task's own
    # official verifier, which is the only thing that produces a reward.
    assert MiniSweAgent._absorb_agent_timeout(_exc(3)) is None


@pytest.mark.parametrize(
    ("code", "meaning"),
    [
        (4, "provider_failed"),
        (5, "internal_error"),
        (6, "setup_error"),
        (137, "oom_kill"),
        (1, "unclassified"),
    ],
)
def test_every_real_fault_still_raises(code, meaning):
    # Grading these would assert something about work the agent never got to
    # attempt. Only an exhausted budget is a result.
    with pytest.raises(NonZeroAgentExitCodeError):
        MiniSweAgent._absorb_agent_timeout(_exc(code))


def test_both_arms_share_one_timeout_policy():
    # A treatment difference that is partly a bookkeeping difference is not a
    # measurement. GT and GT-off must absorb the same code by the same code.
    assert (
        MiniSweGtAgent._absorb_agent_timeout.__func__
        is MiniSweAgent._absorb_agent_timeout.__func__
    )
    assert MiniSweGtAgent._agent_exit_code is MiniSweAgent._agent_exit_code


def test_the_workflow_reachable_adapter_inherits_the_policy():
    """The paid workflow runs eval.pier_gt_harness_adapter, not MiniSweGtAgent.

    A fix applied to a class the workflow never constructs is not a fix, so pin
    that the adapter reaching Pier is the one carrying this behaviour.
    """
    from eval.pier_gt_harness_adapter import PierGtHarnessMiniSwe246Agent

    assert PierGtHarnessMiniSwe246Agent.run is MiniSweGtAgent.run
    assert (
        PierGtHarnessMiniSwe246Agent._absorb_agent_timeout.__func__
        is MiniSweAgent._absorb_agent_timeout.__func__
    )
