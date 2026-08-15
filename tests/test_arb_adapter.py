from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from gt_engine.hybrid_repository import build_hybrid_repository
from gt_engine.hybrid_retrieval import RetrievalIntent
from gt_engine.indexer import IndexBuildReceipt, IndexBuildStatus
from scripts.arb_adapter import (
    RedactedSampleError,
    RetrievalProbe,
    _attach_source_chunks,
    _intent_for_task_type,
    load_redacted_samples,
    normalize_sample,
    run_probe,
)


def test_normalize_sample_allows_declared_given_files_only() -> None:
    row = normalize_sample(
        {
            "sample_id": "s1",
            "repository": "repo",
            "base_commit": "abc",
            "query": "Find the parser implementation.",
            "given_files": ["src/parser.py"],
            "task_type": "trace2code",
        }
    )
    assert row.sample_id == "s1"
    assert row.instruction == "Find the parser implementation."
    assert row.active_paths == ("src/parser.py",)
    assert row.task_type == "trace2code"


@pytest.mark.parametrize(
    ("task_type", "intent"),
    [
        ("code2test", RetrievalIntent.VALIDATION_CONTEXT),
        ("comment2context", RetrievalIntent.MISSING_CONTEXT),
        ("trace2code", RetrievalIntent.DIAGNOSTIC_ROOT_CAUSE),
        ("edit2ripple", RetrievalIntent.CHANGE_IMPACT),
        ("selective", RetrievalIntent.OTHER),
        ("abstention", RetrievalIntent.OTHER),
    ],
)
def test_task_type_maps_to_typed_production_intent(task_type, intent) -> None:
    assert _intent_for_task_type(task_type) is intent


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


def test_source_chunks_persist_ranked_span_and_text(tmp_path) -> None:
    source = tmp_path / "src.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    rows = _attach_source_chunks(
        (
            {
                "file_path": "src.py",
                "symbol": "fn",
                "line": 2,
            },
        ),
        repo_root=tmp_path,
        graph_db=None,
    )
    assert rows[0]["source_span"] == {"path": "src.py", "start_line": 2, "end_line": 2}
    assert rows[0]["source_text"] == "two"
    assert rows[0]["source_chunk"] == {
        "path": "src.py",
        "start_line": 2,
        "end_line": 2,
        "text": "two",
    }


def test_source_chunks_use_the_indexed_symbol_range(tmp_path) -> None:
    source = tmp_path / "src.py"
    source.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,file_path TEXT,name TEXT,"
            "start_line INTEGER,end_line INTEGER,signature TEXT)"
        )
        connection.execute("INSERT INTO nodes VALUES (1,'src.py','fn',2,3,'def fn()')")
        connection.commit()
    finally:
        connection.close()

    rows = _attach_source_chunks(
        ({"file_path": "src.py", "symbol": "fn", "line": 2},),
        repo_root=tmp_path,
        graph_db=str(graph),
    )

    assert rows[0]["source_span"] == {
        "path": "src.py",
        "start_line": 2,
        "end_line": 3,
    }
    assert rows[0]["source_text"] == "two\nthree"


def test_run_probe_uses_hybrid_result_and_persists_channel_receipts(
    tmp_path,
    monkeypatch,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "allocator.py").write_text(
        "def allocate():\n    return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_allocator.py").write_text(
        "def test_allocate():\n    assert allocate()\n",
        encoding="utf-8",
    )
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,name TEXT,file_path TEXT,"
            "start_line INTEGER,end_line INTEGER,signature TEXT,language TEXT,is_test BOOLEAN)"
        )
        connection.execute(
            "CREATE TABLE edges (id INTEGER PRIMARY KEY,source_id INTEGER,target_id INTEGER,"
            "type TEXT,confidence REAL,trust_tier TEXT)"
        )
        connection.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?)",
            (
                (
                    1,
                    "allocate",
                    "src/allocator.py",
                    1,
                    2,
                    "def allocate()",
                    "python",
                    0,
                ),
                (
                    2,
                    "test_allocate",
                    "tests/test_allocator.py",
                    1,
                    2,
                    "def test_allocate()",
                    "python",
                    1,
                ),
            ),
        )
        connection.execute("CREATE VIRTUAL TABLE nodes_fts USING fts5(name,file_path)")
        connection.executemany(
            "INSERT INTO nodes_fts(rowid,name,file_path) VALUES (?,?,?)",
            (
                (1, "allocate", "src/allocator.py"),
                (2, "test_allocate", "tests/test_allocator.py"),
            ),
        )
        connection.execute("INSERT INTO edges VALUES (1,1,2,'TESTED_BY',0.99,'CERTIFIED')")
        connection.commit()
    finally:
        connection.close()

    index = SimpleNamespace(
        graph_db=str(graph),
        elapsed_ms=1.25,
        error_type=None,
        error_diagnostic="",
        source_files=2,
        indexable_files=2,
        schema_valid=True,
        node_count=2,
        edge_count=1,
        binary_sha256="binary-hash",
    )
    monkeypatch.setattr(
        "scripts.arb_adapter.inspect_index",
        lambda *args, **kwargs: index,
    )
    probe = RetrievalProbe(
        sample_id="sample-1",
        repository="owner/repo",
        base_commit="abc",
        instruction="find the allocator regression test",
        task_type="code2test",
        active_paths=("src/allocator.py",),
        source_revision="source-1",
    )

    result = run_probe(probe, repo_root=tmp_path, state_dir=tmp_path / "state")

    assert result.retrieval_intent == RetrievalIntent.VALIDATION_CONTEXT.value
    assert result.ranked_candidates[0]["file_path"] == "tests/test_allocator.py"
    assert result.ranked_candidates[0]["source_text"].startswith("def test_allocate")
    assert result.ranked_candidates[0]["channel_ranks"]
    assert "structural_certified" in result.ranked_candidates[0]["provenance"]
    assert result.delivered_evidence[0]["file_path"] == "tests/test_allocator.py"
    assert result.abstained is False
    assert result.payload_chars > 0
    assert result.payload_tokens == result.selected_token_count
    assert len(result.channel_receipts) == 5
    assert result.repository_document_count == 2


