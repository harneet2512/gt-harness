"""Pins for eval/swe_agent.py's pure command builders (grading integrity).

Everything here is a string invariant a refactor could silently break:

1. _SNAPSHOT ordering: GT's ``.gt/`` index dir is ``rm -rf``'ed BEFORE
   ``git add -A`` — the only reason .gt can never reach the staged model
   patch or the graded tree.
2. Baseline run-command shape: byte-exact against the historical string
   (pre-GTNanoSweAgent), so the frozen baseline arm stays reproducible.
3. GT defaults: ``gt_root`` resolves ON to /testbed (kwarg/env still win),
   GT_RL_PROFILE resolves kwarg > env fallback > "2".

harbor is an eval-only dependency; skip cleanly where it isn't installed
(the CI eval workflows install harbor==0.20.0; the unit-test venv may not).
"""
from __future__ import annotations

import shlex

import pytest

pytest.importorskip("harbor")

from eval.swe_agent import (  # noqa: E402
    _SNAPSHOT,
    _TASK_TEMPLATE,
    _WORKDIR,
    GTNanoSweAgent,
    NanoSweAgent,
)


def test_snapshot_removes_gt_dir_before_staging():
    rm = _SNAPSHOT.index("rm -rf .gt")
    add = _SNAPSHOT.index("git add -A")
    diff = _SNAPSHOT.index("git diff") if "git diff" in _SNAPSHOT else _SNAPSHOT.index(
        "diff --cached")
    assert _SNAPSHOT.startswith(f"cd {_WORKDIR} && ")
    assert rm < add < diff, "must be: rm .gt -> stage -> snapshot the patch"


def test_baseline_run_command_is_byte_identical_to_historical():
    issue = "Widget crashes when frobnicating\nwith a 'quote' and $dollar"
    task = _TASK_TEMPLATE.format(workdir=_WORKDIR, issue=issue)
    a = NanoSweAgent(logs_dir=".", model_name="anthropic/claude-opus-4-8")
    assert a.build_cli_flags() == ""  # baseline: GT flag absent by default
    cmd = a._run_command(task, "claude-opus-4-8", a.build_cli_flags())
    # The exact pre-refactor inline string (eval/swe_agent.py @ a2e3bfa).
    assert cmd == (
        f"cd {_WORKDIR} && "
        f'"$HOME/.local/bin/nano" run {shlex.quote(task)} '
        f"--model claude-opus-4-8 --max-iterations 100 "
        " "  # empty gt_flags slot
        "</dev/null 2>&1 | tee /logs/agent/nano.txt || true"
    )


def test_gt_arm_defaults_and_overrides():
    g = GTNanoSweAgent(logs_dir=".", model_name="anthropic/claude-opus-4-8")
    assert g.build_cli_flags() == "--gt-root /testbed"
    assert g.resolve_env_vars() == {"GT_RL_PROFILE": "2"}
    g2 = GTNanoSweAgent(
        logs_dir=".", model_name="m", gt_root="/elsewhere", gt_profile="0"
    )
    assert g2.build_cli_flags() == "--gt-root /elsewhere"
    assert g2.resolve_env_vars() == {"GT_RL_PROFILE": "0"}
    assert NanoSweAgent.name() == "nano-swe"
    assert GTNanoSweAgent.name() == "nano-swe-gt"
