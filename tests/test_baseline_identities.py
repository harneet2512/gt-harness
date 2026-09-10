"""What "the same test still passes" has to mean before it can be claimed.

The regression check compares test NAMES either side of the agent's work. A
name is not an identity. `test_widget.py::test_rejects_bad_input` passing before
and passing after is only conservation if it is the same test, and the file it
lives in is a file the agent can edit. Rewriting an assertion into `assert True`
conserves the name perfectly.

Test configuration and declared dependencies are the other two things the same
command reads and the agent can change. Those are recorded rather than treated
as blockers -- adding a fixture to conftest.py or a package to requirements is
ordinary work -- but a comparison that cannot say whether they moved is a
comparison making a claim it did not check.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from gt_engine.persistent_plan.baseline import (
    BaselineResult,
    compare_results,
    identity_paths_for,
)

CAPTURED = BaselineResult(
    status="captured",
    command=("pytest", "-q"),
    passed=2,
    passing_names=("tests/test_widget.py::test_keeps_shape",
                   "tests/test_loader.py::test_loads"),
    environment_sha256="env",
    source_revision="rev1",
    after_source_revision="rev1",
    test_file_digests=(("tests/test_loader.py", "loader-1"),
                       ("tests/test_widget.py", "widget-1")),
    config_sha256="config-1",
    dependency_sha256="deps-1",
)

AFTER = replace(CAPTURED, source_revision="rev2", after_source_revision="rev2")


def test_an_untouched_suite_is_still_intact():
    assert compare_results(CAPTURED, AFTER).status == "intact"


def test_a_previously_passing_test_whose_own_source_changed_is_not_carried():
    """The name survived; the test did not.

    An agent that rewrites the body of a test it cannot make pass conserves
    every name in the baseline. The only thing that distinguishes that from
    real work is the digest of the file the test lives in.
    """
    after = replace(AFTER, test_file_digests=(("tests/test_loader.py", "loader-1"),
                                              ("tests/test_widget.py", "widget-2")))
    report = compare_results(CAPTURED, after)
    assert report.status == "incomplete"
    assert report.changed_test_files == ("tests/test_widget.py",)
    assert "tests/test_widget.py::test_keeps_shape" in report.detail
    assert "tests/test_loader.py::test_loads" not in report.detail


def test_a_previously_passing_test_whose_file_disappeared_is_not_carried():
    after = replace(AFTER, test_file_digests=(("tests/test_loader.py", "loader-1"),
                                              ("tests/test_widget.py", "")))
    report = compare_results(CAPTURED, after)
    assert report.status == "incomplete"
    assert report.changed_test_files == ("tests/test_widget.py",)


def test_a_new_test_file_is_not_a_conservation_failure():
    """Adding tests is the work. Only the baseline's own files are compared."""
    after = replace(AFTER, test_file_digests=(
        ("tests/test_loader.py", "loader-1"),
        ("tests/test_widget.py", "widget-1"),
        ("tests/test_strict_mode.py", "new-1"),
    ))
    assert compare_results(CAPTURED, after).status == "intact"


def test_an_unrecorded_test_identity_cannot_be_claimed_as_conserved():
    """A runner whose names carry no file gives no identity to check.

    Silence here would read as "nothing changed". It has to read as "this was
    never established", because it was not.
    """
    baseline = replace(CAPTURED, test_file_digests=())
    report = compare_results(baseline, replace(AFTER, test_file_digests=()))
    assert report.status == "unknown"
    assert "test identity" in report.detail


@pytest.mark.parametrize(("field", "name"), [
    ("config_sha256", "test configuration"),
    ("dependency_sha256", "declared dependencies"),
])
def test_a_changed_configuration_or_dependency_set_is_reported_not_hidden(field, name):
    """Recorded, and named, without blocking ordinary work.

    Adding a fixture or a package is legitimate. Claiming an intact baseline
    while silently not knowing whether the command still selects the same suite
    is not.
    """
    report = compare_results(CAPTURED, replace(AFTER, **{field: "moved"}))
    assert report.status == "intact"
    assert name in report.detail
    assert field.removesuffix("_sha256") in report.as_dict()["changed_identities"]


def test_an_unchanged_run_reports_no_changed_identities():
    assert compare_results(CAPTURED, AFTER).as_dict()["changed_identities"] == []


def test_a_real_failure_still_outranks_every_identity_finding():
    """A named regression is the most useful thing the report can say."""
    after = replace(AFTER, failing_names=("tests/test_widget.py::test_keeps_shape",),
                    failed=1, passed=1,
                    passing_names=("tests/test_loader.py::test_loads",),
                    test_file_digests=(("tests/test_loader.py", "loader-1"),
                                       ("tests/test_widget.py", "widget-2")),
                    config_sha256="moved")
    report = compare_results(CAPTURED, after)
    assert report.status == "regressed"
    assert report.newly_failing == ("tests/test_widget.py::test_keeps_shape",)


def test_identity_paths_are_taken_from_the_names_the_runner_printed():
    assert identity_paths_for((
        "tests/test_widget.py::test_a",
        "tests/test_widget.py::TestClass::test_b",
        r"tests\test_windows.py::test_c",
        "./tests/test_dotted.py::test_d",
        "a_name_with_no_file",
    )) == ("tests/test_dotted.py", "tests/test_widget.py", "tests/test_windows.py")


def test_a_dotted_unittest_identity_resolves_against_the_real_tree():
    """unittest names a module, not a path. Resolve it, never guess it."""
    tree = ("tests/test_widget.py", "tests/helpers.py", "src/widget.py")
    assert identity_paths_for(("test_a (tests.test_widget.TestCase.test_a)",), tree) == ()
    assert identity_paths_for(("tests.test_widget.TestCase.test_a",), tree) == (
        "tests/test_widget.py",
    )
    # A module the tree does not contain resolves to nothing rather than to a
    # plausible-looking path that was never checked.
    assert identity_paths_for(("tests.test_absent.TestCase.test_a",), tree) == ()
    # Without a tree to resolve against there is nothing to resolve.
    assert identity_paths_for(("tests.test_widget.TestCase.test_a",)) == ()
