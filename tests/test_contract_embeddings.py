"""Tests for gt_engine.contract_embeddings.

The fixture is built in *variants* of the same repository rather than as one
static graph, because the whole claim under test is a claim about change: what
happens to the vector store when a file is reformatted, when a guard moves, and
when a return shape moves.  A single-snapshot fixture cannot express any of it.

Variant meanings:

``base``
    The reference commit.
``reformat``
    Every symbol and every fact shifted ten lines down.  Not one stored value
    differs.  This is what a prettier run looks like to the graph.
``guard_change``
    One symbol gains a branch: its ``guard_clause`` value changes and the
    producer's ``fingerprint`` (``complexity:N|calls:...``) changes with it.
``return_change``
    One symbol's ``return_shape`` changes while its ``fingerprint`` does not --
    the producer's fingerprint counts branches and calls, so a changed return
    shape is invisible to it.  The plan requires this vector to change anyway.
``removed``
    One symbol deleted.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import struct
from pathlib import Path

import pytest

from gt_engine import contract, contract_embeddings, dense_runtime, retrieval

# (id, label, name, qualified_name, file_path, language, start_line, end_line,
#  signature, return_type)
_NODES = [
    (1, "File", "clone.ts", "ark/util/clone.ts", "ark/util/clone.ts",
     "typescript", 1, 40, "", None),
    (2, "Function", "deepClone", "deepClone", "ark/util/clone.ts",
     "typescript", 9, 11, "<input extends object>(input: input): input", "input"),
    (3, "Function", "_clone", "_clone", "ark/util/clone.ts",
     "typescript", 12, 30, "(input: unknown): unknown", "unknown"),
    (4, "Function", "parseRegex", "parseRegex", "ark/regex/parse.ts",
     "typescript", 4, 60, "(pattern: string): RegexNode", "RegexNode"),
    (5, "Class", "Scope", "InternalScope", "ark/type/scope.ts",
     "typescript", 202, 400, "", None),
]

# (id, node_id, kind, value, line, confidence)
_PROPERTIES = [
    (1, 2, "param", "input:: object [required]", 9, 1.0),
    (2, 2, "return_shape", "value|input", 10, 0.9),
    (3, 2, "fingerprint", "complexity:1|calls:_clone", 9, 0.9),
    (4, 3, "guard_clause", "return: (input === null) -> return", 13, 1.0),
    (5, 3, "boundary_condition", "null_check|input === null", 13, 0.9),
    (6, 3, "fingerprint", "complexity:4|calls:isEmpty", 12, 0.9),
    (7, 4, "return_shape", "value|compileRegexPattern(pattern)", 40, 0.8),
    (8, 4, "fingerprint", "complexity:2|calls:compileRegexPattern", 4, 0.9),
    (9, 5, "visibility", "public", 202, 1.0),
    # A real fact that is not a contract field: it must not reach the text.
    (10, 5, "caller_usage", "InternalScope() at ark/type/scope.ts:410", 202, 0.7),
]

_LINE_SHIFT = 10

_SCHEMA = """
CREATE TABLE nodes (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT,
    file_path TEXT NOT NULL,
    signature TEXT,
    language TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    return_type TEXT,
    stable_id TEXT,
    source_revision TEXT
);
CREATE TABLE properties (
    id INTEGER PRIMARY KEY,
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    line INTEGER,
    confidence REAL DEFAULT 1.0
);
CREATE TABLE resolution_symbols (
    stable_id TEXT NOT NULL,
    native_id TEXT NOT NULL,
    native_kind TEXT,
    normalized_kind TEXT,
    language TEXT,
    path TEXT,
    qualified_name TEXT,
    start_line INTEGER,
    end_line INTEGER,
    export_status TEXT
);
CREATE VIRTUAL TABLE nodes_fts USING fts5(
    name, qualified_name, signature, file_path,
    content='nodes', content_rowid='id'
);
"""


def _variant_rows(variant: str) -> tuple[list, list]:
    nodes = [list(row) for row in _NODES]
    properties = [list(row) for row in _PROPERTIES]
    if variant == "reformat":
        for row in nodes:
            row[6] += _LINE_SHIFT
            row[7] += _LINE_SHIFT
        for row in properties:
            row[4] += _LINE_SHIFT
    elif variant == "guard_change":
        for row in properties:
            if row[0] == 4:
                row[3] = "return: (input === null || isEmpty(input)) -> return"
            if row[0] == 6:
                row[3] = "complexity:5|calls:isEmpty"
    elif variant == "return_change":
        for row in properties:
            if row[0] == 7:
                row[3] = "value|compileRegexPattern(pattern) | undefined"
    elif variant == "removed":
        nodes = [row for row in nodes if row[0] != 5]
        properties = [row for row in properties if row[1] != 5]
    elif variant != "base":
        raise AssertionError(f"unknown variant {variant}")
    return nodes, properties


def build_graph(path: Path, variant: str = "base", revision: str = "rev-1") -> Path:
    """Write one variant of the fixture repository to ``path``."""
    nodes, properties = _variant_rows(variant)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(_SCHEMA)
        connection.executemany(
            "INSERT INTO nodes (id,label,name,qualified_name,file_path,language,"
            "start_line,end_line,signature,return_type,stable_id,source_revision) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?)",
            [
                (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7],
                 row[8], row[9], revision)
                for row in nodes
            ],
        )
        connection.executemany(
            "INSERT INTO properties (id,node_id,kind,value,line,confidence) "
            "VALUES (?,?,?,?,?,?)",
            [tuple(row) for row in properties],
        )
        # The producer mints stable ids here; nodes.stable_id stays NULL, as it
        # is on every real graph.  Line columns move with the reformat, which is
        # why the minted id -- not a line-bearing derivation -- is the key.
        connection.executemany(
            "INSERT INTO resolution_symbols (stable_id,native_id,native_kind,"
            "normalized_kind,language,path,qualified_name,start_line,end_line,"
            "export_status) VALUES (?,?,?,?,?,?,?,?,?,'exported')",
            [
                (f"sym-{row[0]}", str(row[0]), row[1], row[1].lower(), row[5],
                 row[4], row[3], row[6], row[7])
                for row in nodes
            ],
        )
        connection.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")
        connection.commit()
    finally:
        connection.close()
    return path


@pytest.fixture()
def base_graph(tmp_path: Path) -> Path:
    return build_graph(tmp_path / "base.db", "base")


class RecordingEmbedder:
    """A deterministic stand-in for the ONNX forward pass that counts work.

    Every assertion about invalidation is an assertion about how many texts
    reached this object, so the count is the measurement, not a side effect.
    """

    dimension = 8

    def __init__(self) -> None:
        self.batches: list[tuple[str, ...]] = []

    def __call__(self, texts):
        batch = tuple(texts)
        self.batches.append(batch)
        return [self.vector(text) for text in batch]

    @property
    def embedded(self) -> int:
        return sum(len(batch) for batch in self.batches)

    @property
    def texts(self) -> tuple[str, ...]:
        return tuple(text for batch in self.batches for text in batch)

    @classmethod
    def vector(cls, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = struct.unpack("<8H", digest[:16])
        values = [float(value) - 32767.5 for value in raw]
        norm = sum(value * value for value in values) ** 0.5
        return tuple(value / norm for value in values)


def retrieval_identity(node_id: int) -> str:
    """The stable id *retrieval* mints for a fixture node.

    Not the same string as the producer-minted id the store is keyed by, and
    deliberately so: retrieval fuses three sources in one identity space, and
    the store is keyed in the durable one.  The two are joined on the node id of
    the graph in hand, which is exactly what this test asserts.
    """
    row = next(item for item in _NODES if item[0] == node_id)
    from gt_engine.resolution_provenance import stable_symbol_id

    return stable_symbol_id(
        language=row[5],
        path=row[4],
        qualified_name=row[3],
        native_kind=row[1],
        start_line=row[6],
        end_line=row[7],
    )


def _store(path: Path) -> contract_embeddings.ContractEmbeddingStore:
    return contract_embeddings.ContractEmbeddingStore(
        path, model_id="test-embedder", tokenizer_id="test-tokenizer", dimension=8
    )


# ---------------------------------------------------------------------------
# 1. the embedded text
# ---------------------------------------------------------------------------


def test_contract_text_is_rendered_from_the_contract_not_the_source(
    base_graph: Path,
) -> None:
    by_id = {c["symbol"]["stable_id"]: c for c in contract.contracts(base_graph)}
    text = contract_embeddings.contract_text(by_id["sym-3"])

    assert "Function _clone" in text
    assert "guard" in text
    assert "input === null" in text
    assert "boundary" in text
    # caller_usage belongs to node 5 and is not a contract field either way.
    assert "caller_usage" not in text


def test_contract_text_carries_no_line_number(base_graph: Path) -> None:
    for symbol_contract in contract.contracts(base_graph):
        text = contract_embeddings.contract_text(symbol_contract)
        assert "start_line" not in text
        assert "line:" not in text


def test_contract_text_is_identical_after_a_reformat(tmp_path: Path) -> None:
    base = build_graph(tmp_path / "a.db", "base", revision="rev-1")
    shifted = build_graph(tmp_path / "b.db", "reformat", revision="rev-2")

    def texts(graph: Path) -> dict[str, str]:
        return {
            item["symbol"]["stable_id"]: contract_embeddings.contract_text(item)
            for item in contract.contracts(graph)
        }

    assert texts(base) == texts(shifted)


def test_contract_digest_moves_with_lines_but_the_embedded_text_does_not(
    tmp_path: Path,
) -> None:
    """Why a separate rendering exists rather than hashing the contract itself."""
    base = build_graph(tmp_path / "a.db", "base")
    shifted = build_graph(tmp_path / "b.db", "reformat")

    def digest(graph: Path) -> str:
        item = next(
            c for c in contract.contracts(graph) if c["symbol"]["stable_id"] == "sym-3"
        )
        return contract.contract_digest(item)

    assert digest(base) != digest(shifted)


# ---------------------------------------------------------------------------
# 2. the invalidation key
# ---------------------------------------------------------------------------


def test_fingerprints_are_read_from_the_property_row_not_the_bytes(
    base_graph: Path,
) -> None:
    values = contract_embeddings.fingerprints(base_graph)

    assert values[2] == "complexity:1|calls:_clone"
    assert values[3] == "complexity:4|calls:isEmpty"
    assert 5 not in values  # no fingerprint row: absence stays visible


def test_embedding_inputs_bind_stable_id_fingerprint_and_line_range(
    base_graph: Path,
) -> None:
    inputs = {
        item.stable_id: item
        for item in contract_embeddings.embedding_inputs(base_graph)
    }

    item = inputs["sym-3"]
    assert item.node_id == 3
    assert item.fingerprint == "complexity:4|calls:isEmpty"
    assert (item.start_line, item.end_line) == (12, 30)
    assert item.file_path == "ark/util/clone.ts"
    assert item.invalidation_key


def test_invalidation_key_is_stable_under_a_reformat(tmp_path: Path) -> None:
    base = build_graph(tmp_path / "a.db", "base")
    shifted = build_graph(tmp_path / "b.db", "reformat")

    def keys(graph: Path) -> dict[str, str]:
        return {
            item.stable_id: item.invalidation_key
            for item in contract_embeddings.embedding_inputs(graph)
        }

    assert keys(base) == keys(shifted)


def test_invalidation_key_moves_when_the_fingerprint_moves(tmp_path: Path) -> None:
    base = build_graph(tmp_path / "a.db", "base")
    changed = build_graph(tmp_path / "b.db", "guard_change")

    def key(graph: Path, stable_id: str) -> str:
        return next(
            item.invalidation_key
            for item in contract_embeddings.embedding_inputs(graph)
            if item.stable_id == stable_id
        )

    assert key(base, "sym-3") != key(changed, "sym-3")
    assert key(base, "sym-2") == key(changed, "sym-2")


# ---------------------------------------------------------------------------
# 3. the invalidation plan -- the acceptance criteria
# ---------------------------------------------------------------------------


def test_a_fresh_store_embeds_every_symbol(base_graph: Path, tmp_path: Path) -> None:
    store = _store(tmp_path / "vectors.sqlite")
    embedder = RecordingEmbedder()

    receipt = store.refresh(base_graph, embed_fn=embedder)
    store.close()

    assert receipt["symbols_total"] == 5
    assert receipt["embedded"] == 5
    assert receipt["embedded_new"] == 5
    assert receipt["unchanged"] == 0
    assert embedder.embedded == 5


def test_a_reformat_only_change_re_embeds_nothing(tmp_path: Path) -> None:
    base = build_graph(tmp_path / "a.db", "base", revision="rev-1")
    shifted = build_graph(tmp_path / "b.db", "reformat", revision="rev-2")
    store = _store(tmp_path / "vectors.sqlite")
    store.refresh(base, embed_fn=RecordingEmbedder())

    embedder = RecordingEmbedder()
    receipt = store.refresh(shifted, embed_fn=embedder)
    assert store.bindings()["sym-3"].start_line == 12 + _LINE_SHIFT
    store.close()

    assert embedder.embedded == 0
    assert receipt["embedded"] == 0
    assert receipt["unchanged"] == 5
    assert receipt["deleted"] == 0


def test_binding_publication_failure_rolls_back_vectors(tmp_path, monkeypatch):
    base = build_graph(tmp_path / "base.db", "base")
    changed = build_graph(tmp_path / "changed.db", "guard_change")
    store = _store(tmp_path / "vectors.sqlite")
    try:
        store.refresh(base, embed_fn=RecordingEmbedder())
        before_vectors = store.vectors()
        before_bindings = store.bindings()
        def fail(*args, **kwargs):
            raise OSError("fixture binding publication failure")
        monkeypatch.setattr(store, "_write_bindings", fail)
        with pytest.raises(OSError):
            store.refresh(changed, embed_fn=RecordingEmbedder())
        assert store.vectors() == before_vectors
        assert store.bindings() == before_bindings
    finally:
        store.close()


def test_ambiguous_stable_identity_does_not_choose_first_contract(base_graph, monkeypatch):
    rows = list(contract.contracts_with_node_ids(base_graph))
    first = rows[0][1]
    conflicting = json.loads(json.dumps(first))
    conflicting["symbol"]["qualified_name"] = "different_symbol"
    monkeypatch.setattr(contract, "contracts_with_node_ids", lambda _: [(1, first), (2, conflicting)])
    with pytest.raises(ValueError, match="ambiguous_stable_identity"):
        contract_embeddings.embedding_inputs(base_graph)


def test_a_semantic_change_re_embeds_exactly_the_changed_symbol(
    tmp_path: Path,
) -> None:
    base = build_graph(tmp_path / "a.db", "base")
    changed = build_graph(tmp_path / "b.db", "guard_change")
    store = _store(tmp_path / "vectors.sqlite")
    store.refresh(base, embed_fn=RecordingEmbedder())

    embedder = RecordingEmbedder()
    receipt = store.refresh(changed, embed_fn=embedder)
    store.close()

    assert embedder.embedded == 1
    assert receipt["embedded"] == 1
    assert receipt["unchanged"] == 4
    assert receipt["embedded_fingerprint_changed"] == 1
    assert receipt["re_embedded_stable_ids"] == ["sym-3"]


def test_a_return_shape_change_re_embeds_despite_an_unchanged_fingerprint(
    tmp_path: Path,
) -> None:
    """The producer's fingerprint counts branches and calls, not return shapes.

    Keeping the old vector here would leave the store describing behaviour the
    symbol no longer has, so the text half of the key has to catch it -- and the
    receipt has to say which half did.
    """
    base = build_graph(tmp_path / "a.db", "base")
    changed = build_graph(tmp_path / "b.db", "return_change")
    fingerprints_before = contract_embeddings.fingerprints(base)
    fingerprints_after = contract_embeddings.fingerprints(changed)
    assert fingerprints_before[4] == fingerprints_after[4]

    store = _store(tmp_path / "vectors.sqlite")
    store.refresh(base, embed_fn=RecordingEmbedder())
    embedder = RecordingEmbedder()
    receipt = store.refresh(changed, embed_fn=embedder)
    store.close()

    assert embedder.embedded == 1
    assert receipt["re_embedded_stable_ids"] == ["sym-4"]
    assert receipt["embedded_fingerprint_changed"] == 0
    assert receipt["embedded_contract_changed"] == 1


def test_a_removed_symbol_is_deleted_and_nothing_else_is_touched(
    tmp_path: Path,
) -> None:
    base = build_graph(tmp_path / "a.db", "base")
    smaller = build_graph(tmp_path / "b.db", "removed")
    store = _store(tmp_path / "vectors.sqlite")
    store.refresh(base, embed_fn=RecordingEmbedder())

    embedder = RecordingEmbedder()
    receipt = store.refresh(smaller, embed_fn=embedder)

    assert embedder.embedded == 0
    assert receipt["deleted"] == 1
    assert receipt["deleted_stable_ids"] == ["sym-5"]
    assert set(store.vectors()) == {"sym-1", "sym-2", "sym-3", "sym-4"}
    store.close()


def test_refresh_is_idempotent(base_graph: Path, tmp_path: Path) -> None:
    store = _store(tmp_path / "vectors.sqlite")
    first = store.refresh(base_graph, embed_fn=RecordingEmbedder())
    embedder = RecordingEmbedder()
    second = store.refresh(base_graph, embed_fn=embedder)
    store.close()

    assert embedder.embedded == 0
    assert second["unchanged"] == first["embedded"]


# ---------------------------------------------------------------------------
# 4. what the store persists
# ---------------------------------------------------------------------------


def test_bindings_record_line_range_fingerprint_and_revision(
    base_graph: Path, tmp_path: Path
) -> None:
    store = _store(tmp_path / "vectors.sqlite")
    store.refresh(base_graph, embed_fn=RecordingEmbedder())
    bindings = store.bindings()
    store.close()

    binding = bindings["sym-3"]
    assert binding.node_id == 3
    assert (binding.start_line, binding.end_line) == (12, 30)
    assert binding.fingerprint == "complexity:4|calls:isEmpty"
    assert binding.file_path == "ark/util/clone.ts"
    assert binding.embedded_at_revision == "rev-1"
    assert binding.contract_schema == contract.CONTRACT_SCHEMA


def test_vectors_are_keyed_by_the_minted_stable_id(
    base_graph: Path, tmp_path: Path
) -> None:
    store = _store(tmp_path / "vectors.sqlite")
    store.refresh(base_graph, embed_fn=RecordingEmbedder())
    vectors = store.vectors()
    store.close()

    assert set(vectors) == {"sym-1", "sym-2", "sym-3", "sym-4", "sym-5"}
    assert all(len(vector) == 8 for vector in vectors.values())


def test_the_store_reuses_the_engine_vector_table(
    base_graph: Path, tmp_path: Path
) -> None:
    """No second vector store: the rows land in the engine's own table."""
    path = tmp_path / "vectors.sqlite"
    store = _store(path)
    store.refresh(base_graph, embed_fn=RecordingEmbedder())
    store.close()

    connection = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        count = connection.execute(
            "SELECT COUNT(*) FROM gt_vector_documents"
        ).fetchone()
    finally:
        connection.close()

    assert "gt_vector_documents" in tables
    assert "gt_vector_index_metadata" in tables
    assert contract_embeddings.BINDING_TABLE in tables
    assert count[0] == 5


