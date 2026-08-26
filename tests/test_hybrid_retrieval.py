from __future__ import annotations

import math
from dataclasses import replace

import gt_engine.hybrid_retrieval as hybrid_module
from gt_engine.hybrid_retrieval import (
    BM25RetrievalChannel,
    DenseRetrievalChannel,
    EvidenceAuthority,
    EvidenceOrigin,
    ExactRetrievalChannel,
    HybridRetriever,
    LexicalRetrievalChannel,
    RepositoryDocument,
    RetrievalActionState,
    RetrievalCandidate,
    RetrievalChannel,
    RetrievalIntent,
    RetrievalState,
    StructuralLink,
    StructuralRetrievalChannel,
    build_preemptive_frame,
    filter_provider_known_context,
    reciprocal_rank_fusion,
    retrieval_exact_identifiers,
    retrieval_query_terms,
)


def _state(**overrides: object) -> RetrievalState:
    values: dict[str, object] = {
        "task_text": "repair allocator cleanup",
        "intent": RetrievalIntent.IMPLEMENTATION_CONTEXT,
        "source_revision": "source-1",
    }
    values.update(overrides)
    return RetrievalState(**values)


def _candidate(
    path: str,
    channel: RetrievalChannel,
    rank: int,
    *,
    text: str = "implementation",
) -> RetrievalCandidate:
    return RetrievalCandidate(
        path=path,
        start_line=1,
        end_line=2,
        symbol=None,
        text=text,
        channel=channel,
        channel_rank=rank,
        relation=None,
        provenance=(channel.value,),
        source_revision="source-1",
        channel_score=1.0 / rank,
    )


class FakeDenseBackend:
    """Deterministic semantic witness; no external model is involved."""

    identity = "fake-dense-v1"

    def embed_query(self, text: str) -> tuple[float, ...]:
        del text
        return (1.0, 0.0)

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(
            (1.0, 0.0) if "releases reserved storage" in text else (0.0, 1.0) for text in texts
        )


class BrokenDenseBackend:
    def embed_query(self, text: str) -> tuple[float, ...]:
        del text
        raise RuntimeError("model unavailable")

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        del texts
        raise AssertionError("query failure should short-circuit")


class CountingDenseBackend(FakeDenseBackend):
    def __init__(self) -> None:
        self.query_calls = 0

    def embed_query(self, text: str) -> tuple[float, ...]:
        self.query_calls += 1
        return super().embed_query(text)


def test_typed_state_builds_trajectory_conditioned_query_without_gold_fields():
    state = _state(
        task_text="repair allocator cleanup",
        active_paths=("src/allocator.py",),
        active_symbols=("Arena.release",),
        changed_paths=("src/pool.py",),
        diagnostics=("tests/test_pool.py:44 leaked block",),
        validation_state="fail",
    )

    query = state.query_text()

    assert "repair allocator cleanup" in query
    assert "src/allocator.py" in query
    assert "Arena.release" in query
    assert "tests/test_pool.py:44 leaked block" in query
    assert not hasattr(state, "gold_files")


def test_diagnostic_query_plan_prioritizes_current_failure_over_full_task_prose():
    state = _state(
        task_text="rewrite the entire service and update unrelated documentation",
        intent=RetrievalIntent.DIAGNOSTIC_ROOT_CAUSE,
        action=RetrievalActionState(
            operation="validate",
            executable="pytest",
            targets=("tests/test_pool.py",),
        ),
        active_symbols=("Arena.release",),
        diagnostics=("tests/test_pool.py:44 leaked block in Arena.release",),
        validation_state="fail",
    )

    plan = state.query_plan()

    assert "tests/test_pool.py:44" in plan.primary_text
    assert "Arena.release" in plan.primary_text
    assert "rewrite the entire service" not in plan.primary_text
    assert "rewrite the entire service" in plan.fallback_text
    assert state.sparse_query_text() == plan.primary_text
    assert state.dense_query_text() == plan.primary_text


def test_legacy_raw_action_is_normalized_to_typed_state_without_heredoc_body():
    state = _state(
        proposed_action=(
            "python - <<'PY'\n"
            "SECRET_PROGRAM_BODY = 'must never enter retrieval'\n"
            "print(SECRET_PROGRAM_BODY)\n"
            "PY"
        )
    )

    assert isinstance(state.action, RetrievalActionState)
    assert state.action.executable == "python"
    assert "SECRET_PROGRAM_BODY" not in state.query_text()
    assert "must never enter retrieval" not in state.sparse_query_text()


def test_legacy_inline_program_body_is_not_retrieval_query_text():
    state = _state(proposed_action="python -c \"print('PRIVATE_INLINE_PROGRAM')\"")

    assert isinstance(state.action, RetrievalActionState)
    assert state.action.executable == "python"
    assert "PRIVATE_INLINE_PROGRAM" not in state.query_text()


def test_explicit_typed_action_contributes_only_bounded_semantic_fields():
    state = _state(
        action=RetrievalActionState(
            operation="validate",
            executable="pytest",
            targets=("tests/test_allocator.py",),
            validation_kind="pytest",
        )
    )

    query = state.query_text()

    assert "operation=validate" in query
    assert "executable=pytest" in query
    assert "targets=tests/test_allocator.py" in query


