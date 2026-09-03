"""Tests for gt_engine.retrieval.

The synthetic fixture mirrors the real graph's shape rather than a convenient
one: ``nodes_fts`` is an external-content FTS5 table over ``nodes``, code
symbols carry a NULL ``stable_id`` (as they do on every graph built today), and
``properties`` holds the extracted behavioural facts keyed by ``node_id``.
A fixture that handed out stable ids would silently pass while the real graph
failed.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from gt_engine import dense_runtime, retrieval

REAL_GRAPH = Path(
    r"D:\tmp\claude\D--gt-harness\d4578d92-0fad-4131-b9ed-3ade34ece4fc"
    r"\scratchpad\ark-new.db"
)

# (id, label, name, qualified_name, file_path, language, start_line, end_line,
#  signature)
_NODES = [
    (1, "File", "clone.ts", "ark/util/clone.ts", "ark/util/clone.ts", "typescript", 1, 40, ""),
    (2, "Function", "deepClone", "deepClone", "ark/util/clone.ts", "typescript", 9, 11,
     "<input extends object>(input: input): input"),
    (3, "Function", "_clone", "_clone", "ark/util/clone.ts", "typescript", 12, 30,
     "(input: unknown): unknown"),
    (4, "Function", "parseRegex", "parseRegex", "ark/regex/parse.ts", "typescript", 4, 60,
     "(pattern: string): RegexNode"),
    (5, "Class", "Scope", "InternalScope", "ark/type/scope.ts", "typescript", 202, 400, ""),
    # A fact node: carries a stored stable_id and must never be retrieved as a
    # symbol, because nobody can open it.
    (6, "CompletenessFact", "empty input fact", "empty input fact",
     "ark/util/clone.ts", "typescript", 9, 9, ""),
]

_STORED_STABLE_IDS = {6: "fact-stable-id-6"}

# (id, node_id, kind, value, confidence)
_PROPERTIES = [
    (1, 3, "guard_clause",
     "return: (input === null || isEmpty(input)) -> return", 1.0),
    (2, 3, "boundary_condition", "empty_check|input.length === 0 => {", 0.9),
    (3, 2, "param", "input:: object [required]", 1.0),
    (4, 4, "return_shape", "value|compileRegexPattern(pattern)", 0.8),
    (5, 5, "class_field", "unit: UnitTypeParser<$> = value => this.units([value])", 1.0),
    # A low-confidence single-term mention: must rank below a symbol with two
    # confident matching facts.
    (6, 1, "docstring", "utilities for cloning input structures", 0.2),
]


def _build_fixture(path: Path) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
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
            CREATE VIRTUAL TABLE nodes_fts USING fts5(
                name, qualified_name, signature, file_path,
                content='nodes', content_rowid='id'
            );
            """
        )
        connection.executemany(
            "INSERT INTO nodes (id,label,name,qualified_name,file_path,language,"
            "start_line,end_line,signature,stable_id,source_revision) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,'rev-1')",
            [(*row, _STORED_STABLE_IDS.get(row[0])) for row in _NODES],
        )
        connection.executemany(
            "INSERT INTO properties (id,node_id,kind,value,line,confidence) "
            "VALUES (?,?,?,?,NULL,?)",
            _PROPERTIES,
        )
        # External-content tables are populated by rebuild, exactly as the
        # producer does it.
        connection.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")
        connection.commit()
    finally:
        connection.close()
    return path


@pytest.fixture()
def graph(tmp_path: Path) -> Path:
    return _build_fixture(tmp_path / "fixture.db")


def _identity(node_id: int) -> str:
    """The stable id retrieval will mint for a fixture node."""
    row = next(item for item in _NODES if item[0] == node_id)
    stored = _STORED_STABLE_IDS.get(node_id)
    if stored:
        return stored
    from gt_engine.resolution_provenance import stable_symbol_id

    return stable_symbol_id(
        language=row[5],
        path=row[4],
        qualified_name=row[3],
        native_kind=row[1],
        start_line=row[6],
        end_line=row[7],
    )


# ---------------------------------------------------------------------------
# 1. lexical
# ---------------------------------------------------------------------------


