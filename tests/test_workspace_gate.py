"""Per-workspace gating of GT's code-repo guidance (gt_engine.workspace_gate).

Campaign 2026-09-29 (same model, space-bunny-alpha): on Terminal-Bench 2.0 the
code-repo workflow ("gt-query first", "gt-impact", write tests) and the submit
review were applied to QEMU / data / ops workspaces with no repository, and the
review flagged every task literal as "[not in your changes]" because it diffs
against git (qemu-startup rep4). These tests pin the gate that keeps that
guidance to real code repositories.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from gt_engine.attached_delivery import attached_instance_template, instance_template_for
from gt_engine.submit_review import session_review
from gt_engine.workspace_gate import MIN_SOURCE_FILES, is_code_workspace, lacks_repository

STOCK = (
    "Please solve this issue: {{task}}\n\n"
    "## Recommended Workflow\n\n1. Analyze the codebase\n"
    "2. Submit: `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`\n\n"
    "## Command Execution Rules\n\nOne command per response.\n"
    "<example_response>\nls -la\n</example_response>\n"
)


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch, tmp_path):
    """Each test sees only its own fixture directory: no parent checkout above
    tmp_path, no inherited GIT_DIR / GIT_WORK_TREE."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)


def _git_repo(root: Path, n_sources: int) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n_sources):
        (root / f"mod_{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"], check=True)
    return root


def test_the_threshold_is_inclusive(tmp_path):
    assert not is_code_workspace(str(_git_repo(tmp_path / "below", MIN_SOURCE_FILES - 1)))
    assert is_code_workspace(str(_git_repo(tmp_path / "at", MIN_SOURCE_FILES)))


def test_a_subdirectory_of_a_code_repo_counts_the_whole_repo(tmp_path):
    repo = _git_repo(tmp_path / "repo", MIN_SOURCE_FILES)
    sub = repo / "docs"
    sub.mkdir()

    assert is_code_workspace(str(sub))


def test_a_git_error_other_than_no_repository_keeps_gt_on(monkeypatch, tmp_path):
    """A container whose repo is owned by another user fails `git rev-parse` with
    "dubious ownership"; the gate must fail open, never silently drop GT."""
    from gt_engine import workspace_gate

    def dubious(_cwd, *_args):
        return subprocess.CompletedProcess(
            ["git"], 128, "", "fatal: detected dubious ownership in repository at '/app'")

    (tmp_path / ".git").mkdir()  # a repository git refuses to read: stay unknown, fail open
    monkeypatch.setattr(workspace_gate, "_git", dubious)

    assert workspace_gate.is_code_workspace(str(tmp_path))
    assert not workspace_gate.lacks_repository(str(tmp_path))


def test_git_runs_in_the_c_locale(monkeypatch, tmp_path):
    """The gate matches git's English "not a git repository"; a localized container
    (LANG=de_DE, ...) must not turn every answer into UNKNOWN."""
    from gt_engine import workspace_gate

    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(argv, 128, "", "fatal: not a git repository")

    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.setattr(workspace_gate.subprocess, "run", fake_run)

    assert workspace_gate.lacks_repository(str(tmp_path))
    assert seen.get("LC_ALL") == "C" and seen.get("LANGUAGE") == ""
    assert seen.get("LANG") == "de_DE.UTF-8"  # the rest of the environment is kept


def test_a_git_timeout_keeps_gt_on(monkeypatch, tmp_path):
    from gt_engine import workspace_gate

    (tmp_path / ".git").mkdir()  # git timed out on a real repository: fail open
    monkeypatch.setattr(workspace_gate, "_git", lambda _cwd, *_args: None)

    assert workspace_gate.is_code_workspace(str(tmp_path))


def test_no_git_binary_and_no_git_entry_is_no_repository(monkeypatch, tmp_path):
    """Most TB2 images ship without git (validation run 36665598281: 7 of 8 tasks
    read 'unknown', e.g. dna-assembly = one sequences.fasta). No .git anywhere
    up the tree means no repository whatever git would say."""
    from gt_engine import workspace_gate

    (tmp_path / "sequences.fasta").write_text(">s\nACGT\n", encoding="utf-8")
    monkeypatch.setattr(workspace_gate, "_git", lambda _cwd, *_args: None)

    assert workspace_gate.repository_state(str(tmp_path)) == workspace_gate.NO_REPOSITORY
    assert not workspace_gate.is_code_workspace(str(tmp_path))