def test_exact_lexical_and_bm25_channels_are_independent_rankers():
    documents = (
        RepositoryDocument(
            "src/allocator.py",
            "def cleanup_allocator(): pass",
            symbol="cleanup_allocator",
        ),
        RepositoryDocument("src/network.py", "def open_socket(): pass", symbol="open_socket"),
        RepositoryDocument("tests/test_allocator.py", "cleanup allocator regression test"),
    )
    state = _state(task_text="cleanup allocator")

    exact = ExactRetrievalChannel(documents).retrieve(state, limit=10)
    lexical = LexicalRetrievalChannel(documents).retrieve(state, limit=10)
    bm25 = BM25RetrievalChannel(documents).retrieve(state, limit=10)

    assert exact[0].path == "src/allocator.py"
    assert lexical[0].path in {"src/allocator.py", "tests/test_allocator.py"}
    assert bm25[0].path in {"src/allocator.py", "tests/test_allocator.py"}
    assert {row.channel for row in exact} == {RetrievalChannel.EXACT}
    assert {row.channel for row in lexical} == {RetrievalChannel.LEXICAL}
    assert {row.channel for row in bm25} == {RetrievalChannel.BM25}


def test_prepared_sparse_channels_do_not_retokenize_documents_per_query(monkeypatch):
    marker = "unique_document_marker"
    documents = (RepositoryDocument("src/allocator.py", f"cleanup allocator {marker}"),)
    retriever = HybridRetriever(documents, dense_backend=None)
    original = hybrid_module._tokens
    observed: list[str] = []

    def recording_tokens(text: str) -> tuple[str, ...]:
        observed.append(text)
        return original(text)

    monkeypatch.setattr(hybrid_module, "_tokens", recording_tokens)
    retriever.retrieve(_state(task_text="cleanup allocator"), token_budget=200)
    retriever.retrieve(_state(task_text="cleanup allocator again"), token_budget=200)

    assert not any(marker in text for text in observed)


def test_exact_channel_splits_snake_and_camel_case_symbols():
    documents = (
        RepositoryDocument(
            "src/helpers.py",
            "def cleanupAllocatorCache(): pass",
            symbol="cleanupAllocatorCache",
        ),
    )

    ranked = ExactRetrievalChannel(documents).retrieve(
        _state(task_text="repair allocator cache cleanup"),
        limit=10,
    )

    assert ranked[0].path == "src/helpers.py"
    assert "exact_symbol_token" in ranked[0].provenance


def test_short_or_common_symbol_is_rank_only_and_never_exact_certified():
    for symbol in ("x", "run"):
        documents = (
            RepositoryDocument(
                f"src/worker_{symbol}.py",
                f"def {symbol}(): pass",
                symbol=symbol,
            ),
        )
        state = _state(task_text=f"repair {symbol}")
        channel = ExactRetrievalChannel(documents)

        ranked = channel.retrieve(state, limit=10)
        result = HybridRetriever((), channels=(channel,)).retrieve(state)

        assert "exact_symbol" not in ranked[0].provenance
        assert result.selected_context == ()


def test_exact_symbol_certification_requires_unique_explicit_identifier():
    duplicate_documents = (
        RepositoryDocument("src/one.py", "def calculateTotal(): pass", symbol="calculateTotal"),
        RepositoryDocument("src/two.py", "def calculateTotal(): pass", symbol="calculateTotal"),
    )
    unique_documents = duplicate_documents[:1]
    state = _state(task_text="repair `calculateTotal()`")

    duplicate = ExactRetrievalChannel(duplicate_documents).retrieve(state, limit=10)
    unique = ExactRetrievalChannel(unique_documents).retrieve(state, limit=10)

    assert all("exact_symbol" not in row.provenance for row in duplicate)
    assert "exact_symbol" in unique[0].provenance

    selected = HybridRetriever(unique_documents, dense_backend=None).retrieve(state)
    assert selected.selected_context == ()
    assert unique[0].authority is EvidenceAuthority.IDENTITY_ONLY
    assert "no_decision_relevant_evidence" in selected.reason_codes


def test_ordinary_task_prose_cannot_be_promoted_to_exact_symbol_authority():
    documents = (
        RepositoryDocument("terminal/terminal.go", "func clear() {}", symbol="clear"),
        RepositoryDocument(
            "eval/modules.go",
            "func require_cache_info() {}",
            symbol="require_cache_info",
        ),
    )
    state = _state(
        task_text=(
            "Clear the module cache and update `require_cache_info()` so "
            "ABS_MODULE_PATH remains authoritative."
        )
    )

    ranked = ExactRetrievalChannel(documents).retrieve(state, limit=10)
    by_symbol = {row.symbol: row for row in ranked}

    assert "exact_symbol" not in by_symbol["clear"].provenance
    assert "exact_symbol" in by_symbol["require_cache_info"].provenance


def test_behavior_subject_pascal_types_seed_exact_graph_retrieval() -> None:
    state = _state(
        task_text=(
            "The Reporter constructor validates this config. Config adds template "
            "validation. Bottle is a lightweight framework."
        )
    )

    identifiers = retrieval_exact_identifiers(state)

    assert "reporter" in identifiers
    assert "config" in identifiers
    assert "bottle" not in identifiers


def test_exact_path_certification_requires_a_complete_path_token():
    documents = (RepositoryDocument("src/calculate.py", "def calculate(): pass"),)

    explicit = ExactRetrievalChannel(documents).retrieve(
        _state(task_text="inspect src/calculate.py"),
        limit=10,
    )
    app_absolute = ExactRetrievalChannel(documents).retrieve(
        _state(task_text="inspect /app/src/calculate.py"),
        limit=10,
    )
    suffix_collision = ExactRetrievalChannel(documents).retrieve(
        _state(task_text="inspect src/calculate.py.bak"),
        limit=10,
    )

    assert "exact_path" in explicit[0].provenance
    assert "exact_path" in app_absolute[0].provenance
    assert "exact_path" not in suffix_collision[0].provenance