def test_refresh_never_writes_to_the_graph(base_graph: Path, tmp_path: Path) -> None:
    before = hashlib.sha256(base_graph.read_bytes()).hexdigest()
    store = _store(tmp_path / "vectors.sqlite")
    store.refresh(base_graph, embed_fn=RecordingEmbedder())
    store.close()

    assert hashlib.sha256(base_graph.read_bytes()).hexdigest() == before


def test_a_dimension_change_is_refused_rather_than_mixed(
    base_graph: Path, tmp_path: Path
) -> None:
    path = tmp_path / "vectors.sqlite"
    store = _store(path)
    store.refresh(base_graph, embed_fn=RecordingEmbedder())
    store.close()

    other = contract_embeddings.ContractEmbeddingStore(
        path, model_id="test-embedder", tokenizer_id="test-tokenizer", dimension=16
    )
    try:
        with pytest.raises(ValueError):
            other.refresh(
                base_graph, embed_fn=lambda texts: [(0.0,) * 16 for _ in texts]
            )
    finally:
        other.close()


def test_the_receipt_is_json_serialisable_and_names_its_schema(
    base_graph: Path, tmp_path: Path
) -> None:
    store = _store(tmp_path / "vectors.sqlite")
    receipt = store.refresh(base_graph, embed_fn=RecordingEmbedder())
    store.close()

    assert receipt["schema"] == contract_embeddings.RECEIPT_SCHEMA
    assert receipt["promotes_trust"] is False
    assert json.loads(json.dumps(receipt))["dimension"] == 8


