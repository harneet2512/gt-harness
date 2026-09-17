"""TB2 host route: the runtime-observation table, pinned as assertions.

Terminal-Bench 2.0 verifies post hoc.  Harbor mounts ``tests/`` at ``/tests``
*after* the agent phase and runs ``bash /tests/test.sh``, which installs
pytest (82 of the 89 tasks via ``uvx``, 7 via ``pip``) and runs
``pytest /tests/test_outputs.py``.  On 81 of the 89 tasks the agent cannot run
the grading tests at all, so the only test-shaped commands GroundTruth ever
sees are the agent's own.  Whether those commands produce an
``execution_evidence`` row of ``kind == "test"`` decides whether the
``test_result`` boundary - the sole boundary for ``covering_red`` and
``recovery`` and the red leg of ``submit_refusal`` - can ever occur.

Two surfaces are measured, and they disagree:

* the pinned wheel's ``groundtruth.runtime.patterns.TEST_RUNNER_RE``, which is
  certified and frozen; and
* this repo's ``gt_engine.runtime_observation``, which has since grown
  ``wrapper_stripped_command`` so that ``uvx`` is peeled off before the wheel
  sees the command.

Ported from ``tb2/host-route-offline-repro`` (``dff90fd8``); on that branch the
module was loaded under an alias package against ``GT_CONTEXT_PLAN_ROOT`` --
here it imports ``gt_engine`` natively.

Pure classification.  No provider call, no benchmark dispatch, no Docker.
"""

from __future__ import annotations

import importlib

import pytest

from gt_engine import runtime_observation


def _wheel_patterns():
    try:
        return importlib.import_module("groundtruth.runtime.patterns")
    except ImportError:  # pragma: no cover - environment dependent
        pytest.skip(
            "UNPROVEN, not a pass: the pinned groundtruth wheel is not importable, "
            "so TEST_RUNNER_RE cannot be measured here."
        )


# ---------------------------------------------------------------------------
# The empirically verified table.  Every row is CURRENT truth, measured, not
# quoted: `wheel_match` is `TEST_RUNNER_RE.search(command)` against the pinned
# wheel, `kind` is `compile_execution_evidence(...).kind` in
# gt_engine.runtime_observation, with `None` meaning no evidence row at all.
# ---------------------------------------------------------------------------

TABLE = (
    # command,                                wheel_match, kind
    ("bash tests/test.sh", False, None),
    ("uvx pytest tests/test_outputs.py -q", False, "test"),
    ("uv run pytest", True, "test"),
    ("pytest", True, "test"),
    ("python -m pytest", True, "test"),
    ("make", False, "build"),
    ("make test", True, "test"),
    ("make check", True, "test"),
    ("cargo test", True, "test"),
    ("gcc -o a a.c", False, None),
    ("python script.py", False, None),
    ("./run.sh", False, None),
)
TABLE_IDS = tuple(row[0] for row in TABLE)


@pytest.mark.parametrize(("command", "wheel_match", "kind"), TABLE, ids=TABLE_IDS)
def test_wheel_test_runner_regex_row(command, wheel_match, kind):
    patterns = _wheel_patterns()
    assert bool(patterns.TEST_RUNNER_RE.search(command)) is wheel_match, command


@pytest.mark.parametrize(("command", "wheel_match", "kind"), TABLE, ids=TABLE_IDS)
def test_execution_evidence_kind_row(command, wheel_match, kind):
    evidence = runtime_observation.compile_execution_evidence(
        command=command,
        output="",
        returncode=0,
        action_id=1,
        repository_revision="revision-1",
    )
    assert (evidence.kind if evidence is not None else None) == kind, command


def test_no_evidence_row_starves_the_test_result_boundary():
    """The load-bearing consequence of the ``None`` rows above.

    ``bash tests/test.sh`` is the exact command every TB2 verifier runs, and
    ``python script.py`` / ``./run.sh`` are what an agent reaches for on a
    class D/E/F task.  None of them produce any evidence row, so the
    ``test_result`` boundary never occurs while ``covering_red``, ``recovery``
    and the red leg of ``submit_refusal`` all report enabled.
    """
    for command in ("bash tests/test.sh", "python script.py", "./run.sh", "[ -f out.json ]"):
        assert (
            runtime_observation.compile_execution_evidence(
                command=command,
                output="",
                returncode=1,
                action_id=1,
                repository_revision="revision-1",
            )
            is None
        ), command