def test_claim_identity_ignores_global_revision_but_tracks_semantic_evidence():
    original = RetrievalCandidate(
        path="src/calculate.py",
        start_line=4,
        end_line=8,
        symbol="calculateTotal",
        text="def calculateTotal(): return 1",
        channel=RetrievalChannel.STRUCTURAL,
        channel_rank=1,
        relation="CALLS",
        provenance=("graph_edge:7", "trust:CERTIFIED"),
        source_revision="source-1",
    )

    assert replace(original, source_revision="source-unrelated").claim_hash == original.claim_hash
    assert (
        replace(original, text="def calculateTotal(): return 2").claim_hash
        != original.claim_hash
    )
    assert replace(original, relation="IMPORTS").claim_hash != original.claim_hash
    # Physical graph row IDs are rebuild-local.  The same bounded semantic
    # fact must retain one delivery identity after an unrelated graph rebuild.
    assert replace(original, provenance=("graph_edge:8",)).claim_hash == original.claim_hash
    assert (
        replace(
            original,
            provenance=(
                *original.provenance,
                "delivery_support:certified",
                "support_channel:structural",
            ),
        ).claim_hash
        == original.claim_hash
    )


def test_structural_channel_returns_the_edge_endpoint_span_not_arbitrary_file_span():
    documents = (
        RepositoryDocument(
            "src/errors.ts",
            "export class ResolutionError {}",
            1,
            1,
            "ResolutionError",
        ),
        RepositoryDocument(
            "src/container.test.ts",
            "it('surfaces ResolutionError', () => expect(resolve()).toThrow(ResolutionError))",
            10,
            10,
            "surfaces_resolution_error",
        ),
        RepositoryDocument(
            "src/container.test.ts",
            "it('supports Symbol.toStringTag', () => expect(container).toBeDefined())",
            80,
            80,
            "symbol_to_string_tag",
        ),
    )
    result = HybridRetriever(
        documents,
        structural_links=(
            StructuralLink(
                "src/errors.ts",
                "src/container.test.ts",
                "ASSERTED_BY",
                confidence=1.0,
                certified=True,
                source_symbol="ResolutionError",
                source_start_line=1,
                target_symbol="surfaces_resolution_error",
                target_start_line=10,
            ),
        ),
        dense_backend=None,
    ).retrieve(
        RetrievalState(
            task_text="change ResolutionError",
            intent=RetrievalIntent.CHANGE_IMPACT,
            active_paths=("src/errors.ts",),
            source_revision="source-1",
        ),
        selection_limit=1,
        token_budget=200,
    )

    structural = next(
        row
        for row in result.ranked_spans
        if row.channel is RetrievalChannel.STRUCTURAL
    )
    assert structural.start_line == 10
    assert structural.symbol == "surfaces_resolution_error"
    assert "Symbol.toStringTag" not in structural.text


def test_unresolved_structural_endpoint_never_carries_alignment_certificate():
    documents = (
        RepositoryDocument("src/errors.ts", "export class ResolutionError {}"),
        RepositoryDocument(
            "src/container.test.ts",
            "it('supports Symbol.toStringTag', () => expect(container).toBeDefined())",
            80,
            80,
            "symbol_to_string_tag",
        ),
    )
    result = HybridRetriever(
        documents,
        structural_links=(
            StructuralLink(
                "src/errors.ts",
                "src/container.test.ts",
                "ASSERTED_BY",
                confidence=1.0,
                certified=True,
                target_symbol="missing_test_symbol",
                target_start_line=10,
            ),
        ),
        dense_backend=None,
    ).retrieve(
        RetrievalState(
            task_text="change ResolutionError",
            intent=RetrievalIntent.CHANGE_IMPACT,
            active_paths=("src/errors.ts",),
            source_revision="source-1",
        ),
        selection_limit=1,
    )

    structural = next(
        row
        for row in result.ranked_spans
        if row.channel is RetrievalChannel.STRUCTURAL
    )
    assert "edge_endpoint_unresolved" in structural.provenance
    assert not any(item.startswith("edge_endpoint_start:") for item in structural.provenance)
    assert result.selected_context == ()


def test_closed_token_budget_does_not_execute_any_retrieval_channel() -> None:
    class MustNotRun:
        channel = RetrievalChannel.EXACT

        def retrieve(self, state, *, limit):  # pragma: no cover - RED sentinel
            raise AssertionError("retrieval ran after its delivery budget closed")

    result = HybridRetriever((), channels=(MustNotRun(),)).retrieve(
        _state(),
        token_budget=0,
    )

    assert result.abstained is True
    assert result.reason_codes == ("context_budget_closed",)
    assert result.channel_receipts == ()


def test_retrieved_unchanged_evidence_keeps_claim_across_unrelated_revisions():
    document = RepositoryDocument(
        "src/calculate.py",
        "def calculateTotal(): return 1",
        symbol="calculateTotal",
        provenance=("graph_node:4",),
    )
    retriever = HybridRetriever((document,), dense_backend=None)

    before = retriever.retrieve(
        _state(task_text="inspect src/calculate.py", source_revision="source-1")
    )
    after = retriever.retrieve(
        _state(task_text="inspect src/calculate.py", source_revision="source-2")
    )
    changed = HybridRetriever(
        (
            replace(
                document,
                text="def calculateTotal(): return 2",
            ),
        ),
        dense_backend=None,
    ).retrieve(_state(task_text="inspect src/calculate.py", source_revision="source-2"))

    assert before.ranked_spans[0].claim_hash == after.ranked_spans[0].claim_hash
    assert changed.ranked_spans[0].claim_hash != after.ranked_spans[0].claim_hash


def test_dense_channel_finds_semantic_candidate_sparse_terms_do_not_name():
    documents = (
        RepositoryDocument("src/reclaimer.py", "releases reserved storage after use"),
        RepositoryDocument("src/socket.py", "opens a remote network connection"),
    )
    state = _state(task_text="repair allocator cleanup")

    dense = DenseRetrievalChannel(documents, FakeDenseBackend()).retrieve(state, limit=2)

    assert dense[0].path == "src/reclaimer.py"
    assert dense[0].channel is RetrievalChannel.DENSE


