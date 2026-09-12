"""Churn-governor contract tests.

Threshold semantics mirror run 34656860834: churn onset ~turn 30, two edit
transactions in 840 commands, 54% archaeology commands. The governor must
steer early, abort long before the deadline, and stay quiet on a healthy
edit/test cadence.
"""
from __future__ import annotations

from gt_engine.churn_governor import ChurnGovernor


def _feed(g: ChurnGovernor, commands: list[str], productive: set[int] | None = None):
    productive = productive or set()
    signals = []
    for i, cmd in enumerate(commands):
        signals.append(g.observe(cmd, productive=i in productive))
    return signals


def test_healthy_run_stays_quiet():
    g = ChurnGovernor()
    commands = ["ls", "cat src/a.py", "edit a.py", "pytest -q"] * 20
    signals = _feed(g, commands)
    assert set(signals) <= {""}
    assert not g.aborted


def test_reads_alone_do_not_abort_indefinitely_but_eventually_stall():
    """Pure reading is not churn, but 50 actions without a single edit or
    check is a doomed run even when the reads are task files."""
    g = ChurnGovernor()
    commands = ["cat file%d.py" % i for i in range(60)]
    signals = _feed(g, commands)
    assert signals[-1] == "abort"
    assert g.aborted


def test_archaeology_steer_then_abort_mirrors_the_paid_run():
    """The paid-run pattern: exploration, then gt-state/trajectory mining
    with no edits. Steer first, abort when the loop survives the steer."""
    g = ChurnGovernor()
    commands = (
        ["ls", "cat src/node_visitor.py", "cat src/context.py"] * 3
        + ["cat gt-state/deliveries/abc.json",
           "python3 -c 'import json;print(json.load(open(\"/logs/agent/miniswe_trajectory.json\")))'",
           "gt-evidence read 4bb060e 0 8192",
           "grep -rn GT_PERSISTENT_PLAN /logs/agent"] * 20
    )
    signals = _feed(g, commands)
    assert "steer" in signals
    assert signals.index("steer") < signals.index("abort")
    assert g.aborted
    # The abort lands near turn ~60-90, not at turn 769.
    assert signals.index("abort") < 100
    assert g.steers_issued >= 1


def test_edit_after_steer_prevents_abort():
    """A steer that works - the agent goes back to editing - must not abort.
    The churn block is long enough to trip the steer (~25 stalls) but ends
    before the post-steer abort (~40), because the agent recovers."""
    g = ChurnGovernor()
    churn = ["cat gt-state/deliveries/x.json", "gt-evidence read a 0 8192"] * 14
    signals = _feed(g, churn)
    assert "steer" in signals
    recovery = ["edit src/fix.py", "pytest -q", "cat src/fix.py"] * 20
    signals += _feed(g, recovery, productive={3 * i for i in range(20)})
    assert "abort" not in signals[-len(recovery):]


def test_identical_command_hammering_aborts_fast():
    g = ChurnGovernor()
    signals = _feed(g, ["cat same_file.py"] * 20)
    assert signals[-1] == "abort"


def test_verification_commands_reset_stall():
    g = ChurnGovernor()
    commands = (
        ["cat file%d.py" % i for i in range(20)]
        + ["pytest -q"]
        + ["cat file%d.py" % i for i in range(20, 40)]
        + ["pytest -q"]
    )
    signals = _feed(g, commands)
    assert "abort" not in signals
    assert g.stall_turns == 0


def test_disabled_governor_never_fires():
    g = ChurnGovernor(disabled=True)
    signals = _feed(g, ["cat gt-state/x"] * 200)
    assert set(signals) == {""}
    assert not g.aborted


def test_abort_is_sticky():
    g = ChurnGovernor()
    _feed(g, ["cat f.py"] * 60)
    assert g.aborted
    assert g.observe("edit f.py", productive=True) == "abort"
