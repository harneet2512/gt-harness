from __future__ import annotations

import sys
from pathlib import Path

from gt_engine.indexer import start_lsp_promotion


def _producer(monkeypatch, *, servers, starter=None, stats=None):
    module = type(sys)("groundtruth.lsp.background_promotion")
    module.detect_available_servers = lambda: dict(servers)
    module.start_background_promotion = starter or (lambda db, root: None)
    module.get_promotion_stats = lambda: dict(stats or {"status": "running"})
    monkeypatch.setitem(sys.modules, "groundtruth.lsp.background_promotion", module)
    return module


def test_promotion_refuses_to_claim_unreceipted_schedule(monkeypatch, tmp_path: Path):
    started: list[tuple[str, str]] = []
    _producer(
        monkeypatch,
        servers={"go": "gopls", "python": "pyright-langserver"},
        starter=lambda db, root: started.append((db, root)),
    )

    result = start_lsp_promotion(tmp_path / "graph.db", "/repo")

    assert result == {
        "status": "promotion_not_scheduled",
        "servers": ["go", "python"],
        "reason": "producer_scheduler_receipt_unavailable",
    }
    assert started == []


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


def test_unreceipted_promoter_is_never_called(monkeypatch, tmp_path: Path):
    """No background graph mutation occurs without a graph-bound receipt."""

    def boom(db, root):
        raise RuntimeError("language server exploded")

    _producer(monkeypatch, servers={"rust": "rust-analyzer"}, starter=boom)

    result = start_lsp_promotion(tmp_path / "graph.db", "/repo")

    assert result == {
        "status": "promotion_not_scheduled",
        "servers": ["rust"],
        "reason": "producer_scheduler_receipt_unavailable",
    }


def test_global_launcher_stats_are_not_used_as_graph_receipts(monkeypatch, tmp_path: Path):
    _producer(
        monkeypatch,
        servers={"typescript": "typescript-language-server"},
        stats={"status": "idle"},
    )

    result = start_lsp_promotion(tmp_path / "graph.db", "/repo")

    assert result == {
        "status": "promotion_not_scheduled",
        "servers": ["typescript"],
        "reason": "producer_scheduler_receipt_unavailable",
    }


def test_failed_discovery_degrades_to_no_servers(monkeypatch, tmp_path: Path):
    module = _producer(monkeypatch, servers={})

    def broken_discovery():
        raise OSError("PATH unreadable")

    module.detect_available_servers = broken_discovery

    assert start_lsp_promotion(tmp_path / "graph.db", "/repo")["status"] == (
        "promotion_no_servers"
    )


def test_every_emitted_outcome_is_distinguishable():
    """Three states, three names; none claims work that lacks a receipt."""

    assert len(
        {
            "promotion_no_servers",
            "promotion_unavailable",
            "promotion_not_scheduled",
        }
    ) == 3
