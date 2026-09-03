from __future__ import annotations

from pathlib import Path

import pytest

from gt_engine import indexer
from gt_engine.indexer import (
    _INDEX_BUILD_ATTEMPTS,
    _INDEX_MAX_FILES,
    _INDEX_MAX_PROCS,
    IndexProcessResult,
    _build_index_with_attempts,
    _index_command,
    scrub_index_stderr,
)


def _command() -> list[str]:
    return _index_command("gt-index", "/repo", "/out/graph.db")


def test_walk_ceiling_is_stated_not_inherited():
    """gt-index defaults to 10000 files and truncates the walk silently."""

    command = _command()
    assert "-max-files" in command
    assert command[command.index("-max-files") + 1] == str(_INDEX_MAX_FILES)
    assert _INDEX_MAX_FILES > 10_000


def test_worker_count_matches_the_runtime_budget():
    """NumCPU workers oversubscribe a runtime capped at GOMAXPROCS."""

    command = _command()
    assert command[command.index("-workers") + 1] == str(_INDEX_MAX_PROCS)


def test_transitive_closure_is_requested_explicitly():
    assert "-closure=true" in _command()


def test_root_and_output_are_still_first():
    command = _command()
    assert command[:5] == ["gt-index", "-root", "/repo", "-output", "/out/graph.db"]


def test_scrub_keeps_the_diagnostic_and_drops_the_secret():
    scrubbed = scrub_index_stderr(
        b"OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456\n"
        b"open /repo/pkg/x.go: no such file or directory\n"
    )

    assert "no such file or directory" in scrubbed
    assert "/repo/pkg/x.go" in scrubbed
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in scrubbed


@pytest.mark.parametrize(
    "raw",
    [
        b"GT_TOKEN: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        b"password=hunter2hunter2hunter2hunter2hunter2",
        b"bare AKIAIOSFODNN7EXAMPLEDEADBEEFCAFE0123456789 run",
    ],
)
def test_scrub_removes_secret_shaped_runs(raw: bytes):
    assert "[redacted]" in scrub_index_stderr(raw)


def test_scrub_survives_invalid_utf8():
    assert scrub_index_stderr(b"\xff\xfe broken pipe") .endswith("broken pipe")


def _result(success: bool, status: str) -> IndexProcessResult:
    return IndexProcessResult(success=success, status=status, error_code="" if success else "GT_INDEX_PROCESS_FAILED")


def test_a_transient_failure_no_longer_costs_the_run_its_graph(monkeypatch, tmp_path: Path):
    calls: list[int] = []

    def fake(root, output, log_dir):
        calls.append(1)
        return _result(len(calls) == 2, "completed" if len(calls) == 2 else "nonzero_exit")

    monkeypatch.setattr(indexer, "_run_index_bounded", fake)
    result, attempts = _build_index_with_attempts("/repo", tmp_path / "g.db", tmp_path)

    assert result.success is True
    assert len(attempts) == 2
    assert attempts[0].startswith("1:nonzero_exit")
    assert attempts[1].startswith("2:completed")


def test_a_deterministic_failure_is_visible_as_repetition(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        indexer, "_run_index_bounded", lambda *a: _result(False, "nonzero_exit")
    )
    result, attempts = _build_index_with_attempts("/repo", tmp_path / "g.db", tmp_path)

    assert result.success is False
    assert len(attempts) == _INDEX_BUILD_ATTEMPTS


def test_success_does_not_retry(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        indexer, "_run_index_bounded", lambda *a: _result(True, "completed")
    )
    _result_, attempts = _build_index_with_attempts("/repo", tmp_path / "g.db", tmp_path)

    assert len(attempts) == 1


def test_a_partial_database_is_never_carried_between_attempts(monkeypatch, tmp_path: Path):
    output = tmp_path / "graph.db"
    seen: list[bool] = []

    def fake(root, out, log_dir):
        seen.append(Path(out).exists())
        Path(out).write_bytes(b"partial")
        return _result(len(seen) == 3, "completed" if len(seen) == 3 else "nonzero_exit")

    monkeypatch.setattr(indexer, "_run_index_bounded", fake)
    _build_index_with_attempts("/repo", output, tmp_path)

    # every attempt started from no database, including the ones after a failure
    assert seen == [False, False, False]