def test_run_probe_records_the_exact_dense_backend_identity(tmp_path, monkeypatch):
    class DenseBackend:
        identity = "snowflake_onnx:model@sha256:abc"

        def embed_query(self, text):
            return (1.0, 0.0)

        def embed_documents(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

        def receipt(self):
            return {
                "backend": "snowflake_onnx",
                "model_name": "model",
                "model_sha256": "abc",
                "provider_calls": 0,
            }

    (tmp_path / "src.py").write_text("def target():\n    pass\n", encoding="utf-8")
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,name TEXT,file_path TEXT,"
            "start_line INTEGER,end_line INTEGER,signature TEXT)"
        )
        connection.execute("INSERT INTO nodes VALUES (1,'target','src.py',1,2,'def target()')")
        connection.commit()
    finally:
        connection.close()
    index = SimpleNamespace(
        graph_db=str(graph),
        elapsed_ms=1.0,
        error_type=None,
        error_diagnostic="",
        source_files=1,
        indexable_files=1,
        schema_valid=True,
        node_count=1,
        edge_count=0,
        binary_sha256="binary",
    )
    monkeypatch.setattr(
        "scripts.arb_adapter.inspect_index",
        lambda *args, **kwargs: index,
    )

    result = run_probe(
        RetrievalProbe(
            "s1",
            "owner/repo",
            "abc",
            "Find target in src.py",
            (),
            "source-1",
        ),
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        dense_backend=DenseBackend(),
    )

    dense_channel = next(row for row in result.channel_receipts if row["channel"] == "dense")
    assert dense_channel["backend_identity"] == "snowflake_onnx:model@sha256:abc"
    assert result.dense_backend_receipt == {
        "backend": "snowflake_onnx",
        "model_name": "model",
        "model_sha256": "abc",
        "provider_calls": 0,
    }


def test_cached_probe_does_not_run_the_discarded_repository_inspection(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / "src.py").write_text("def target():\n    pass\n", encoding="utf-8")
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,name TEXT,file_path TEXT,"
            "start_line INTEGER,end_line INTEGER,signature TEXT)"
        )
        connection.execute("INSERT INTO nodes VALUES (1,'target','src.py',1,2,'def target()')")
        connection.commit()
    finally:
        connection.close()
    index = IndexBuildReceipt(
        status=IndexBuildStatus.AVAILABLE,
        graph_db=str(graph),
        graph_revision="graph-1",
        source_revision="source-1",
        schema_valid=True,
        source_files=1,
        indexable_files=1,
        node_count=1,
    )
    repository = build_hybrid_repository(
        tmp_path,
        graph,
        source_revision="source-1",
    )
    monkeypatch.setattr(
        "scripts.arb_adapter.inspect_index",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("cached probe rebuilt the index")
        ),
    )

    result = run_probe(
        RetrievalProbe(
            "s1",
            "owner/repo",
            "abc",
            "Find target in src.py",
            (),
            "source-1",
        ),
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        index_receipt=index,
        prepared_repository=repository,
    )

    assert result.graph_status == "source_backed"
    assert result.graph_revision == "graph-1"
    assert result.index_cache_hit is True
    assert result.phase_latency_ms
    assert abs(result.query_latency_ms - sum(result.phase_latency_ms.values())) <= max(
        5.0, result.query_latency_ms * 0.01
    )