def test_lexical_rank_returns_stable_id_score_snippet_triples(graph: Path) -> None:
    result = retrieval.lexical_rank(graph, "deepClone", 5)

    assert result.available is True
    assert result.source is retrieval.RetrievalSource.LEXICAL
    assert list(result) == list(result.ranking)
    stable_id, score, snippet = result[0]
    assert stable_id == _identity(2)
    assert score > 0.0  # bm25 is negated on the way in: bigger is better
    assert snippet


def test_lexical_rank_excludes_non_symbol_labels(graph: Path) -> None:
    result = retrieval.lexical_rank(graph, "empty input", 10)

    assert _identity(6) not in [row.stable_id for row in result]


def test_lexical_rank_is_ordered_by_score_then_stable_id(graph: Path) -> None:
    result = retrieval.lexical_rank(graph, "input pattern clone", 10)

    keys = [(-row.score, row.stable_id) for row in result]
    assert keys == sorted(keys)


def test_lexical_rank_rejects_no_indexable_terms(graph: Path) -> None:
    result = retrieval.lexical_rank(graph, "?? -- !!", 5)

    assert list(result) == []
    assert result.available is True  # it ran; the query had nothing to run on
    assert result.reason == "query_has_no_indexable_terms"


def test_lexical_rank_survives_fts5_operator_punctuation(graph: Path) -> None:
    # A raw MATCH of this string would raise fts5: syntax error.
    result = retrieval.lexical_rank(graph, 'deepClone AND (NOT "input*")', 5)

    assert result.available is True
    assert [row.stable_id for row in result]


def test_lexical_rank_reports_absent_fts_table(tmp_path: Path) -> None:
    path = tmp_path / "bare.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    result = retrieval.lexical_rank(path, "anything", 5)

    assert result.available is False
    assert result.reason == "nodes_fts_absent"


# ---------------------------------------------------------------------------
# 2. property
# ---------------------------------------------------------------------------


def test_property_rank_reaches_a_guard_clause_and_returns_the_owning_symbol(
    graph: Path,
) -> None:
    """The point of the property surface: behaviour text, symbol result."""
    result = retrieval.property_rank(graph, "validates empty input", 5)

    assert result.available is True
    top = result[0]
    # _clone's guard_clause carries both "empty" and "input"; the result is the
    # symbol that owns the clause, and the snippet names the kind that matched
    # so a reader can see why.
    assert top.stable_id == _identity(3)
    assert top.snippet.startswith("guard_clause: ")
    # covers 2 of 3 query terms; weight = guard 2x1.0 + boundary 2x0.9 = 3.8
    assert top.score == pytest.approx(2 + (1 - 1 / 4.8))


def test_property_rank_scores_coverage_first_then_weighted_evidence(
    graph: Path,
) -> None:
    result = retrieval.property_rank(graph, "input", 5)
    scores = {row.stable_id: row.score for row in result}

    # One query term, so all three sit at coverage 1 and the weighted evidence
    # term orders them within that level.
    # _clone: guard_clause (1 term x 1.0) + boundary_condition (1 x 0.9) = 1.9
    assert scores[_identity(3)] == pytest.approx(1 + (1 - 1 / 2.9))
    # deepClone: one param fact at confidence 1.0
    assert scores[_identity(2)] == pytest.approx(1 + (1 - 1 / 2.0))
    # the File node's single low-confidence docstring mention ranks last
    assert scores[_identity(1)] == pytest.approx(1 + (1 - 1 / 1.2))
    assert list(scores) == [_identity(3), _identity(2), _identity(1)]


def test_property_rank_puts_coverage_above_accumulated_evidence(
    graph: Path,
) -> None:
    """A symbol matching the whole query beats one matching it loudly once."""
    result = retrieval.property_rank(graph, "empty pattern", 5)
    scores = {row.stable_id: row.score for row in result}

    # _clone matches only "empty" (twice, confidently); parseRegex matches only
    # "pattern" (once) -- both at coverage 1, so weight decides.
    assert scores[_identity(3)] > scores[_identity(4)]
    assert 1.0 < scores[_identity(3)] < 2.0
    assert 1.0 < scores[_identity(4)] < 2.0


def test_property_rank_can_be_restricted_to_kinds(graph: Path) -> None:
    result = retrieval.property_rank(
        graph, "input", 5, kinds=("guard_clause",)
    )

    assert [row.stable_id for row in result] == [_identity(3)]


def test_property_rank_escapes_like_wildcards(graph: Path) -> None:
    # "_clone" tokenises to a term containing LIKE's single-char wildcard; if it
    # were not escaped this would match far more than it should.
    result = retrieval.property_rank(graph, "_clone", 5)

    assert [row.stable_id for row in result] == []