def test_dense_channel_can_use_a_bounded_cascade_candidate_pool():
    class CapturingBackend(FakeDenseBackend):
        documents: tuple[str, ...] = ()

        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            self.documents = texts
            return super().embed_documents(texts)

    backend = CapturingBackend()
    documents = tuple(
        RepositoryDocument(f"src/{index}.py", "releases reserved storage after use")
        for index in range(4)
    )
    channel = DenseRetrievalChannel(documents, backend)
    channel.set_candidate_paths(("src/2.py", "src/0.py"))

    result = channel.retrieve(_state(), limit=10)

    assert [row.path for row in result] == ["src/0.py", "src/2.py"]
    assert len(backend.documents) == 2
    assert "candidate_pool=2/4_docs/2_paths" in channel.availability_reason


def test_dense_candidate_limit_bounds_spans_when_a_path_has_many_documents():
    class CapturingBackend(FakeDenseBackend):
        documents: tuple[str, ...] = ()

        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            self.documents = texts
            return super().embed_documents(texts)

    backend = CapturingBackend()
    documents = tuple(
        RepositoryDocument(f"src/{index // 4}.py", "releases reserved storage after use")
        for index in range(12)
    )
    channel = DenseRetrievalChannel(documents, backend)
    channel.set_candidate_paths(("src/0.py", "src/1.py", "src/2.py"), document_limit=5)

    channel.retrieve(_state(), limit=10)

    assert len(backend.documents) == 5


def test_dense_backend_receives_path_symbol_and_exact_source_text():
    class CapturingBackend(FakeDenseBackend):
        documents: tuple[str, ...] = ()

        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            self.documents = texts
            return super().embed_documents(texts)

    backend = CapturingBackend()
    document = RepositoryDocument(
        "src/reclaimer.py",
        "releases reserved storage after use",
        symbol="release_pool",
    )

    DenseRetrievalChannel((document,), backend).retrieve(_state(), limit=1)

    assert "path: src/reclaimer.py" in backend.documents[0]
    assert "symbol: release_pool" in backend.documents[0]
    assert "releases reserved storage after use" in backend.documents[0]


def test_structural_channel_uses_known_path_as_seed_and_returns_related_file():
    documents = (
        RepositoryDocument("src/allocator.py", "def allocate(): pass"),
        RepositoryDocument("tests/test_allocator.py", "def test_allocate(): pass"),
    )
    links = (
        StructuralLink(
            source_path="src/allocator.py",
            target_path="tests/test_allocator.py",
            relation="tested_by",
            confidence=1.0,
        ),
    )
    state = _state(active_paths=("src/allocator.py",), intent=RetrievalIntent.VALIDATION_CONTEXT)

    ranked = StructuralRetrievalChannel(documents, links).retrieve(state, limit=10)

    assert [row.path for row in ranked] == ["tests/test_allocator.py"]
    assert ranked[0].relation == "tested_by"


def test_high_confidence_cochange_fact_is_not_alone_a_delivery_certificate():
    documents = (
        RepositoryDocument("src/anchor.py", "anchor_surface"),
        RepositoryDocument("src/neighbor.py", "zqxv_payload"),
    )
    state = _state(
        task_text="repair foobar",
        active_paths=("src/anchor.py",),
        intent=RetrievalIntent.CHANGE_IMPACT,
    )
    retriever = HybridRetriever(
        documents,
        structural_links=(
            StructuralLink(
                "src/anchor.py",
                "src/neighbor.py",
                "COCHANGE",
                confidence=1.0,
                certified=False,
            ),
        ),
    )

    result = retriever.retrieve(state, selection_limit=1)

    assert result.ranked_files[0].path == "src/neighbor.py"
    assert result.selected_context == ()
    assert result.abstained is True


def test_certified_structural_candidate_without_edge_endpoint_abstains() -> None:
    class StructuralOnly:
        channel = RetrievalChannel.STRUCTURAL

        def retrieve(self, state: RetrievalState, *, limit: int):
            del state, limit
            return (
                RetrievalCandidate(
                    path="tests/test_container.py",
                    start_line=80,
                    end_line=80,
                    symbol="unrelated_test",
                    text="def test_unrelated(): pass",
                    channel=self.channel,
                    channel_rank=1,
                    relation="ASSERTED_BY",
                    provenance=("structural_certified", "action_target:src/errors.py"),
                    source_revision="source-1",
                ),
            )

    result = HybridRetriever((), channels=(StructuralOnly(),)).retrieve(_state())

    assert result.selected_context == ()
    assert result.reason_codes == ("insufficient_independent_support",)


def test_exact_certificate_delivers_exact_span_not_unaligned_structural_representative() -> None:
    class StaticChannel:
        def __init__(self, candidate: RetrievalCandidate) -> None:
            self.channel = candidate.channel
            self.candidate = candidate

        def retrieve(self, state: RetrievalState, *, limit: int):
            del state, limit
            return (self.candidate,)

    exact = replace(
        _candidate("src/errors.py", RetrievalChannel.EXACT, 1, text="class ResolutionError: pass"),
        provenance=("exact_path",),
    )
    unrelated = replace(
        _candidate(
            "src/errors.py",
            RetrievalChannel.STRUCTURAL,
            1,
            text="def unrelated_helper(): pass",
        ),
        relation="CALLS",
        provenance=("structural_certified",),
    )

    result = HybridRetriever(
        (),
        channels=(StaticChannel(exact), StaticChannel(unrelated)),
    ).retrieve(_state(task_text="repair src/errors.py"), selection_limit=1)

    assert result.selected_context == ()
    assert "no_decision_relevant_evidence" in result.reason_codes


