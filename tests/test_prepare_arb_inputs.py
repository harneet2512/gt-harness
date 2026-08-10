from __future__ import annotations

from scripts.prepare_arb_redacted_inputs import project_row


def test_code2test_projection_keeps_anchor_but_not_gold_fields() -> None:
    row = project_row(
        {
            "id": "c2t-1",
            "repo": "owner/repo",
            "base_commit": "abc",
            "task_type": "code2test",
            "query": {
                "pr_title": "Fix parser",
                "pr_body": "Add regression coverage.",
                "changed_file": "src/parser.py",
                "implementation_files": ["src/parser.py"],
            },
            "gold": {"root_cause_files": ["tests/test_parser.py"]},
        }
    )
    assert set(row) == {
        "sample_id",
        "repository",
        "base_commit",
        "task_type",
        "instruction",
        "active_paths",
    }
    assert row["active_paths"] == ["src/parser.py"]
    assert "tests/test_parser.py" not in row["instruction"]


def test_comment_projection_exposes_only_given_file_as_active_context() -> None:
    row = project_row(
        {
            "id": "c2c-1",
            "repo": "owner/repo",
            "base_commit": "abc",
            "task_type": "comment2context",
            "query": {
                "pr_title": "Review",
                "review_comment": "Please preserve the invariant.",
                "diff_hunk_context": "assert value",
                "given_file": "tests/test_parser.py",
            },
            "gold": {"must_context_files": [{"path": "src/parser.py"}]},
        }
    )
    assert row["active_paths"] == ["tests/test_parser.py"]
    assert "src/parser.py" not in row["instruction"]


def test_trace_projection_does_not_invent_an_active_path() -> None:
    row = project_row(
        {
            "id": "t2c-1",
            "repo": "owner/repo",
            "base_commit": "abc",
            "task_type": "trace2code",
            "query": {
                "command": "pytest tests/test_parser.py",
                "failure_excerpt": "AssertionError: bad value",
                "run_strategy": "focused",
                "source_type": "test_failure",
            },
            "gold": {"root_cause_files": ["src/parser.py"]},
        }
    )
    assert row["active_paths"] == []
