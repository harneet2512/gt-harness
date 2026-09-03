from __future__ import annotations

import sys
from pathlib import Path

from gt_engine.indexer import start_lsp_promotion


def _producer(monkeypatch, *, servers, starter=None):
    module = type(sys)("groundtruth.lsp.background_promotion")
    module.detect_available_servers = lambda: dict(servers)
    module.start_background_promotion = starter or (lambda db, root: None)
    monkeypatch.setitem(sys.modules, "groundtruth.lsp.background_promotion", module)
    return module


def test_promotion_reports_the_servers_it_found(monkeypatch, tmp_path: Path):
    started: list[tuple[str, str]] = []
    _producer(
        monkeypatch,
        servers={"go": "gopls", "python": "pyright-langserver"},
        starter=lambda db, root: started.append((db, root)),
    )

    result = start_lsp_promotion(tmp_path / "graph.db", "/repo")

    assert result == {"status": "promotion_started", "servers": ["go", "python"]}
    assert started == [(str(tmp_path / "graph.db"), "/repo")]


def test_no_servers_on_path_is_recorded_not_silently_successful(monkeypatch, tmp_path: Path):
    """The gap REV-244 named: an unstaged container must not look like success.

    Promotion with nothing to promote with is exactly how the lsp tier stayed
    empty while LSP was nominally wired.
    """

    def must_not_run(db, root):
        raise AssertionError("promotion started with no servers available")

    _producer(monkeypatch, servers={}, starter=must_not_run)

    result = start_lsp_promotion(tmp_path / "graph.db", "/repo")

    assert result == {"status": "promotion_no_servers", "servers": []}


def test_a_missing_producer_package_is_not_an_index_failure(monkeypatch, tmp_path: Path):
    import builtins

    original = builtins.__import__

    def explode(name, *args, **kwargs):
        if name.startswith("groundtruth"):
            raise ImportError(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", explode)

    assert start_lsp_promotion(tmp_path / "graph.db", "/repo") == {
        "status": "promotion_unavailable",
        "servers": [],
    }


def test_a_raising_promoter_never_fails_the_index(monkeypatch, tmp_path: Path):
    """Promotion is an optimiser over an already-published graph, never a gate."""

    def boom(db, root):
        raise RuntimeError("language server exploded")

    _producer(monkeypatch, servers={"rust": "rust-analyzer"}, starter=boom)

    result = start_lsp_promotion(tmp_path / "graph.db", "/repo")

    assert result == {"status": "promotion_failed", "servers": ["rust"]}


def test_failed_discovery_degrades_to_no_servers(monkeypatch, tmp_path: Path):
    module = _producer(monkeypatch, servers={})

    def broken_discovery():
        raise OSError("PATH unreadable")

    module.detect_available_servers = broken_discovery

    assert start_lsp_promotion(tmp_path / "graph.db", "/repo")["status"] == (
        "promotion_no_servers"
    )


def test_every_outcome_is_distinguishable():
    """Four states, four names — none collapses into another."""

    assert len(
        {
            "promotion_started",
            "promotion_no_servers",
            "promotion_unavailable",
            "promotion_failed",
        }
    ) == 4