def test_rrf_is_equal_weight_k60_and_aggregates_unique_files_deterministically():
    channel_results = {
        RetrievalChannel.LEXICAL: (
            _candidate("src/a.py", RetrievalChannel.LEXICAL, 1),
            _candidate("src/b.py", RetrievalChannel.LEXICAL, 2),
        ),
        RetrievalChannel.DENSE: (
            _candidate("src/b.py", RetrievalChannel.DENSE, 1),
            _candidate("src/a.py", RetrievalChannel.DENSE, 2),
            _candidate("src/a.py", RetrievalChannel.DENSE, 3),
        ),
    }

    ranked = reciprocal_rank_fusion(channel_results, k=60)

    assert [row.path for row in ranked] == ["src/a.py", "src/b.py"]
    assert math.isclose(ranked[0].fused_score, (1 / 61) + (1 / 62))
    assert math.isclose(ranked[1].fused_score, (1 / 62) + (1 / 61))
    assert ranked[0].channel_ranks == (
        (RetrievalChannel.LEXICAL, 1),
        (RetrievalChannel.DENSE, 2),
    )


def test_hybrid_selection_keeps_active_path_spans_but_excludes_prior_claims():
    documents = (
        RepositoryDocument("src/allocator.py", "cleanup allocator current implementation"),
        RepositoryDocument(
            "src/reclaimer.py",
            "allocator cleanup releases reserved storage after use",
        ),
        RepositoryDocument("tests/test_allocator.py", "cleanup allocator regression test"),
    )
    links = (
        StructuralLink(
            source_path="src/allocator.py",
            target_path="src/reclaimer.py",
            relation="calls",
            certified=True,
            target_start_line=1,
        ),
    )
    first = HybridRetriever(
        documents, structural_links=links, dense_backend=FakeDenseBackend()
    ).retrieve(
        _state(
            task_text="repair allocator cleanup in src/reclaimer.py",
            active_paths=("src/allocator.py",),
        ),
        selection_limit=3,
        token_budget=200,
    )
    exposed = first.selected_context[0].claim_hash

    second = HybridRetriever(
        documents, structural_links=links, dense_backend=FakeDenseBackend()
    ).retrieve(
        _state(
            task_text="repair allocator cleanup in src/reclaimer.py",
            active_paths=("src/allocator.py",),
            previously_exposed_claims=(exposed,),
        ),
        selection_limit=3,
        token_budget=200,
    )

    assert "src/allocator.py" in {row.path for row in first.ranked_files}
    assert "src/allocator.py" not in {row.path for row in first.selected_context}
    assert exposed not in {row.claim_hash for row in second.selected_context}
    dense_receipt = next(
        row for row in first.channel_receipts if row.channel is RetrievalChannel.DENSE
    )
    assert dense_receipt.available is True
    assert dense_receipt.backend_identity == "fake-dense-v1"


def test_model_authored_active_file_can_rank_but_cannot_be_delivered_as_context():
    documents = (
        RepositoryDocument(
            "src/allocator.py",
            "def repair_allocator(): return 'model hypothesis'",
            symbol="repair_allocator",
            origin=EvidenceOrigin.MODEL_AUTHORED,
            origin_revision="source-2",
        ),
        RepositoryDocument(
            "src/preexisting.py",
            "def unrelated(): return None",
            symbol="unrelated",
        ),
    )
    result = HybridRetriever(documents).retrieve(
        _state(
            task_text="Fix `repair_allocator` in src/allocator.py",
            active_paths=("src/allocator.py",),
            changed_paths=("src/allocator.py",),
            source_revision="source-2",
        ),
        selection_limit=2,
        token_budget=200,
    )

    assert result.ranked_files[0].path == "src/allocator.py"
    assert all(row.path != "src/allocator.py" for row in result.selected_context)
    assert "model_authored_context_rejected" in result.reason_codes


def test_certified_cross_file_relation_from_active_file_is_deliverable():
    documents = (
        RepositoryDocument(
            "src/allocator.py",
            "def repair_allocator(): pass",
            symbol="repair_allocator",
            origin=EvidenceOrigin.MODEL_AUTHORED,
            origin_revision="source-2",
        ),
        RepositoryDocument(
            "src/reclaimer.py",
            "def release_reserved(): pass",
            symbol="release_reserved",
            origin=EvidenceOrigin.PREEXISTING_REPOSITORY,
            origin_revision="source-1",
        ),
    )
    links = (
        StructuralLink(
            source_path="src/allocator.py",
            target_path="src/reclaimer.py",
            relation="calls",
            certified=True,
            source_symbol="repair_allocator",
            target_symbol="release_reserved",
            confidence=1.0,
        ),
    )
    result = HybridRetriever(documents, structural_links=links).retrieve(
        _state(
            task_text="Fix allocator cleanup",
            active_paths=("src/allocator.py",),
            changed_paths=("src/allocator.py",),
            source_revision="source-2",
        ),
        selection_limit=2,
        token_budget=200,
    )

    assert [row.path for row in result.selected_context] == ["src/reclaimer.py"]
    selected = result.selected_context[0]
    assert selected.origin is EvidenceOrigin.PREEXISTING_REPOSITORY
    assert selected.authority is EvidenceAuthority.CERTIFIED_RELATION


def test_provider_history_source_text_is_not_redelivered():
    documents = (
        RepositoryDocument("src/seed.py", "def seed(): pass", symbol="seed"),
        RepositoryDocument(
            "src/related.py",
            "def related_contract():\n    return 42",
            symbol="related_contract",
        ),
    )
    result = HybridRetriever(
        documents,
        structural_links=(
            StructuralLink(
                source_path="src/seed.py",
                target_path="src/related.py",
                relation="calls",
                certified=True,
                target_symbol="related_contract",
                target_start_line=1,
            ),
        ),
    ).retrieve(
        _state(active_paths=("src/seed.py",)),
        token_budget=200,
    )

    filtered = filter_provider_known_context(
        result,
        [
            {
                "role": "tool",
                "content": "def related_contract():\n    return 42",
            }
        ],
    )

    assert result.selected_context
    assert filtered.selected_context == ()
    assert filtered.abstained is True
    assert "provider_history_already_contains_evidence" in filtered.reason_codes