def test_property_rank_is_ordered_by_score_then_stable_id(graph: Path) -> None:
    result = retrieval.property_rank(graph, "input value pattern", 10)

    keys = [(-row.score, row.stable_id) for row in result]
    assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# 3. dense
# ---------------------------------------------------------------------------


def test_dense_rank_degrades_when_model_dir_is_unset(
    graph: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GT_DENSE_MODEL_DIR", raising=False)

    result = retrieval.dense_rank(graph, "validates empty input", 5)

    assert result.available is False
    assert result.reason == "dense_model_dir_unset"
    assert list(result) == []


def test_dense_rank_degrades_when_assets_are_absent(
    graph: Path, tmp_path: Path
) -> None:
    empty = tmp_path / "no-model"
    empty.mkdir()

    result = retrieval.dense_rank(graph, "clone", 5, model_dir=empty)

    assert result.available is False
    assert result.reason == "dense_model_assets_absent"
    assert result.detail["missing"] == [
        "model.onnx", "tokenizer.json", "manifest.json",
    ]
    assert list(result) == []


def test_dense_rank_degrades_when_the_runtime_raises(
    graph: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_dir = _stub_model_dir(tmp_path)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("onnxruntime missing")

    monkeypatch.setattr(dense_runtime, "rank_documents", _boom)

    result = retrieval.dense_rank(graph, "clone", 5, model_dir=model_dir)

    assert result.available is False
    assert result.reason.startswith("dense_runtime_failed:RuntimeError")
    assert list(result) == []


def _stub_model_dir(tmp_path: Path) -> Path:
    model_dir = tmp_path / "snowflake-arctic-embed-m"
    model_dir.mkdir(exist_ok=True)
    (model_dir / "model.onnx").write_bytes(b"onnx")
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model_dir / "manifest.json").write_text("{}", encoding="utf-8")
    return model_dir


def _install_fake_embedder(monkeypatch: pytest.MonkeyPatch, model_dir: Path) -> None:
    """A two-dimensional stand-in for the ONNX forward pass.

    Documents mentioning "empty" point one way, everything else the other; the
    query points at the "empty" axis.  Enough to prove the wiring without
    requiring the pinned 428 MB asset.
    """
    monkeypatch.setattr(
        dense_runtime,
        "_verified_assets",
        lambda _root: (model_dir / "model.onnx", model_dir / "tokenizer.json"),
    )
    monkeypatch.setattr(dense_runtime, "_DIMENSION", 2)
    monkeypatch.setattr(
        dense_runtime,
        "_embed",
        lambda _m, _t, texts: [
            (1.0, 0.0) if "empty" in text.lower() else (0.0, 1.0) for text in texts
        ],
    )


def test_dense_rank_runs_standalone_over_a_bounded_pool(
    graph: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_dir = _stub_model_dir(tmp_path)
    _install_fake_embedder(monkeypatch, model_dir)

    result = retrieval.dense_rank(
        graph,
        "empty",
        3,
        model_dir=model_dir,
        index_path=tmp_path / "dense.sqlite",
    )

    assert result.available is True
    assert result.reason is None
    # Only _clone's contract text contains "empty" (boundary_condition), so it
    # is the only document on the query axis.
    assert result[0].stable_id == _identity(3)
    assert result.detail["pool_size"] == 5  # the five symbol-labelled nodes
    assert result.detail["pool_bounded"] is False


def test_dense_rank_honours_restrict_to(
    graph: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_dir = _stub_model_dir(tmp_path)
    _install_fake_embedder(monkeypatch, model_dir)

    result = retrieval.dense_rank(
        graph,
        "empty",
        3,
        model_dir=model_dir,
        index_path=tmp_path / "dense.sqlite",
        restrict_to=[_identity(2), _identity(4)],
    )

    assert result.detail["pool_size"] == 2
    assert set(row.stable_id for row in result) <= {_identity(2), _identity(4)}


# ---------------------------------------------------------------------------
# 4. fusion
# ---------------------------------------------------------------------------


def _rows(*stable_ids: str) -> list[retrieval.RankedSymbol]:
    return [
        retrieval.RankedSymbol(stable_id, 0.0, f"snippet-{stable_id}")
        for stable_id in stable_ids
    ]


def test_fuse_matches_a_hand_computed_rrf_example() -> None:
    """score(d) = sum 1/(k + rank), k = 10, ranks 1-based.

    A = [bbb, aaa, ccc], B = [aaa, bbb]

        bbb = 1/(10+1) + 1/(10+2) = 1/11 + 1/12 = 0.174242424242...
        aaa = 1/(10+2) + 1/(10+1) = 1/12 + 1/11 = 0.174242424242...
        ccc = 1/(10+3)                          = 0.076923076923...

    aaa and bbb tie exactly, so the tie-break decides: `aaa` must come first
    even though `bbb` led the first ranking.
    """
    fused = retrieval.fuse([_rows("bbb", "aaa", "ccc"), _rows("aaa", "bbb")], 10)

    assert [row.stable_id for row in fused] == ["aaa", "bbb", "ccc"]
    assert fused[0].score == pytest.approx(1 / 11 + 1 / 12)
    assert fused[1].score == pytest.approx(1 / 11 + 1 / 12)
    assert fused[2].score == pytest.approx(1 / 13)


def test_fuse_default_constant_is_sixty() -> None:
    fused = retrieval.fuse([_rows("aaa", "bbb")])

    assert fused[0].score == pytest.approx(1 / 61)
    assert fused[1].score == pytest.approx(1 / 62)


def test_fuse_takes_the_snippet_from_the_first_contributing_ranking() -> None:
    first = [retrieval.RankedSymbol("aaa", 9.0, "from-first")]
    second = [retrieval.RankedSymbol("aaa", 1.0, "from-second")]

    assert retrieval.fuse([first, second])[0].snippet == "from-first"
    assert retrieval.fuse([second, first])[0].snippet == "from-second"


def test_fuse_ignores_an_unavailable_source() -> None:
    lexical = retrieval.SourceRanking(
        retrieval.RetrievalSource.LEXICAL, tuple(_rows("aaa", "bbb"))
    )
    dense = retrieval.SourceRanking(
        retrieval.RetrievalSource.DENSE, (), available=False, reason="absent"
    )

    with_dense = retrieval.fuse([lexical, dense])
    without_dense = retrieval.fuse([lexical])

    assert with_dense == without_dense


def test_fuse_honours_limit_and_rejects_a_negative_constant() -> None:
    assert len(retrieval.fuse([_rows("aaa", "bbb", "ccc")], limit=2)) == 2
    with pytest.raises(ValueError, match="rrf_k_must_be_non_negative"):
        retrieval.fuse([_rows("aaa")], -1)


# ---------------------------------------------------------------------------
# 5. hybrid, determinism, and the trust invariant
# ---------------------------------------------------------------------------


def test_hybrid_rank_records_inputs_and_availability(graph: Path) -> None:
    result = retrieval.hybrid_rank(graph, "validates empty input", 5, use_dense=False)

    assert [str(s.source) for s in result.sources] == ["lexical", "property", "dense"]
    assert result.available_sources == ("lexical", "property")
    assert result.degraded_sources == {"dense": "dense_disabled_by_caller"}
    assert result.fused
    assert _identity(3) in [row.stable_id for row in result.fused]
    # _clone is reached by both surfaces here: its signature mentions `input`
    # and its guard clause mentions "empty input".
    assert result.contributing_sources(_identity(3)) == ("lexical", "property")


def test_hybrid_rank_surfaces_a_symbol_only_the_property_index_can_reach(
    graph: Path,
) -> None:
    """The delta this item buys: a hit no identifier index could produce."""
    result = retrieval.hybrid_rank(graph, "UnitTypeParser", 5, use_dense=False)

    lexical = next(s for s in result.sources if s.source == "lexical")
    assert list(lexical) == []
    assert result.contributing_sources(_identity(5)) == ("property",)
    assert result.fused[0].stable_id == _identity(5)


def test_hybrid_fusion_is_a_subset_of_the_union_of_its_inputs(graph: Path) -> None:
    result = retrieval.hybrid_rank(graph, "input pattern clone", 10, use_dense=False)

    union = {row.stable_id for source in result.sources for row in source}
    assert {row.stable_id for row in result.fused} <= union


def test_hybrid_rank_is_deterministic(graph: Path) -> None:
    first = retrieval.hybrid_rank(graph, "validates empty input", 5, use_dense=False)
    second = retrieval.hybrid_rank(graph, "validates empty input", 5, use_dense=False)

    assert first.fused == second.fused
    assert [s.as_dict() for s in first.sources] == [s.as_dict() for s in second.sources]
    assert first.attribution_record() == second.attribution_record()


def test_hybrid_rank_accepts_an_open_connection_without_closing_it(
    graph: Path,
) -> None:
    connection = sqlite3.connect(f"file:{graph}?mode=ro", uri=True)
    try:
        result = retrieval.hybrid_rank(connection, "clone", 5, use_dense=False)
        assert result.fused
        # Still usable: hybrid_rank must not close a caller's connection.
        assert connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 6
    finally:
        connection.close()


def test_retrieval_never_writes_to_the_graph(graph: Path) -> None:
    """Ranking is not evidence: no tier, no edge, not one byte."""
    before = hashlib.sha256(graph.read_bytes()).hexdigest()

    retrieval.hybrid_rank(graph, "validates empty input", 5, use_dense=False)
    retrieval.lexical_rank(graph, "clone", 5)
    retrieval.property_rank(graph, "input", 5)

    assert hashlib.sha256(graph.read_bytes()).hexdigest() == before
    assert not os.path.exists(str(graph) + "-wal")


def test_attribution_record_is_content_safe_and_names_the_source(
    graph: Path,
) -> None:
    result = retrieval.hybrid_rank(graph, "validates empty input", 3, use_dense=False)
    record = result.attribution_record()

    assert record["schema"] == "gt.hybrid_retrieval.v1"
    assert record["promotes_trust"] is False
    assert record["query_sha256"] == hashlib.sha256(
        b"validates empty input"
    ).hexdigest()
    assert record["query_chars"] == len("validates empty input")
    assert record["degraded_sources"]["dense"] == "dense_disabled_by_caller"
    serialized = repr(record)
    # No snippet text anywhere in the record.
    assert "guard_clause: return:" not in serialized
    top = record["fused"][0]
    assert top["rank"] == 1
    assert top["contributing_sources"]
    assert top["provenance"]["identity_origin"] == "derived:gt.symbol.identity.v1"


def test_symbol_identity_is_derived_when_the_graph_stores_none(graph: Path) -> None:
    result = retrieval.lexical_rank(graph, "deepClone", 5)
    provenance: dict[str, retrieval.SymbolProvenance] = {}
    retrieval.lexical_rank(graph, "deepClone", 5, provenance=provenance)

    entry = provenance[result[0].stable_id]
    assert entry.identity_origin == "derived:gt.symbol.identity.v1"
    assert entry.file_path == "ark/util/clone.ts"
    assert entry.node_id == 2


def test_lexical_only_and_dense_only_remain_runnable_standalone(
    graph: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lexical_only = retrieval.lexical_rank(graph, "empty input", 5)
    model_dir = _stub_model_dir(tmp_path)
    _install_fake_embedder(monkeypatch, model_dir)
    dense_only = retrieval.dense_rank(
        graph, "empty input", 5, model_dir=model_dir,
        index_path=tmp_path / "dense.sqlite",
    )

    assert lexical_only.available is True
    assert dense_only.available is True
    # They disagree, which is the whole reason for fusing them.
    assert [row.stable_id for row in lexical_only] != [
        row.stable_id for row in dense_only
    ]


# ---------------------------------------------------------------------------
# 6. integration against the real graph
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not REAL_GRAPH.is_file(), reason="real arktype graph absent")
@pytest.mark.parametrize("query", ["validates empty input", "parse regex pattern"])
def test_real_graph_hybrid_rank(query: str) -> None:
    result = retrieval.hybrid_rank(REAL_GRAPH, query, 5)

    lexical = next(s for s in result.sources if s.source == "lexical")
    assert lexical.available is True
    assert len(lexical) > 0, "the populated nodes_fts must return lexical hits"

    union = {row.stable_id for source in result.sources for row in source}
    assert {row.stable_id for row in result.fused} <= union
    assert result.fused

    for row in result.fused:
        provenance = result.provenance[row.stable_id]
        assert provenance.label in retrieval.SYMBOL_LABELS
        assert provenance.file_path
        assert result.contributing_sources(row.stable_id)

    # Determinism on the real graph, not only on the fixture.
    assert retrieval.hybrid_rank(REAL_GRAPH, query, 5).fused == result.fused
