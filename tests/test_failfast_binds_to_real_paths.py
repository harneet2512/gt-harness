from __future__ import annotations

import ast
import io
from pathlib import Path

import pytest

import gt_engine
from gt_engine import indexer
from gt_engine.indexer import BenchmarkGraphRequired

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def benchmark_run(monkeypatch):
    monkeypatch.setenv("GT_TASK_ID", "arktype-json-schema-refs-dependencies")
    monkeypatch.setenv("GT_PRODUCT_SOURCE_SHA", "2" * 40)


def test_create_bridge_propagates_instead_of_going_dormant(
    benchmark_run, monkeypatch, tmp_path: Path
):
    """The gap REV-245 found: a broad handler swallowed the refusal.

    `create_bridge` promises GT never breaks the harness, which is right for
    local work. On a benchmark run it turned a refusal back into a dormant
    bridge and the run continued to provider calls.
    """

    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    monkeypatch.setattr(indexer, "is_code_repo", lambda root: True)
    monkeypatch.setattr(indexer, "_ensure_index_unlocked", lambda root, state_dir=None: None)

    with pytest.raises(BenchmarkGraphRequired):
        gt_engine.create_bridge(str(tmp_path))


def test_create_bridge_still_goes_dormant_for_local_work(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("GT_TASK_ID", raising=False)
    monkeypatch.delenv("GT_PRODUCT_SOURCE_SHA", raising=False)
    monkeypatch.setattr(indexer, "is_code_repo", lambda root: True)
    monkeypatch.setattr(indexer, "_ensure_index_unlocked", lambda root, state_dir=None: None)

    # A dormant bridge, not an exception: unchanged behaviour outside a benchmark.
    assert gt_engine.create_bridge(str(tmp_path)) is not None


def _handlers_guarding(call: str, source: str) -> list[str]:
    """Names of exceptions handled by the try block wrapping `call`."""

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if call not in ast.unparse(node.body):
            continue
        names = []
        for handler in node.handlers:
            names.append("" if handler.type is None else ast.unparse(handler.type))
        return names
    return []


def test_the_benchmark_runner_lets_the_refusal_through():
    """`scripts/miniswe_gt_run.py` is the path a paid run actually takes.

    Structural rather than behavioural: constructing the real agent needs the
    full task environment. This asserts the ordering that matters — the refusal
    is handled before the broad `Exception` that records and continues.
    """

    source = io.open(REPO / "scripts" / "miniswe_gt_run.py", encoding="utf-8").read()
    handlers = _handlers_guarding("ensure_index(cwd", source)

    assert "BenchmarkGraphRequired" in handlers
    assert handlers.index("BenchmarkGraphRequired") < handlers.index("Exception")


def test_the_bridge_path_lets_the_refusal_through():
    source = io.open(REPO / "gt_engine" / "__init__.py", encoding="utf-8").read()
    handlers = _handlers_guarding("ensure_index(gt_root)", source)

    assert "_must_propagate" in handlers
    assert handlers.index("_must_propagate") < handlers.index("Exception")