def test_character_budget_is_applied_before_evidence_is_selected():
    documents = (
        RepositoryDocument("src/seed.py", "def seed(): pass", symbol="seed"),
        RepositoryDocument(
            "src/allocator.py",
            "def release_allocator(value):\n    return value\n",
            start_line=1,
            end_line=2,
            symbol="release_allocator",
        ),
    )
    state = _state(task_text="Change allocator", active_paths=("src/seed.py",))
    links = (
        StructuralLink(
            source_path="src/seed.py",
            target_path="src/allocator.py",
            relation="calls",
            certified=True,
            target_symbol="release_allocator",
            target_start_line=1,
        ),
    )

    result = HybridRetriever(
        documents, structural_links=links, dense_backend=None
    ).retrieve(
        state,
        token_budget=200,
        character_budget=16,
    )

    assert result.selected_context == ()
    assert result.selected_character_count == 0
    assert result.character_budget == 16
    assert "context_character_budget" in result.reason_codes
    assert build_preemptive_frame(result, state, trigger="task_start") is None
    # Candidate discovery was necessary to learn the exact complete-span size,
    # but no evidence may be marked selected and discarded later by the host.
    assert result.channel_receipts


def test_optional_dense_backend_failure_is_fail_open_and_receipted():
    documents = (
        RepositoryDocument("src/allocator.py", "cleanup allocator"),
        RepositoryDocument("src/unrelated.py", "network transport"),
    )
    result = HybridRetriever(documents, dense_backend=BrokenDenseBackend()).retrieve(
        _state(task_text="cleanup allocator"),
        token_budget=200,
    )

    assert result.ranked_files[0].path == "src/allocator.py"
    dense_receipt = next(
        row for row in result.channel_receipts if row.channel is RetrievalChannel.DENSE
    )
    assert dense_receipt.failed is True
    assert dense_receipt.available is False
    assert dense_receipt.candidate_count == 0
    assert "RuntimeError" in dense_receipt.reason


def test_absent_dense_backend_is_a_clean_abstaining_channel_not_an_error():
    result = HybridRetriever(
        (RepositoryDocument("src/allocator.py", "cleanup allocator"),),
        dense_backend=None,
    ).retrieve(_state(), token_budget=200)

    dense_receipt = next(
        row for row in result.channel_receipts if row.channel is RetrievalChannel.DENSE
    )
    assert dense_receipt.failed is False
    assert dense_receipt.available is False
    assert dense_receipt.reason == "backend_unavailable"


def test_all_channels_run_when_non_dense_evidence_is_deliverable():
    backend = CountingDenseBackend()
    documents = (
        RepositoryDocument(
            "src/entry.py",
            "def run(): pass",
            symbol="run",
        ),
        RepositoryDocument(
            "src/allocator.py",
            "def cleanup_allocator(): pass",
            symbol="cleanup_allocator",
        ),
    )

    result = HybridRetriever(
        documents,
        structural_links=(
            StructuralLink(
                "src/entry.py",
                "src/allocator.py",
                "calls",
                certified=True,
                source_symbol="run",
                target_symbol="cleanup_allocator",
            ),
        ),
        dense_backend=backend,
        dense_fallback_only=True,
    ).retrieve(
        _state(task_text="Fix allocator cleanup", active_paths=("src/entry.py",)),
        token_budget=200,
    )

    assert result.selected_context
    assert backend.query_calls == 1
    dense_receipt = next(
        row for row in result.channel_receipts if row.channel is RetrievalChannel.DENSE
    )
    assert dense_receipt.failed is False
    assert dense_receipt.available is True
    assert dense_receipt.reason == ""


def test_sparse_first_uses_dense_when_sparse_evidence_is_not_deliverable():
    class WeakLexicalChannel:
        channel = RetrievalChannel.LEXICAL

        def retrieve(
            self, state: RetrievalState, *, limit: int
        ) -> tuple[RetrievalCandidate, ...]:
            del state, limit
            return (_candidate("src/weak.py", RetrievalChannel.LEXICAL, 1),)

    backend = CountingDenseBackend()
    dense = DenseRetrievalChannel(
        (RepositoryDocument("src/dense.py", "releases reserved storage"),),
        backend,
    )

    result = HybridRetriever(
        (),
        channels=(WeakLexicalChannel(), dense),
        dense_fallback_only=True,
    ).retrieve(_state(task_text="semantic allocator behavior"), token_budget=200)

    assert backend.query_calls == 1
    dense_receipt = next(
        row for row in result.channel_receipts if row.channel is RetrievalChannel.DENSE
    )
    assert dense_receipt.reason == ""


def test_all_channels_run_independent_of_caller_registration_order():
    backend = CountingDenseBackend()
    documents = (
        RepositoryDocument("src/entry.py", "def run(): pass", symbol="run"),
        RepositoryDocument("src/core.py", "def work(): pass", symbol="work"),
    )
    links = (
        StructuralLink(
            "src/entry.py",
            "src/core.py",
            "calls",
            certified=True,
            source_symbol="run",
            target_symbol="work",
        ),
    )

    result = HybridRetriever(
        documents,
        channels=(
            DenseRetrievalChannel(documents, backend),
            ExactRetrievalChannel(documents),
            StructuralRetrievalChannel(documents, links),
        ),
        dense_fallback_only=True,
    ).retrieve(
        _state(task_text="Inspect src/entry.py", active_paths=("src/entry.py",)),
        token_budget=200,
    )

    assert result.selected_context
    assert backend.query_calls == 1
    dense_receipt = next(
        row for row in result.channel_receipts if row.channel is RetrievalChannel.DENSE
    )
    assert dense_receipt.reason == ""