def test_uvx_is_recognised_in_context_plan_but_not_in_the_pinned_wheel():
    """The 82-task ``uvx`` gap, and where it is and is not closed today.

    The pinned wheel's runner detection never learned ``uvx``.
    ``gt_engine`` compensates outside the wheel by peeling the wrapper before
    handing the command over, so the row exists there.  Both halves are
    asserted because only the wheel's half is certified.
    """
    patterns = _wheel_patterns()

    assert patterns.TEST_RUNNER_RE.search("uvx pytest -q") is None
    assert patterns.TEST_RUNNER_RE.search("uv run pytest -q") is not None
    assert runtime_observation.wrapper_stripped_command("uvx pytest -q") == "pytest -q"
    evidence = runtime_observation.compile_execution_evidence(
        command="uvx pytest -q",
        output="",
        returncode=0,
        action_id=1,
        repository_revision="revision-1",
    )
    assert evidence is not None and evidence.kind == "test"


# ---------------------------------------------------------------------------
# Footgun 1: a bare ``make`` exiting 0 writes a positive evidence row on a
# task where no test ran.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "tb2_bare_make_books_pass: _BUILD_RE matches `make(?:\\s|$)`, so a bare "
        "`make` yields kind='build', and _execution_outcome_guard turns "
        "returncode 0 into outcome='pass' with no test output of any kind.  On "
        "build-pmars, build-pov-ray, compile-compcert and the make-* tasks that "
        "is a positive evidence row manufactured from a compile that proves "
        "nothing about the hidden suite."
    ),
)
def test_bare_make_exit_zero_is_not_booked_as_a_pass():
    evidence = runtime_observation.compile_execution_evidence(
        command="make",
        output="make: Nothing to be done for 'all'.\n",
        returncode=0,
        action_id=1,
        repository_revision="revision-1",
    )
    assert evidence is not None and evidence.kind == "build"
    assert evidence.outcome != "pass", (
        "a build that ran no test must not carry a passing outcome"
    )


def test_bare_make_current_truth_is_a_pass_row():
    """GREEN companion: pin today's behaviour so a fix has to move this row."""
    evidence = runtime_observation.compile_execution_evidence(
        command="make",
        output="make: Nothing to be done for 'all'.\n",
        returncode=0,
        action_id=1,
        repository_revision="revision-1",
    )
    assert evidence is not None
    assert (evidence.kind, evidence.outcome) == ("build", "pass")
    assert evidence.observed_test_outcome == ""


# ---------------------------------------------------------------------------
# Footgun 2: the wheel's ``_non_test_outcome`` reads returncode 0 as "pass"
# for every non-runner kind.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "tb2_non_test_rc0_reads_as_pass: groundtruth/runtime/patterns.py "
        "(_non_test_outcome) returns 'pass' for any non-runner kind whose "
        "returncode is 0.  `gcc -o a a.c` exiting 0 is a COMPILER_CHECK that "
        "compiled, not a task that passed, and on the 81 TB2 tasks whose "
        "grading suite the agent cannot run it is the only positive signal "
        "GroundTruth ever sees."
    ),
)
def test_clean_compile_is_not_booked_as_a_passing_validation():
    patterns = _wheel_patterns()
    observation = patterns.classify_validation_observation("gcc -o a a.c", "", 0)

    assert observation.kind is patterns.ValidationKind.COMPILER_CHECK
    assert observation.outcome != "pass", (
        "a clean compile is execution truth, not a passing validation"
    )


def test_clean_compile_current_truth_is_a_pass_outcome():
    """GREEN companion: pin today's behaviour so a fix has to move this row."""
    patterns = _wheel_patterns()
    observation = patterns.classify_validation_observation("gcc -o a a.c", "", 0)

    assert observation.kind is patterns.ValidationKind.COMPILER_CHECK
    assert observation.outcome == "pass"
    assert observation.protocol == "command"
    # ... and the same command produces no execution_evidence row, so the
    # "pass" lives only on the validation surface.
    assert (
        runtime_observation.compile_execution_evidence(
            command="gcc -o a a.c",
            output="",
            returncode=0,
            action_id=1,
            repository_revision="revision-1",
        )
        is None
    )
