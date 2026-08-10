from __future__ import annotations

import json

import pytest

from scripts.arb_adapter import (
    RedactedSampleError,
    load_redacted_samples,
    normalize_sample,
)


def test_normalize_sample_allows_declared_given_files_only() -> None:
    row = normalize_sample(
        {
            "sample_id": "s1",
            "repository": "repo",
            "base_commit": "abc",
            "query": "Find the parser implementation.",
            "given_files": ["src/parser.py"],
        }
    )
    assert row.sample_id == "s1"
    assert row.instruction == "Find the parser implementation."
    assert row.active_paths == ("src/parser.py",)


def test_normalize_sample_rejects_gold_or_fix_fields() -> None:
    with pytest.raises(RedactedSampleError, match="gold/fix leakage"):
        normalize_sample(
            {
                "sample_id": "s1",
                "repository": "repo",
                "base_commit": "abc",
                "query": "Find it.",
                "gold": {"files": ["src/parser.py"]},
            }
        )


def test_load_redacted_samples_is_deterministic(tmp_path) -> None:
    path = tmp_path / "samples.jsonl"
    path.write_text(
        json.dumps(
            {
                "sample_id": "s1",
                "repository": "repo",
                "base_commit": "abc",
                "instruction": "Locate parser.",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    rows = load_redacted_samples(path)
    assert [row.sample_id for row in rows] == ["s1"]
