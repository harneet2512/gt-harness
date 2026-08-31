from __future__ import annotations

import sqlite3

from gt_engine.hybrid_retrieval import (
    EmbeddingRecord,
    HybridQuery,
    SQLiteVectorIndex,
)


def _record(document_id: str, vector: tuple[float, ...], text: str) -> EmbeddingRecord:
    return EmbeddingRecord(
        document_id=document_id,
        text=text,
        embedding=vector,
        content_hash=f"content-{document_id}",
        model_id="model-v1",
        tokenizer_id="tokenizer-v1",
        source_revision="source-r1",
        graph_revision="graph-r1",
    )


def test_optional_vec0_falls_back_and_exact_rescore_owns_order(tmp_path) -> None:
    index = SQLiteVectorIndex(
        tmp_path / "vectors.sqlite",
        model_id="model-v1",
        tokenizer_id="tokenizer-v1",
        dimension=3,
        source_revision="source-r1",
        graph_revision="graph-r1",
        extension_loader=lambda _connection: False,
    )
    index.upsert(
        [
            _record("owner", (1.0, 0.0, 0.0), "owner implementation"),
            _record("dependency", (0.99, 0.01, 0.0), "dependency implementation"),
            _record("test", (0.98, 0.02, 0.0), "test coverage"),
        ]
    )

    result = index.query(
        HybridQuery(
            vector=(1.0, 0.0, 0.0),
            lexical_scores={"dependency": 1.0, "owner": 0.1},
            graph_scores={"owner": 1.0},
            limit=2,
        )
    )

    assert result.fallback_reason == "vec0_unavailable"
    assert [item.document_id for item in result.items] == ["owner", "dependency"]
    assert result.items[0].exact_score > result.items[1].exact_score
    assert result.candidate_ids == ("dependency", "owner", "test")


def test_sqlite_vec_import_failure_is_a_typed_fallback(tmp_path) -> None:
    def missing_extension(_connection: sqlite3.Connection) -> bool:
        raise ImportError("sqlite_vec is not installed")

    index = SQLiteVectorIndex(
        tmp_path / "vectors.sqlite",
        model_id="model-v1",
        tokenizer_id="tokenizer-v1",
        dimension=2,
        source_revision="source-r1",
        graph_revision="graph-r1",
        extension_loader=missing_extension,
    )
    index.upsert([_record("owner", (1.0, 0.0), "owner")])

    result = index.query(HybridQuery(vector=(1.0, 0.0), limit=1))

    assert result.fallback_reason == "vec0_load_failed"
    assert result.items[0].document_id == "owner"


def test_metadata_mismatch_is_named_and_does_not_use_stale_rows(tmp_path) -> None:
    path = tmp_path / "vectors.sqlite"
    first = SQLiteVectorIndex(
        path,
        model_id="model-v1",
        tokenizer_id="tokenizer-v1",
        dimension=2,
        source_revision="source-r1",
        graph_revision="graph-r1",
        extension_loader=lambda _connection: False,
    )
    first.upsert([_record("owner", (1.0, 0.0), "owner")])

    reopened = SQLiteVectorIndex(
        path,
        model_id="model-v2",
        tokenizer_id="tokenizer-v1",
        dimension=2,
        source_revision="source-r1",
        graph_revision="graph-r1",
        extension_loader=lambda _connection: False,
    )
    result = reopened.query(HybridQuery(vector=(1.0, 0.0), limit=1))

    assert result.items == ()
    assert result.fallback_reason == "metadata_mismatch"


def test_update_and_delete_are_atomic_and_restart_reuses_identity(tmp_path) -> None:
    path = tmp_path / "vectors.sqlite"
    index = SQLiteVectorIndex(
        path,
        model_id="model-v1",
        tokenizer_id="tokenizer-v1",
        dimension=2,
        source_revision="source-r1",
        graph_revision="graph-r1",
        extension_loader=lambda _connection: False,
    )
    index.upsert([_record("old", (1.0, 0.0), "old")])
    index.upsert([_record("new", (0.0, 1.0), "new")], delete_ids=("old",))
    index.close()

    reopened = SQLiteVectorIndex(
        path,
        model_id="model-v1",
        tokenizer_id="tokenizer-v1",
        dimension=2,
        source_revision="source-r1",
        graph_revision="graph-r1",
        extension_loader=lambda _connection: False,
    )
    result = reopened.query(HybridQuery(vector=(0.0, 1.0), limit=5))

    assert result.candidate_ids == ("new",)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM gt_vector_documents").fetchone()[0] == 1


def test_reported_extension_must_create_real_vec0_table_before_use(tmp_path) -> None:
    index = SQLiteVectorIndex(
        tmp_path / "vectors.sqlite",
        model_id="model-v1",
        tokenizer_id="tokenizer-v1",
        dimension=2,
        source_revision="source-r1",
        graph_revision="graph-r1",
        extension_loader=lambda _connection: True,
    )
    index.upsert([_record("owner", (1.0, 0.0), "owner")])
    result = index.query(HybridQuery(vector=(1.0, 0.0), limit=1))

    with sqlite3.connect(tmp_path / "vectors.sqlite") as connection:
        virtual_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'gt_vector_vec0'"
        ).fetchone()
    if result.fallback_reason is None:
        assert virtual_table == ("gt_vector_vec0",)
        assert result.items[0].document_id == "owner"
    else:
        assert result.fallback_reason in {"vec0_unavailable", "vec0_query_failed"}


def _load_sqlite_vec(connection: sqlite3.Connection) -> bool:
    import sqlite_vec

    connection.enable_load_extension(True)
    sqlite_vec.load(connection)
    return True


def test_accelerated_mode_unions_lexical_graph_candidates_with_ann_pool(tmp_path) -> None:
    index = SQLiteVectorIndex(
        tmp_path / "vectors.sqlite",
        model_id="model-v1",
        tokenizer_id="tokenizer-v1",
        dimension=3,
        source_revision="source-r1",
        graph_revision="graph-r1",
        extension_loader=_load_sqlite_vec,
    )
    index.upsert(
        [
            _record("lex_only", (0.0, 1.0, 0.0), "lexical-only row"),
            _record("ann_hit", (1.0, 0.0, 0.0), "vector-nearest row"),
            _record("orphan", (0.0, 0.0, 1.0), "outside both channels"),
        ]
    )

    result = index.query(
        HybridQuery(
            vector=(1.0, 0.0, 0.0),
            lexical_scores={"lex_only": 1.0},
            graph_scores={},
            limit=5,
            candidate_pool=1,
        )
    )

    assert result.fallback_reason is None
    assert "ann_hit" in result.candidate_ids
    assert "lex_only" in result.candidate_ids
    assert "orphan" not in result.candidate_ids
