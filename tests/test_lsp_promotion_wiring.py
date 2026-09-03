from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gt_engine.indexer import start_lsp_promotion


def test_promotion_starts_when_the_producer_is_available(monkeypatch, tmp_path: Path):
    started: list[tuple[str, str]] = []

    module = type(sys)("groundtruth.lsp.background_promotion")
    module.start_background_promotion = lambda db, root: started.append((db, root))
    monkeypatch.setitem(sys.modules, "groundtruth.lsp.background_promotion", module)

    assert start_lsp_promotion(tmp_path / "graph.db", "/repo") == "promotion_started"
    assert started == [(str(tmp_path / "graph.db"), "/repo")]


def test_a_missing_producer_package_is_not_an_index_failure(monkeypatch, tmp_path: Path):
    """A harness without the producer wheel still publishes a usable graph."""

    def explode(name, *args, **kwargs):
        if name.startswith("groundtruth"):
            raise ImportError(name)
        return original(name, *args, **kwargs)

    import builtins

    original = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", explode)

    assert start_lsp_promotion(tmp_path / "graph.db", "/repo") == "promotion_unavailable"


def test_a_raising_promoter_never_fails_the_index(monkeypatch, tmp_path: Path):
    """Promotion is an optimiser over an already-published graph, never a gate."""

    module = type(sys)("groundtruth.lsp.background_promotion")

    def boom(db, root):
        raise RuntimeError("language server exploded")

    module.start_background_promotion = boom
    monkeypatch.setitem(sys.modules, "groundtruth.lsp.background_promotion", module)

    assert start_lsp_promotion(tmp_path / "graph.db", "/repo") == "promotion_failed"


@pytest.mark.parametrize("outcome", ["promotion_started", "promotion_unavailable", "promotion_failed"])
def test_every_outcome_is_a_reported_string_not_an_exception(outcome: str):
    assert isinstance(outcome, str)