# ---------------------------------------------------------------------------
# 5. retrieval consumes the vectors
# ---------------------------------------------------------------------------


def _stub_model_dir(tmp_path: Path) -> Path:
    model_dir = tmp_path / "snowflake-arctic-embed-m"
    model_dir.mkdir(exist_ok=True)
    (model_dir / "model.onnx").write_bytes(b"onnx")
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model_dir / "manifest.json").write_text("{}", encoding="utf-8")
    return model_dir


def _install_query_embedder(monkeypatch: pytest.MonkeyPatch, model_dir: Path) -> None:
    monkeypatch.setattr(
        dense_runtime,
        "_verified_assets",
        lambda _root: (model_dir / "model.onnx", model_dir / "tokenizer.json"),
    )
    monkeypatch.setattr(dense_runtime, "_DIMENSION", 8)
    identity = {**dense_runtime.model_identity(), "model_id": "test-embedder",
                "tokenizer_sha256": "test-tokenizer", "dimension": 8}
    monkeypatch.setattr(dense_runtime, "model_identity", lambda: identity)
    monkeypatch.setattr(
        dense_runtime,
        "_embed",
        lambda _m, _t, texts: [RecordingEmbedder.vector(text.removeprefix(dense_runtime.QUERY_PREFIX))
                              for text in texts],
    )


