from __future__ import annotations

from pathlib import Path

import pytest

from gt_engine import indexer
from gt_engine.indexer import BenchmarkGraphRequired, ensure_index


@pytest.fixture
def benchmark_run(monkeypatch):
    monkeypatch.setenv("GT_TASK_ID", "arktype-json-schema-refs-dependencies")
    monkeypatch.setenv("GT_PRODUCT_SOURCE_SHA", "2" * 40)


@pytest.fixture
def local_run(monkeypatch):
    monkeypatch.delenv("GT_TASK_ID", raising=False)
    monkeypatch.delenv("GT_PRODUCT_SOURCE_SHA", raising=False)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    return tmp_path


def test_a_benchmark_run_refuses_to_proceed_without_its_graph(
    benchmark_run, monkeypatch, tmp_path: Path
):
    """The product is the graph: a run without one measures nothing, at full cost."""

    monkeypatch.setattr(indexer, "is_code_repo", lambda root: True)
    monkeypatch.setattr(indexer, "_ensure_index_unlocked", lambda root, state_dir=None: None)

    with pytest.raises(BenchmarkGraphRequired):
        ensure_index(str(_repo(tmp_path)))


def test_an_index_that_raises_is_still_a_refusal_not_a_silent_none(
    benchmark_run, monkeypatch, tmp_path: Path
):
    def explode(root, state_dir=None):
        raise RuntimeError("gt-index exited 1")

    monkeypatch.setattr(indexer, "is_code_repo", lambda root: True)
    monkeypatch.setattr(indexer, "_ensure_index_unlocked", explode)

    with pytest.raises(BenchmarkGraphRequired):
        ensure_index(str(_repo(tmp_path)))


def test_a_benchmark_run_with_a_graph_proceeds(benchmark_run, monkeypatch, tmp_path: Path):
    monkeypatch.setattr(indexer, "is_code_repo", lambda root: True)
    monkeypatch.setattr(
        indexer, "_ensure_index_unlocked", lambda root, state_dir=None: "/g/graph.db"
    )

    assert ensure_index(str(_repo(tmp_path))) == "/g/graph.db"


def test_a_task_starting_with_no_source_is_not_a_failure(
    benchmark_run, monkeypatch, tmp_path: Path
):
    """Nothing to index yet — the graph fills as the agent creates files."""

    monkeypatch.setattr(indexer, "is_code_repo", lambda root: False)

    assert ensure_index(str(tmp_path)) is None


def test_local_work_keeps_its_degraded_mode(local_run, monkeypatch, tmp_path: Path):
    """Outside a benchmark a missing graph is deliberate, not a defect."""

    monkeypatch.setattr(indexer, "is_code_repo", lambda root: True)
    monkeypatch.setattr(indexer, "_ensure_index_unlocked", lambda root, state_dir=None: None)

    assert ensure_index(str(_repo(tmp_path))) is None


def test_an_incomplete_benchmark_identity_does_not_trigger_the_refusal(
    monkeypatch, tmp_path: Path
):
    """benchmark_invalid is its own diagnosed condition, not this one."""

    monkeypatch.setenv("GT_TASK_ID", "some-task")
    monkeypatch.delenv("GT_PRODUCT_SOURCE_SHA", raising=False)
    monkeypatch.setattr(indexer, "is_code_repo", lambda root: True)
    monkeypatch.setattr(indexer, "_ensure_index_unlocked", lambda root, state_dir=None: None)

    assert ensure_index(str(_repo(tmp_path))) is None