def test_selection_requires_certified_or_multi_channel_support():
    class WeakChannel:
        channel = RetrievalChannel.LEXICAL

        def retrieve(self, state: RetrievalState, *, limit: int) -> tuple[RetrievalCandidate, ...]:
            del state, limit
            return (_candidate("src/weak.py", RetrievalChannel.LEXICAL, 1),)

    result = HybridRetriever((), channels=(WeakChannel(),)).retrieve(_state())

    assert result.ranked_files[0].path == "src/weak.py"
    assert result.selected_context == ()
    assert result.abstained is True
    assert "insufficient_independent_support" in result.reason_codes


def test_lexical_and_bm25_are_one_sparse_family_for_abstention():
    class SparseChannel:
        def __init__(self, channel: RetrievalChannel) -> None:
            self.channel = channel

        def retrieve(self, state: RetrievalState, *, limit: int) -> tuple[RetrievalCandidate, ...]:
            del state, limit
            return (_candidate("src/sparse.py", self.channel, 1),)

    result = HybridRetriever(
        (),
        channels=(
            SparseChannel(RetrievalChannel.LEXICAL),
            SparseChannel(RetrievalChannel.BM25),
        ),
    ).retrieve(_state())

    assert result.ranked_files[0].support_count == 2
    assert result.selected_context == ()
    assert result.reason_codes == ("insufficient_independent_support",)


def test_dense_rerank_of_sparse_candidates_is_not_independent_delivery_support():
    class CandidateChannel:
        def __init__(self, channel: RetrievalChannel) -> None:
            self.channel = channel

        def retrieve(self, state: RetrievalState, *, limit: int) -> tuple[RetrievalCandidate, ...]:
            del state, limit
            return (_candidate("src/candidate.py", self.channel, 1),)

    result = HybridRetriever(
        (),
        channels=(
            CandidateChannel(RetrievalChannel.BM25),
            CandidateChannel(RetrievalChannel.DENSE),
        ),
    ).retrieve(_state())

    assert result.ranked_files[0].path == "src/candidate.py"
    assert result.selected_context == ()
    assert result.abstained is True
    assert result.reason_codes == ("insufficient_independent_support",)


def test_validation_dense_rerank_can_deliver_honest_test_candidate_context():
    class CandidateChannel:
        def __init__(self, channel: RetrievalChannel) -> None:
            self.channel = channel

        def retrieve(self, state: RetrievalState, *, limit: int) -> tuple[RetrievalCandidate, ...]:
            del state, limit
            return (_candidate("tests/test_candidate.py", self.channel, 1),)

    state = _state(intent=RetrievalIntent.VALIDATION_CONTEXT)
    result = HybridRetriever(
        (),
        channels=(
            CandidateChannel(RetrievalChannel.BM25),
            CandidateChannel(RetrievalChannel.DENSE),
        ),
    ).retrieve(state)
    frame = build_preemptive_frame(result, state, trigger="validation_context")

    assert [row.path for row in result.selected_context] == ["tests/test_candidate.py"]
    assert "validation_candidate" in result.selected_context[0].provenance
    assert frame is not None
    assert "Candidate repository context" in frame.rendered_text


def test_diagnostic_delivery_rejects_certified_but_unrelated_structural_relation():
    documents = (
        RepositoryDocument("Makefile", "test:\n\tpytest -q", symbol="test"),
    )
    state = _state(
        intent=RetrievalIntent.DIAGNOSTIC_ROOT_CAUSE,
        active_paths=("tests/test_worker.py",),
        diagnostics=("tests/test_worker.py:1 failed",),
    )
    result = HybridRetriever(
        documents,
        structural_links=(
            StructuralLink(
                "tests/test_worker.py",
                "Makefile",
                "IMPORTS",
                confidence=1.0,
                certified=True,
                source_symbol="test_worker",
                source_start_line=1,
                target_symbol="test",
                target_start_line=1,
            ),
        ),
        dense_backend=None,
    ).retrieve(state, selection_limit=1)

    assert result.selected_context == ()
    assert "no_decision_relevant_evidence" in result.reason_codes


def test_diagnostic_delivery_accepts_direct_certified_test_to_code_relation():
    documents = (
        RepositoryDocument("src/worker.py", "def work(): return 1", symbol="work"),
        RepositoryDocument(
            "tests/test_worker.py",
            "def test_worker(): assert work() == 2",
            symbol="test_worker",
        ),
    )
    state = _state(
        intent=RetrievalIntent.DIAGNOSTIC_ROOT_CAUSE,
        active_paths=("tests/test_worker.py",),
        diagnostics=("tests/test_worker.py:1 AssertionError",),
    )
    result = HybridRetriever(
        documents,
        structural_links=(
            StructuralLink(
                "src/worker.py",
                "tests/test_worker.py",
                "ASSERTED_BY",
                confidence=1.0,
                certified=True,
                source_symbol="work",
                source_start_line=1,
                target_symbol="test_worker",
                target_start_line=1,
            ),
        ),
        dense_backend=None,
    ).retrieve(state, selection_limit=1)

    assert [row.path for row in result.selected_context] == ["src/worker.py"]
    assert "decision_relevance:diagnostic_direct_relation" in (
        result.selected_context[0].provenance
    )


def test_validation_intent_prioritizes_mechanically_recognized_test_paths():
    documents = (
        RepositoryDocument(
            "src/help.py",
            "render help default value regression coverage help default value",
        ),
        RepositoryDocument("tests/test_help.py", "regression"),
    )

    implementation = HybridRetriever(documents, dense_backend=None).retrieve(
        _state(
            task_text="add help default value regression coverage",
            intent=RetrievalIntent.IMPLEMENTATION_CONTEXT,
        )
    )
    validation = HybridRetriever(documents, dense_backend=None).retrieve(
        _state(
            task_text="add help default value regression coverage",
            intent=RetrievalIntent.VALIDATION_CONTEXT,
        )
    )

    assert implementation.ranked_files[0].path == "src/help.py"
    assert validation.ranked_files[0].path == "tests/test_help.py"