def test_a_git_file_counts_as_a_git_entry(monkeypatch, tmp_path):
    """Worktrees and submodules have a .git FILE, not a directory."""
    from gt_engine import workspace_gate

    (tmp_path / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n", encoding="utf-8")
    monkeypatch.setattr(workspace_gate, "_git", lambda _cwd, *_args: None)

    assert workspace_gate.repository_state(str(tmp_path)) == workspace_gate.UNKNOWN


def test_the_gate_decision_is_reported(tmp_path):
    from gt_engine.workspace_gate import gate_report

    report = gate_report(str(tmp_path))

    assert report == {"code_workspace": False, "repository": "none", "source_files": 0}


def test_a_workspace_without_git_is_not_a_code_workspace(tmp_path):
    (tmp_path / "alpine.iso").write_bytes(b"\0" * 16)
    for i in range(MIN_SOURCE_FILES + 5):
        (tmp_path / f"s{i}.c").write_text("int main(){return 0;}\n", encoding="utf-8")

    assert lacks_repository(str(tmp_path))
    assert not is_code_workspace(str(tmp_path))


def test_a_git_repo_with_enough_sources_is_a_code_workspace(tmp_path):
    repo = _git_repo(tmp_path / "repo", MIN_SOURCE_FILES)

    assert not lacks_repository(str(repo))
    assert is_code_workspace(str(repo))


def test_a_git_repo_with_few_sources_is_not_a_code_workspace(tmp_path):
    repo = _git_repo(tmp_path / "tiny", 3)

    assert not lacks_repository(str(repo))
    assert not is_code_workspace(str(repo))


def test_non_code_workspace_keeps_the_stock_task_template(tmp_path):
    template = instance_template_for(STOCK, str(tmp_path))

    assert template == STOCK
    assert "gt-query" not in template


def test_code_workspace_gets_the_gt_workflow(tmp_path):
    repo = _git_repo(tmp_path / "repo", MIN_SOURCE_FILES)

    assert instance_template_for(STOCK, str(repo)) == attached_instance_template(STOCK)


def test_submit_review_is_skipped_without_git(tmp_path):
    (tmp_path / "start.sh").write_text("qemu-system-x86_64 -cdrom alpine.iso\n", encoding="utf-8")
    engine = SimpleNamespace(issue_text="Start `alpine.iso` so `telnet 127.0.0.1 6665` shows a login prompt.",
                             repo_root=str(tmp_path))
    session = SimpleNamespace(_engine=engine)

    assert session_review(session, "") == ""


def test_submit_review_still_runs_in_a_git_repo(monkeypatch, tmp_path):
    """Positive control for the skip above: same task, a real repository."""
    monkeypatch.delenv("GT_SUBMIT_REVIEW_MODE", raising=False)
    repo = _git_repo(tmp_path / "repo", 2)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    engine = SimpleNamespace(issue_text="Start `alpine.iso` so `telnet 127.0.0.1 6665` shows a login prompt.",
                             repo_root=str(repo))

    assert session_review(SimpleNamespace(_engine=engine), head).startswith("[GT] before you submit")


def test_submit_review_is_skipped_in_an_empty_git_init(tmp_path):
    """`git init` with nothing committed: no commit to diff against, so no review
    (every literal would otherwise read as missing)."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "start.sh").write_text("qemu-system-x86_64 -cdrom alpine.iso\n", encoding="utf-8")
    engine = SimpleNamespace(issue_text="Start `alpine.iso` so `telnet 127.0.0.1 6665` shows a login prompt.",
                             repo_root=str(tmp_path))

    assert session_review(SimpleNamespace(_engine=engine), "") == ""


def test_the_review_is_skipped_when_git_refuses_the_repository(monkeypatch, tmp_path):
    from gt_engine import workspace_gate

    monkeypatch.setattr(workspace_gate, "_git", lambda _cwd, *_args: None)  # no git binary

    assert not workspace_gate.has_start_commit(str(tmp_path))


def test_untracked_sources_count(tmp_path):
    """A `git init` whose sources were never committed is still a code repository."""
    for i in range(MIN_SOURCE_FILES):
        (tmp_path / f"m{i}.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    assert is_code_workspace(str(tmp_path))


def test_non_code_files_do_not_count(tmp_path):
    for i in range(MIN_SOURCE_FILES + 5):
        (tmp_path / f"n{i}.sh").write_text("echo hi\n", encoding="utf-8")
        (tmp_path / f"d{i}.md").write_text("# doc\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    assert not is_code_workspace(str(tmp_path))


def test_the_indexer_s_languages_count(tmp_path):
    for i in range(MIN_SOURCE_FILES):
        (tmp_path / f"m{i}.EX").write_text("defmodule M do end\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    assert is_code_workspace(str(tmp_path))  # Elixir, upper-case extension


def test_a_file_listing_failure_fails_open(monkeypatch, tmp_path):
    from gt_engine import workspace_gate

    def fake(_cwd, *args):
        if args[0] == "rev-parse":
            return subprocess.CompletedProcess(["git"], 0, "true\n", "")
        return subprocess.CompletedProcess(["git"], 128, "", "fatal: boom")

    monkeypatch.setattr(workspace_gate, "_git", fake)

    assert workspace_gate.gate_report(str(tmp_path)) == {
        "code_workspace": True, "repository": "git", "source_files": None}


def test_an_inherited_git_dir_is_ignored(monkeypatch, tmp_path):
    """A leaked GIT_DIR must not make the gate describe another repository."""
    other = _git_repo(tmp_path / "other", MIN_SOURCE_FILES)
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))

    assert not is_code_workspace(str(plain))


def test_the_applied_template_follows_a_precomputed_report(tmp_path):
    repo = _git_repo(tmp_path / "repo", MIN_SOURCE_FILES)

    assert instance_template_for(STOCK, str(repo), report={"code_workspace": False}) == STOCK
