"""The measuring instrument, measured.

`scripts/graph_transition_study.py` produces the numbers that answer whether the
batch amendment is worth having. An instrument with no tests produces confident
wrong numbers, and a performance claim is exactly the kind of claim nobody
re-derives by hand. So the parts that decide what gets compared are tested here
against a stub producer: no graph is built, because none of these properties is
about the graph.

The deadlock test is the one that matters most. The first version of `_run`
polled `poll()` in a loop and called `communicate()` only after the process had
exited. That hangs forever the moment a child writes more than a pipe buffer:
the child blocks on a full stderr, so it never exits, so `poll()` never returns,
so nothing drains the pipe. It survived `click`, whose output is small, and the
repositories this study exists for are the large ones.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.graph_transition_study import (  # noqa: E402
    _apply_transition,
    _largest_source,
    _median,
    _run,
    study_repository,
)


def test_a_chatty_child_does_not_deadlock_the_measurement():
    """More output than a pipe buffer, read while the process is still alive."""
    program = (
        "import sys;"
        "sys.stderr.write('x' * 400000);"
        "sys.stderr.write('\\nFiles:      7\\nNodes:      9\\n');"
        "sys.exit(0)"
    )
    measured = _run([sys.executable, "-c", program])
    assert measured["returncode"] == 0
    assert measured["counts"] == {"files": 7, "nodes": 9}


def test_a_failing_run_is_reported_rather_than_scored(tmp_path):
    measured = _run([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"])
    assert measured["returncode"] == 3
    assert "boom" in measured["stderr_tail"]


def test_the_edited_file_is_the_largest_tracked_parseable_one(tmp_path):
    """Deterministic, and never a file the producer would not read.

    An untracked file is not part of the repository under measurement, and a
    file in a language the producer ignores would make the transition a no-op.
    """
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "small.py").write_text("a = 1\n", encoding="utf-8")
    (root / "pkg" / "big.py").write_text("b = 1\n" * 200, encoding="utf-8")
    (root / "huge.md").write_text("m\n" * 5000, encoding="utf-8")
    (root / "untracked.py").write_text("c = 1\n" * 4000, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "pkg", "huge.md"], check=True)

    chosen = _largest_source(root)
    assert chosen is not None
    assert chosen.name == "big.py"


def test_a_repository_git_cannot_list_yields_no_target(tmp_path):
    root = tmp_path / "loose"
    root.mkdir()
    (root / "a.py").write_text("a = 1\n", encoding="utf-8")
    assert _largest_source(root) is None


def test_the_transition_appends_and_moves_no_existing_line(tmp_path):
    """Line numbers must not shift, or the measurement is of a reflow."""
    path = tmp_path / "module.py"
    original = "def existing():\n    return 1\n"
    path.write_text(original, encoding="utf-8")
    _apply_transition(path)
    updated = path.read_text(encoding="utf-8")
    assert updated.startswith(original)
    assert "gt_transition_probe" in updated
    assert updated.splitlines()[:2] == original.splitlines()[:2]


@pytest.mark.parametrize(("values", "expected"), [
    ([], 0.0), ([4.0], 4.0), ([3.0, 1.0], 2.0), ([5.0, 1.0, 3.0], 3.0),
    ([4.0, 1.0, 3.0, 2.0], 2.5),
])
def test_the_median_is_the_median(values, expected):
    assert _median(values) == expected


def test_the_arms_alternate_and_only_the_candidate_names_a_parent(tmp_path, monkeypatch):
    """The two properties the comparison rests on.

    Alternating is what keeps machine drift off one arm. Naming the parent is
    what makes the candidate an amend rather than a second full build, and if
    that flag were dropped the study would compare a rebuild against a rebuild
    and report a speedup of one.
    """
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("a = 1\n" * 50, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)

    seen: list[list[str]] = []

    def fake_run(command):
        seen.append(list(command))
        return {"seconds": 1.0, "returncode": 0, "peak_mb": 1.0, "counts": {},
                "amend_result": {}, "stderr_tail": ""}

    monkeypatch.setattr("scripts.graph_transition_study._run", fake_run)
    monkeypatch.setattr("scripts.graph_transition_study._parity_digest",
                        lambda _database: {"nodes": "1:aaaa"})

    report = study_repository("repo", root, tmp_path / "work", "/bin/true",
                              repetitions=3, max_files=10, workers=1)
    assert report["status"] == "measured"
    # One parent build, then three baseline/candidate pairs.
    assert len(seen) == 7
    assert not any("-amend-parent" in item for item in seen[0])
    arms = [row["arm"] for row in report["runs"]]
    assert arms == ["baseline", "candidate"] * 3
    for command, arm in zip(seen[1:], arms, strict=True):
        assert ("-amend-parent" in command) is (arm == "candidate"), (arm, command)


def test_a_disagreeing_run_is_reported_as_a_parity_failure(tmp_path, monkeypatch):
    """The claim is equality across every run, not equality within an arm."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("a = 1\n" * 50, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)

    monkeypatch.setattr("scripts.graph_transition_study._run",
                        lambda command: {"seconds": 1.0, "returncode": 0, "peak_mb": 1.0,
                                         "counts": {}, "amend_result": {}, "stderr_tail": ""})
    answers = iter([{"nodes": "1:aaaa"}, {"nodes": "1:bbbb"}])
    monkeypatch.setattr("scripts.graph_transition_study._parity_digest",
                        lambda _database: next(answers))

    report = study_repository("repo", root, tmp_path / "work", "/bin/true",
                              repetitions=1, max_files=10, workers=1)
    assert report["semantic_parity"] is False
    assert report["distinct_digests"] == 2
    # Both readings are retained when they disagree; one is enough when they do not.
    assert len(report["parity"]) == 2


def test_a_failed_arm_does_not_enter_the_medians(tmp_path, monkeypatch):
    """A crash is not a fast run."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("a = 1\n" * 50, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)

    calls = {"n": 0}

    def fake_run(command):
        calls["n"] += 1
        failed = "-amend-parent" in command and calls["n"] == 3
        return {"seconds": 0.0 if failed else 2.0, "returncode": 1 if failed else 0,
                "peak_mb": 1.0, "counts": {}, "amend_result": {}, "stderr_tail": ""}

    monkeypatch.setattr("scripts.graph_transition_study._run", fake_run)
    monkeypatch.setattr("scripts.graph_transition_study._parity_digest",
                        lambda _database: {"nodes": "1:aaaa"})

    report = study_repository("repo", root, tmp_path / "work", "/bin/true",
                              repetitions=2, max_files=10, workers=1)
    assert 0.0 not in report["candidate_seconds"]
    assert report["candidate_median"] == 2.0