def test_retrieval_query_terms_preserve_literal_workflow_vocabulary():
    state = RetrievalState(
        task_text=(
            "Quote empty default values in help output and add a regression test "
            "for default_value_t"
        ),
        intent=RetrievalIntent.VALIDATION_CONTEXT,
        source_revision="source-1",
    )

    terms = retrieval_query_terms(state)

    assert "empty" in terms
    assert "default" in terms
    assert "help" in terms
    assert "default_value_t" in terms
    assert "validation_context" not in terms


def test_sparse_query_terms_do_not_leak_active_path_scaffolding():
    state = RetrievalState(
        task_text="find allocator regression tests",
        intent=RetrievalIntent.VALIDATION_CONTEXT,
        active_paths=("src/allocator.py",),
        changed_paths=("tests/test_allocator.py",),
        source_revision="source-1",
    )

    terms = retrieval_query_terms(state)

    assert "allocator" in terms
    assert "regression" in terms
    assert "src" not in terms
    assert "py" not in terms


def test_stale_revision_candidates_are_rejected_before_fusion():
    class StaleChannel:
        channel = RetrievalChannel.EXACT

        def retrieve(self, state: RetrievalState, *, limit: int) -> tuple[RetrievalCandidate, ...]:
            del state, limit
            return (
                RetrievalCandidate(
                    path="src/stale.py",
                    start_line=1,
                    end_line=2,
                    symbol="stale",
                    text="stale evidence",
                    channel=self.channel,
                    channel_rank=1,
                    relation=None,
                    provenance=("exact_symbol",),
                    source_revision="source-0",
                ),
            )

    result = HybridRetriever((), channels=(StaleChannel(),)).retrieve(_state())

    assert result.ranked_files == ()
    assert result.abstained is True
    assert "stale_candidates_rejected" in result.reason_codes


def test_selection_keeps_complete_evidence_and_never_truncates_to_fit_budget():
    class SupportedChannel:
        channel = RetrievalChannel.STRUCTURAL

        def retrieve(self, state: RetrievalState, *, limit: int) -> tuple[RetrievalCandidate, ...]:
            del state, limit
            return (
                RetrievalCandidate(
                    path="src/large.py",
                    start_line=1,
                    end_line=2,
                    symbol=None,
                    text=" ".join(f"token{i}" for i in range(80)),
                    channel=self.channel,
                    channel_rank=1,
                    relation="calls",
                    provenance=("structural_certified", "edge_endpoint_start:1"),
                    source_revision="source-1",
                    channel_score=1.0,
                ),
            )

    result = HybridRetriever(
        (),
        channels=(SupportedChannel(),),
    ).retrieve(_state(), token_budget=10)

    assert result.selected_context == ()
    assert result.abstained is True
    assert "context_budget" in result.reason_codes


def test_preemptive_frame_is_bounded_revision_bound_and_replayable():
    documents = (
        RepositoryDocument("src/allocator.py", "cleanup allocator implementation"),
        RepositoryDocument("tests/test_allocator.py", "cleanup allocator regression"),
    )
    state = _state(
        task_text="inspect tests/test_allocator.py for cleanup allocator",
        active_paths=("src/allocator.py",),
    )
    result = HybridRetriever(
        documents,
        structural_links=(
            StructuralLink(
                source_path="src/allocator.py",
                target_path="tests/test_allocator.py",
                relation="calls",
                certified=True,
                target_start_line=1,
            ),
        ),
    ).retrieve(state, token_budget=200)

    frame = build_preemptive_frame(result, state, trigger="diagnostic_changed")

    assert frame is not None
    assert frame.source_revision == "source-1"
    assert frame.trigger == "diagnostic_changed"
    assert frame.token_count <= 200
    assert frame.claim_hashes == tuple(row.claim_hash for row in result.selected_context)
    assert frame.query_hash


def test_preemptive_frame_is_none_when_retriever_abstains():
    result = HybridRetriever(()).retrieve(_state())

    assert result.abstained is True
    assert build_preemptive_frame(result, _state(), trigger="task_start") is None


def test_retrieval_status_separates_dense_attempt_from_selected_use():
    result = HybridRetriever(
        (
            RepositoryDocument("src/allocator.py", "allocator implementation"),
            RepositoryDocument("tests/test_allocator.py", "allocator regression"),
        ),
        structural_links=(
            StructuralLink(
                source_path="src/allocator.py",
                target_path="tests/test_allocator.py",
                relation="calls",
                certified=True,
                target_start_line=1,
            ),
        ),
        dense_backend=FakeDenseBackend(),
        dense_fallback_only=True,
    ).retrieve(
        _state(
            intent=RetrievalIntent.VALIDATION_CONTEXT,
            active_paths=("src/allocator.py",),
        ),
        token_budget=200,
    )

    status = result.retrieval_status()
    assert status["expected_mode"] == "dense_fallback_only"
    assert status["dense_channel_present"] is True
    assert status["dense_query_attempted"] is True
    assert status["dense_candidate_count"] >= 0
    assert status["dense_result_used"] is False or status["fallback_used"] is False


def test_retrieval_status_reports_missing_dense_channel_without_fabrication():
    result = HybridRetriever(
        (), channels=(StructuralRetrievalChannel((), ()),), dense_fallback_only=True
    ).retrieve(_state())

    status = result.retrieval_status()
    assert status["dense_channel_present"] is False
    assert status["dense_query_attempted"] is False
    assert status["dense_result_used"] is False
    assert status["fallback_used"] is False