def test_lookup_rejects_stale_content_for_same_stable_id(tmp_path):
    base = build_graph(tmp_path / "base.db", "base")
    changed = build_graph(tmp_path / "changed.db", "return_change")
    path = tmp_path / "vectors.sqlite"
    store = _store(path)
    store.refresh(base, embed_fn=RecordingEmbedder())
    store.close()
    lookup = contract_embeddings.lookup_vectors(path, contract.symbol_node_ids(changed), [4],
        expected_inputs=contract_embeddings.embedding_inputs(changed),
        model_id="test-embedder", tokenizer_id="test-tokenizer", dimension=8)
    assert not lookup.vectors
    assert lookup.reason


def test_lookup_rejects_old_model_recipe(tmp_path):
    base = build_graph(tmp_path / "base.db", "base")
    path = tmp_path / "vectors.sqlite"
    store = _store(path)
    store.refresh(base, embed_fn=RecordingEmbedder())
    store.close()
    lookup = contract_embeddings.lookup_vectors(path, contract.symbol_node_ids(base), [4],
        expected_inputs=contract_embeddings.embedding_inputs(base),
        model_id="corrected-recipe", tokenizer_id="test-tokenizer", dimension=8)
    assert not lookup.vectors
    assert lookup.reason


def test_dense_rank_consumes_the_stored_contract_vectors(
    base_graph: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_dir = _stub_model_dir(tmp_path)
    _install_query_embedder(monkeypatch, model_dir)
    store_path = tmp_path / "vectors.sqlite"
    store = _store(store_path)
    store.refresh(base_graph, embed_fn=RecordingEmbedder())
    texts = {
        item.stable_id: item.text
        for item in contract_embeddings.embedding_inputs(base_graph)
    }
    store.close()

    # The query is literally one stored document's text, so the nearest
    # neighbour is that document and the wiring is unambiguous.
    result = retrieval.dense_rank(
        base_graph, texts["sym-3"], 3, model_dir=model_dir, store_path=store_path
    )

    assert result.available is True
    assert result.reason is None
    assert result.detail["vector_source"] == "contract_embedding_store"
    assert result.detail["store_hits"] == 5
    receipt = result.detail["execution_receipt"]
    assert receipt["query_ready"] is True
    assert receipt["cached_documents"] == 5
    assert receipt["embedded_documents"] == 0
    assert receipt["query_result_count"] == 3
    assert receipt["index_sha256"] == hashlib.sha256(store_path.read_bytes()).hexdigest()
    assert result[0].stable_id == retrieval_identity(3)


def test_dense_rank_from_the_store_ranks_but_never_promotes(
    base_graph: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_dir = _stub_model_dir(tmp_path)
    _install_query_embedder(monkeypatch, model_dir)
    store_path = tmp_path / "vectors.sqlite"
    store = _store(store_path)
    store.refresh(base_graph, embed_fn=RecordingEmbedder())
    store.close()
    before = hashlib.sha256(base_graph.read_bytes()).hexdigest()

    ranking = retrieval.hybrid_rank(
        base_graph, "input === null", 5, model_dir=model_dir, store_path=store_path
    )

    assert hashlib.sha256(base_graph.read_bytes()).hexdigest() == before
    assert ranking.attribution_record()["promotes_trust"] is False
    # hybrid_rank must actually pass store_path down, not merely accept it.
    dense = next(s for s in ranking.sources if s.source is retrieval.RetrievalSource.DENSE)
    assert dense.detail["vector_source"] == "contract_embedding_store"


def test_dense_rank_keeps_its_named_degraded_reason_without_the_model(
    base_graph: Path, tmp_path: Path
) -> None:
    """A populated store is not a licence to answer without the query encoder."""
    store_path = tmp_path / "vectors.sqlite"
    store = _store(store_path)
    store.refresh(base_graph, embed_fn=RecordingEmbedder())
    store.close()

    result = retrieval.dense_rank(
        base_graph,
        "empty input",
        3,
        model_dir=tmp_path / "absent",
        store_path=store_path,
    )

    assert result.available is False
    assert result.reason == "dense_model_assets_absent"
    assert list(result) == []


def test_dense_rank_names_an_absent_store_and_falls_back(
    base_graph: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_dir = _stub_model_dir(tmp_path)
    _install_query_embedder(monkeypatch, model_dir)

    result = retrieval.dense_rank(
        base_graph,
        "empty input",
        3,
        model_dir=model_dir,
        store_path=tmp_path / "no-such-store.sqlite",
        index_path=tmp_path / "dense.sqlite",
    )

    assert result.detail["store_reason"] == "contract_embedding_store_absent"
    assert result.detail["vector_source"] == "dense_runtime_pool"


def test_dense_rank_names_a_store_that_does_not_cover_the_pool(
    base_graph: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_dir = _stub_model_dir(tmp_path)
    _install_query_embedder(monkeypatch, model_dir)
    store_path = tmp_path / "vectors.sqlite"
    empty = _store(store_path)
    empty.close()

    result = retrieval.dense_rank(
        base_graph,
        "empty input",
        3,
        model_dir=model_dir,
        store_path=store_path,
        index_path=tmp_path / "dense.sqlite",
    )

    assert result.detail["store_reason"] == "contract_embedding_store_empty"
    assert result.detail["vector_source"] == "dense_runtime_pool"


def test_dense_rank_reports_a_partially_covered_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store built before a symbol existed must not hide the new symbol."""
    smaller = build_graph(tmp_path / "a.db", "removed")
    full = build_graph(tmp_path / "b.db", "base")
    model_dir = _stub_model_dir(tmp_path)
    _install_query_embedder(monkeypatch, model_dir)
    store_path = tmp_path / "vectors.sqlite"
    store = _store(store_path)
    store.refresh(smaller, embed_fn=RecordingEmbedder())
    store.close()

    result = retrieval.dense_rank(
        full, "input === null", 5, model_dir=model_dir, store_path=store_path
    )

    assert result.detail["store_hits"] == 4
    assert result.detail["store_misses"] == 1
    assert result.detail["missing_stable_ids"] == ["sym-5"]
    assert len(result.ranking) == 5


def test_dense_rank_persists_missing_document_vectors_across_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial contract store must pay for each missing document only once."""
    smaller = build_graph(tmp_path / "a.db", "removed")
    full = build_graph(tmp_path / "b.db", "base")
    model_dir = _stub_model_dir(tmp_path)
    _install_query_embedder(monkeypatch, model_dir)
    store_path = tmp_path / "vectors.sqlite"
    store = _store(store_path)
    store.refresh(smaller, embed_fn=RecordingEmbedder())
    store.close()

    document_batches: list[tuple[str, ...]] = []
    query_batches: list[tuple[str, ...]] = []
    original_documents = dense_runtime.embed_texts
    original_queries = dense_runtime.embed_queries

    def record_documents(root, texts):
        document_batches.append(tuple(texts))
        return original_documents(root, texts)

    def record_queries(root, texts):
        query_batches.append(tuple(texts))
        return original_queries(root, texts)

    monkeypatch.setattr(dense_runtime, "embed_texts", record_documents)
    monkeypatch.setattr(dense_runtime, "embed_queries", record_queries)

    first = retrieval.dense_rank(
        full, "public scope", 5, model_dir=model_dir, store_path=store_path
    )
    second = retrieval.dense_rank(
        full, "public scope again", 5, model_dir=model_dir, store_path=store_path
    )

    document_only = [batch for batch in document_batches
                     if not batch[0].startswith(dense_runtime.QUERY_PREFIX)]
    assert [len(batch) for batch in document_only] == [1]
    assert [len(batch) for batch in query_batches] == [1, 1]
    assert first.detail["runtime_embedded_missing"] == 1
    assert second.detail["runtime_embedded_missing"] == 0
    assert second.detail["runtime_document_cache_hits"] == 1


def test_fallback_document_cache_is_bound_to_content_and_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_dir = _stub_model_dir(tmp_path)
    _install_query_embedder(monkeypatch, model_dir)
    store_path = tmp_path / "vectors.sqlite"
    sqlite3.connect(store_path).close()
    documents = {"symbol": "Function symbol\nfile: source.py"}
    node_stable_ids = {7: "symbol"}
    vector = RecordingEmbedder.vector(documents["symbol"])

    contract_embeddings.publish_document_vectors(
        store_path, documents, node_stable_ids, {7: vector},
    )

    hit = contract_embeddings.lookup_document_vectors(
        store_path, documents, node_stable_ids,
    )
    changed = contract_embeddings.lookup_document_vectors(
        store_path, {"symbol": documents["symbol"] + "\nreturn_shape: changed"},
        node_stable_ids,
    )
    other_recipe = contract_embeddings.lookup_document_vectors(
        store_path, documents, node_stable_ids, recipe_id="different-document-recipe",
    )

    assert hit.vectors == {7: vector}
    assert changed.hits == 0 and changed.misses == 1
    assert other_recipe.hits == 0 and other_recipe.misses == 1


def test_dense_rank_document_cache_invalidates_changed_content_and_keeps_semantic_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Content changes re-embed once and store-only misses remain candidates."""
    smaller = build_graph(tmp_path / "small.db", "removed")
    base = build_graph(tmp_path / "base.db", "base")
    changed = build_graph(tmp_path / "changed.db", "return_change")
    model_dir = _stub_model_dir(tmp_path)
    _install_query_embedder(monkeypatch, model_dir)
    store_path = tmp_path / "vectors.sqlite"
    store = _store(store_path)
    store.refresh(smaller, embed_fn=RecordingEmbedder())
    store.close()

    document_batches: list[tuple[str, ...]] = []
    original_documents = dense_runtime.embed_texts

    def record_documents(root, texts):
        document_batches.append(tuple(texts))
        return original_documents(root, texts)

    monkeypatch.setattr(dense_runtime, "embed_texts", record_documents)

    # sym-5 is absent from the contract store. Its exact document text makes it
    # the semantic winner even though neither a cached contract vector nor a
    # lexical/property candidate is required for admission to the dense pool.
    with sqlite3.connect(base) as connection:
        provenance = {}
        documents = retrieval._dense_pool(
            connection, limit=None, labels=retrieval.SYMBOL_LABELS,
            restrict_to=None, provenance=provenance,
        )
    semantic_only_id = retrieval_identity(5)
    first = retrieval.dense_rank(
        base, documents[semantic_only_id], 5,
        model_dir=model_dir, store_path=store_path,
    )
    assert first[0].stable_id == semantic_only_id

    retrieval.dense_rank(
        changed, "changed return", 5, model_dir=model_dir, store_path=store_path
    )
    unchanged = retrieval.dense_rank(
        changed, "changed return again", 5, model_dir=model_dir, store_path=store_path
    )

    # First query embeds the previously absent sym-5. The changed return text
    # embeds once on the changed graph while sym-5 is reused by content, and the
    # following query embeds no documents.
    document_only = [batch for batch in document_batches
                     if not batch[0].startswith(dense_runtime.QUERY_PREFIX)]
    assert [len(batch) for batch in document_only] == [1, 1]
    assert unchanged.detail["runtime_embedded_missing"] == 0


def test_installed_real_onnx_document_cache_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise cache reuse and invalidation through the installed ONNX runtime."""
    graph_env = os.environ.get("GT_RETRIEVAL_TEST_GRAPH", "").strip()
    graph = Path(graph_env) if graph_env else Path()
    model_dir = Path(
        os.environ.get("GT_DENSE_MODEL_DIR", "/proof/runtime/dense-model")
    )
    if not graph_env or not graph.is_file():
        pytest.skip("installed GT_RETRIEVAL_TEST_GRAPH is unavailable")
    if not all((model_dir / name).is_file() for name in (
        "model.onnx", "tokenizer.json", "manifest.json",
    )):
        pytest.skip("installed pinned dense-model assets are unavailable")

    identity = dense_runtime.model_identity()
    assert identity["model_sha256"] == (
        "564e6c65ee0c739a486702e9e3e9b33c3f697c19c34dbe886bce9eec497ce971"
    )
    assert identity["tokenizer_sha256"] == (
        "91f1def9b9391fdabe028cd3f3fcc4efd34e5d1f08c3bf2de513ebb5911a1854"
    )
    assert "7802add0519e4bf94c46ef23552176697c7a1ac7" in str(identity["model_id"])
    store_path = tmp_path / "installed-vectors.sqlite"
    store = contract_embeddings.ContractEmbeddingStore(
        store_path,
        model_id=str(identity["model_id"]),
        tokenizer_id=str(identity["tokenizer_sha256"]),
        dimension=int(identity["dimension"]),
    )
    try:
        store.refresh(
            graph,
            embed_fn=lambda texts: dense_runtime.embed_texts(model_dir, texts),
        )
    finally:
        store.close()

    provenance: dict[str, retrieval.SymbolProvenance] = {}
    with sqlite3.connect(graph) as connection:
        documents = retrieval._dense_pool(
            connection,
            limit=None,
            labels=retrieval.SYMBOL_LABELS,
            restrict_to=None,
            provenance=provenance,
        )
    by_node = {item.node_id: stable_id for stable_id, item in provenance.items()}
    inputs = [
        item for item in contract_embeddings.embedding_inputs(graph)
        if item.node_id in by_node
    ]
    if len(inputs) < 2:
        pytest.skip("installed retrieval graph has fewer than two contract symbols")
    missing_input = max(inputs[1:], key=lambda item: len(
        documents[by_node[item.node_id]]
    ))
    missing_id = by_node[missing_input.node_id]

    # Force one contract-store miss. Dense retrieval must independently admit
    # this symbol from the complete dense pool and cache its document vector.
    with sqlite3.connect(store_path) as connection:
        connection.execute(
            "DELETE FROM gt_vector_documents WHERE document_id=?",
            (missing_input.stable_id,),
        )
        connection.execute(
            f"DELETE FROM {contract_embeddings.BINDING_TABLE} WHERE stable_id=?",
            (missing_input.stable_id,),
        )

    document_batches: list[tuple[str, ...]] = []
    query_batches: list[tuple[str, ...]] = []
    original_documents = dense_runtime.embed_texts
    original_queries = dense_runtime.embed_queries

    def record_documents(root, texts):
        document_batches.append(tuple(texts))
        return original_documents(root, texts)

    def record_queries(root, texts):
        query_batches.append(tuple(texts))
        return original_queries(root, texts)

    monkeypatch.setattr(dense_runtime, "embed_texts", record_documents)
    monkeypatch.setattr(dense_runtime, "embed_queries", record_queries)

    query = documents[missing_id]
    first = retrieval.dense_rank(
        graph, query, len(documents), model_dir=model_dir, store_path=store_path,
    )
    second = retrieval.dense_rank(
        graph, query + " unchanged", len(documents), model_dir=model_dir,
        store_path=store_path,
    )
    assert first.available and first[0].stable_id == missing_id
    assert first.detail["runtime_embedded_missing"] == 1
    assert second.detail["runtime_embedded_missing"] == 0
    assert second.detail["runtime_document_cache_hits"] == 1

    changed_graph = tmp_path / "changed-graph.db"
    shutil.copy2(graph, changed_graph)
    with sqlite3.connect(changed_graph) as connection:
        connection.execute(
            "UPDATE nodes SET qualified_name=qualified_name || '.cache_changed' "
            "WHERE id=?",
            (missing_input.node_id,),
        )
    changed_provenance: dict[str, retrieval.SymbolProvenance] = {}
    with sqlite3.connect(changed_graph) as connection:
        changed_documents = retrieval._dense_pool(
            connection, limit=None, labels=retrieval.SYMBOL_LABELS,
            restrict_to=None, provenance=changed_provenance,
        )
    changed_id = next(
        stable_id for stable_id, item in changed_provenance.items()
        if item.node_id == missing_input.node_id
    )
    changed_query = changed_documents[changed_id]
    changed = retrieval.dense_rank(
        changed_graph, changed_query, len(changed_documents), model_dir=model_dir,
        store_path=store_path,
    )
    unchanged = retrieval.dense_rank(
        changed_graph, changed_query + " unchanged", len(changed_documents),
        model_dir=model_dir, store_path=store_path,
    )

    real_document_batches = [
        batch for batch in document_batches
        if batch and not batch[0].startswith(dense_runtime.QUERY_PREFIX)
    ]
    assert [len(batch) for batch in real_document_batches] == [1, 1]
    assert [len(batch) for batch in query_batches] == [1, 1, 1, 1]
    assert changed.available and changed[0].stable_id == changed_id
    assert changed.detail["runtime_embedded_missing"] == 1
    assert unchanged.detail["runtime_embedded_missing"] == 0
    assert unchanged.detail["runtime_document_cache_hits"] == 1

    changed_node_ids = {missing_input.node_id: changed_id}
    default_recipe = contract_embeddings.lookup_document_vectors(
        store_path, changed_documents, changed_node_ids,
    )
    other_recipe = contract_embeddings.lookup_document_vectors(
        store_path, changed_documents, changed_node_ids,
        recipe_id=contract_embeddings.DOCUMENT_RECIPE_ID + ".different",
    )
    assert default_recipe.hits == 1 and default_recipe.misses == 0
    assert other_recipe.hits == 0 and other_recipe.misses == 1
