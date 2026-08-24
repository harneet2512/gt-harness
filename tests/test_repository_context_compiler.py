from __future__ import annotations

from dataclasses import replace

from gt_engine.hybrid_repository import HybridRepository
from gt_engine.hybrid_retrieval import (
    EvidenceOrigin,
    HybridRetriever,
    RepositoryDocument,
    RetrievalIntent,
    StructuralLink,
)
from gt_engine.repository_context_compiler import (
    ContextCompileRequest,
    ContextStatus,
    RepositoryContextCompiler,
    _matching_facet_ids,
    compile_task_facets,
)


def _document(path: str, symbol: str, text: str) -> RepositoryDocument:
    return RepositoryDocument(
        path=path,
        symbol=symbol,
        text=text,
        start_line=1,
        end_line=max(1, len(text.splitlines())),
        provenance=("graph_node",),
        origin=EvidenceOrigin.PREEXISTING_REPOSITORY,
        origin_revision="source-1",
    )


def _repository(*links: StructuralLink) -> HybridRepository:
    return HybridRepository(
        documents=(
            _document(
                "gt_engine/hybrid_retrieval.py",
                "HybridRetriever",
                "class HybridRetriever:\n    def retrieve(self, state): ...",
            ),
            _document(
                "src/groundtruth/pretask/hybrid.py",
                "lexical_file_search",
                "def lexical_file_search(query): ...",
            ),
            _document(
                ".github/workflows/arb_gt_retrieval.yml",
                "arb_gt_retrieval",
                "name: retrieval benchmark",
            ),
            _document(
                "gt_harness/treatments.py",
                "GroundTruthTreatment",
                "class GroundTruthTreatment: ...",
            ),
            _document(
                "tests/test_product_treatments.py",
                "test_groundtruth_treatment",
                "def test_groundtruth_treatment(): ...",
            ),
        ),
        structural_links=links,
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=5,
        document_chars=500,
    )


def _request(task: str) -> ContextCompileRequest:
    return ContextCompileRequest(
        task=task,
        source_revision="source-1",
        graph_revision="graph-1",
        intent=RetrievalIntent.IMPLEMENTATION_CONTEXT,
        token_budget=1_000,
        character_budget=4_000,
    )


def test_compiler_prefers_exact_production_symbol_over_legacy_and_workflow() -> None:
    packet = RepositoryContextCompiler().compile(
        _repository(),
        _request("Wire HybridRetriever into GroundTruthTreatment"),
    )

    assert packet.status is ContextStatus.READY
    assert packet.primary_edit_targets[0].path == "gt_engine/hybrid_retrieval.py"
    assert packet.primary_edit_targets[0].symbol == "HybridRetriever"
    assert all(
        target.path != "src/groundtruth/pretask/hybrid.py"
        for target in packet.primary_edit_targets
    )
    assert all(".github/workflows" not in target.path for target in packet.primary_edit_targets)


def test_compiler_does_not_treat_issue_verbs_as_symbol_anchors() -> None:
    repository = _repository()
    repository = HybridRepository(
        documents=(
            *_repository().documents,
            _document("noise.py", "Change", "class Change: ..."),
            _document("app.py", "answer", "def answer(): return 42"),
        ),
        structural_links=repository.structural_links,
        source_revision=repository.source_revision,
        complete=repository.complete,
        reason_codes=repository.reason_codes,
        source_file_count=repository.source_file_count + 2,
        document_chars=repository.document_chars + 64,
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request("Change `answer` without breaking callers"),
    )

    assert packet.primary_edit_targets[0].symbol == "answer"
    assert all(item.symbol != "Change" for item in packet.primary_edit_targets)


def test_prose_technology_name_is_not_promoted_to_exact_edit_authority() -> None:
    repository = HybridRepository(
        documents=(
            _document("bottle.py", "wsgi", "def wsgi(self, environ, start_response): ..."),
            _document("bottle.py", "_hkey", "def _hkey(key): return key.title()"),
            _document("bottle.py", "_hval", "def _hval(value): return value"),
        ),
        structural_links=(),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=1,
        document_chars=180,
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request(
            "Bottle is a lightweight WSGI web framework. Identify and fix the "
            "vulnerability in /app/bottle.py."
        ),
    )

    assert all(
        not (item.symbol.lower() == "wsgi" and item.decision_reason == "exact_task_symbol")
        for item in packet.primary_edit_targets
    )

    exact_path_packet = RepositoryContextCompiler().compile(
        repository,
        _request("Repair /app/bottle.py"),
    )
    path_target = next(
        item
        for item in exact_path_packet.primary_edit_targets
        if item.decision_reason == "exact_task_path"
    )
    assert path_target.path == "bottle.py"
    assert path_target.kind == "file_identity"
    assert path_target.symbol == ""
    assert path_target.start_line == 1


def test_hybrid_similarity_is_an_inspection_candidate_not_a_verified_edit_target() -> None:
    repository = HybridRepository(
        documents=(
            _document(
                "ranking.py",
                "rank_candidates",
                "def rank_candidates(rows): return semantic_relevance(rows)",
            ),
        ),
        structural_links=(),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=1,
        document_chars=72,
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request("Improve rows returned by semantic relevance ordering"),
    )

    assert packet.primary_edit_targets == ()
    assert packet.inspection_candidates
    assert packet.inspection_candidates[0].kind == "inspection_candidate"
    assert packet.inspection_candidates[0].decision_reason == "hybrid_retrieval_inspection"
    assert packet.inspection_candidates[0].completeness == "ranked_candidate_not_edit_target"


def test_dense_repository_candidate_recovers_file_without_claiming_edit_authority() -> None:
    request = _request("Repair invoice rounding for international billing")
    request = replace(
        request,
        dense_candidates=(("gt_harness/treatments.py", 0.82),),
        dense_index_receipt={"status": "READY", "query_ready": True},
        retrieval_mode="hybrid_required",
    )

    packet = RepositoryContextCompiler().compile(_repository(), request)

    assert packet.primary_edit_targets == ()
    dense = next(
        item
        for item in packet.inspection_candidates
        if item.decision_reason == "dense_semantic_inspection"
    )
    assert dense.path == "gt_harness/treatments.py"
    assert dense.symbol == ""
    assert dense.completeness == "dense_file_candidate_not_edit_target"
    assert packet.coverage["dense_index"]["query_ready"] is True


def test_dense_and_sparse_rankings_are_fused_with_auditable_rrf() -> None:
    repository = HybridRepository(
        documents=(
            _document(
                "shared.py",
                "rank_invoices",
                "def rank_invoices(rows): return international_rounding(rows)",
            ),
            _document(
                "sparse_only.py",
                "round_invoices",
                "def round_invoices(rows): return international_billing(rows)",
            ),
            _document(
                "dense_only.py",
                "helper",
                "def helper(rows): return rows",
            ),
        ),
        structural_links=(),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=3,
        document_chars=200,
    )
    request = replace(
        _request("Improve international invoice rounding and billing relevance"),
        dense_candidates=(("shared.py", 0.91), ("dense_only.py", 0.90)),
        dense_index_receipt={"status": "READY", "query_ready": True},
        retrieval_mode="hybrid_required",
    )

    packet = RepositoryContextCompiler().compile(repository, request)

    assert packet.inspection_candidates[0].path == "shared.py"
    assert packet.inspection_candidates[0].decision_reason == "hybrid_rrf_inspection"
    fusion = packet.coverage["dense_sparse_fusion"]
    assert fusion["method"] == "reciprocal_rank_fusion"
    assert fusion["k"] == 60
    assert fusion["candidate_count"] == 3
    assert fusion["ranked_paths"][0]["path"] == "shared.py"
    assert fusion["ranked_paths"][0]["supporting_channels"] == ["dense", "sparse"]


def test_compiler_abstains_instead_of_sending_generic_symbols_for_unmatched_anchor(
) -> None:
    repository = HybridRepository(
        documents=(
            _document("modifiers.py", "modify", "def modify(value): return value"),
            _document("processor.py", "commit", "def commit(repo): return repo"),
            _document("file_utils.py", "remove", "def remove(path): return path"),
        ),
        structural_links=(),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=3,
        document_chars=120,
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request(
            "Sanitize the git repository by replacing AWS_ACCESS_KEY_ID in history; "
            "do not delete or modify files that are not contaminated"
        ),
    )

    assert packet.status is ContextStatus.ABSTAIN
    assert packet.primary_edit_targets == ()
    assert "concrete_task_anchor_unmatched" in packet.uncertainties


def test_compiler_rejects_unverified_full_confidence_relationship() -> None:
    unsafe = StructuralLink(
        source_path="gt_harness/treatments.py",
        target_path="gt_engine/hybrid_retrieval.py",
        relation="CALLS",
        confidence=1.0,
        certified=True,
        verification_status="unverified",
        source_symbol="GroundTruthTreatment",
        target_symbol="HybridRetriever",
        source_start_line=1,
        target_start_line=1,
        source_content_sha256="a" * 64,
        target_content_sha256="b" * 64,
        source_evidence_origin="preexisting_repository",
        target_evidence_origin="preexisting_repository",
        origin="program",
        resolution_outcome="exact",
        resolution_method="exact_symbol",
        candidate_count=1,
    )

    packet = RepositoryContextCompiler().compile(
        _repository(unsafe),
        _request("Wire HybridRetriever into GroundTruthTreatment"),
    )

    assert packet.execution_paths == ()
    assert packet.change_surface == ()
    assert "unverified_edge_rejected" in packet.uncertainties
    assert all(item.verification_status == "verified" for item in packet.evidence_items)


def test_compiler_ignores_relationships_for_unrelated_symbols_in_anchor_file() -> None:
    unrelated = StructuralLink(
        source_path="gt_engine/hybrid_retrieval.py",
        target_path="gt_harness/treatments.py",
        relation="CALLS",
        confidence=1.0,
        certified=True,
        verification_status="verified",
        source_symbol="retrieval_query_terms",
        target_symbol="BareTreatment",
        source_start_line=10,
        target_start_line=10,
        source_content_sha256="a" * 64,
        target_content_sha256="b" * 64,
        source_evidence_origin="preexisting_repository",
        target_evidence_origin="preexisting_repository",
        origin="program",
        resolution_outcome="exact",
        resolution_method="exact_symbol",
        candidate_count=1,
    )

    packet = RepositoryContextCompiler().compile(
        _repository(unrelated),
        _request("Wire HybridRetriever into GroundTruthTreatment"),
    )

    assert all(item.symbol != "BareTreatment" for item in packet.evidence_items)
    assert packet.execution_paths == ()
    assert packet.change_surface == ()
    assert "unverified_edge_rejected" not in packet.uncertainties


def test_compiler_emits_certified_process_impact_and_affected_test() -> None:
    call = StructuralLink(
        source_path="gt_harness/treatments.py",
        target_path="gt_engine/hybrid_retrieval.py",
        relation="CALLS",
        confidence=1.0,
        certified=True,
        verification_status="verified",
        source_symbol="GroundTruthTreatment",
        target_symbol="HybridRetriever",
        source_start_line=1,
        target_start_line=1,
        source_content_sha256="a" * 64,
        target_content_sha256="b" * 64,
        source_evidence_origin="preexisting_repository",
        target_evidence_origin="preexisting_repository",
        origin="program",
        resolution_outcome="exact",
        resolution_method="exact_symbol",
        candidate_count=1,
    )
    tested_by = StructuralLink(
        source_path="gt_engine/hybrid_retrieval.py",
        target_path="tests/test_product_treatments.py",
        relation="TESTED_BY",
        confidence=1.0,
        certified=True,
        verification_status="verified",
        source_symbol="HybridRetriever",
        target_symbol="test_groundtruth_treatment",
        source_start_line=1,
        target_start_line=1,
        source_content_sha256="b" * 64,
        target_content_sha256="c" * 64,
        source_evidence_origin="preexisting_repository",
        target_evidence_origin="preexisting_repository",
        origin="program",
        resolution_outcome="exact",
        resolution_method="exact_symbol",
        candidate_count=1,
    )

    packet = RepositoryContextCompiler().compile(
        _repository(call, tested_by),
        _request("Wire HybridRetriever into GroundTruthTreatment"),
    )

    assert packet.execution_paths
    assert packet.change_surface
    assert packet.affected_tests == ("tests/test_product_treatments.py",)
    assert all(item.source_revision == "source-1" for item in packet.evidence_items)
    assert all(item.graph_revision == "graph-1" for item in packet.evidence_items)


def test_compiler_deduplicates_transitive_copy_of_direct_relationship() -> None:
    common = {
        "source_path": "gt_harness/treatments.py",
        "target_path": "gt_engine/hybrid_retrieval.py",
        "confidence": 1.0,
        "certified": True,
        "verification_status": "verified",
        "source_symbol": "GroundTruthTreatment",
        "target_symbol": "HybridRetriever",
        "source_start_line": 1,
        "target_start_line": 1,
        "source_content_sha256": "a" * 64,
        "target_content_sha256": "b" * 64,
        "source_evidence_origin": "preexisting_repository",
        "target_evidence_origin": "preexisting_repository",
        "origin": "program",
        "resolution_outcome": "exact",
        "resolution_method": "exact_symbol",
        "candidate_count": 1,
    }
    direct = StructuralLink(relation="CALLS", **common)
    transitive = StructuralLink(relation="CALLS_TRANSITIVE", **common)

    packet = RepositoryContextCompiler().compile(
        _repository(transitive, direct),
        _request("Wire HybridRetriever into GroundTruthTreatment"),
    )

    relationships = [
        item for item in packet.evidence_items if item.kind == "relationship"
    ]
    assert len(relationships) == 1
    assert relationships[0].relation == "CALLS"


def test_compiler_separates_public_surface_from_edit_targets() -> None:
    public_export = StructuralLink(
        source_path="src/awilix.ts",
        target_path="src/container.ts",
        relation="RE_EXPORTS",
        confidence=1.0,
        certified=True,
        verification_status="verified",
        source_symbol="AwilixContainer",
        target_symbol="AwilixContainer",
        source_start_line=1,
        target_start_line=10,
        source_content_sha256="a" * 64,
        target_content_sha256="b" * 64,
        source_evidence_origin="preexisting_repository",
        target_evidence_origin="preexisting_repository",
        origin="program",
        resolution_outcome="exact",
        resolution_method="re_export",
        candidate_count=1,
    )
    repository = HybridRepository(
        documents=(
            _document(
                "src/container.ts",
                "AwilixContainer",
                "export interface AwilixContainer { initialize(): Promise<void> }",
            ),
            _document(
                "src/awilix.ts",
                "AwilixContainer",
                "export { AwilixContainer } from './container'",
            ),
        ),
        structural_links=(public_export,),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=2,
        document_chars=160,
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request("Add `AwilixContainer.initialize` to the public API"),
    )

    assert [item.path for item in packet.inspection_public_surface] == ["src/awilix.ts"]
    assert packet.inspection_public_surface[0].localization_role == "PUBLIC_SURFACE"
    assert all(
        item.path != "src/awilix.ts" for item in packet.primary_edit_targets
    )


def test_task_facets_keep_unresolved_api_and_map_its_existing_owner() -> None:
    documents = (
        _document(
            "core/engine/src/evaluation.rs",
            "EvaluationHandle",
            "pub struct EvaluationHandle { cancelled: bool }",
        ),
        _document(
            "core/engine/src/job.rs",
            "run_jobs",
            "pub fn run_jobs(context: &mut Context) {}",
        ),
    )

    facets = compile_task_facets(
        "Add `EvaluationHandle::cancel_with_reason` and "
        "`run_jobs_with_evaluation` while preserving `run_jobs`.",
        documents,
    )

    unresolved = {symbol for facet in facets for symbol in facet.unresolved_symbols}
    owners = {symbol for facet in facets for symbol in facet.owning_symbols}
    exact = {symbol for facet in facets for symbol in facet.exact_symbols}
    assert "EvaluationHandle::cancel_with_reason" in unresolved
    assert "run_jobs_with_evaluation" in unresolved
    assert "EvaluationHandle" in owners
    assert "run_jobs" in exact


def test_task_facets_capture_unquoted_case_significant_symbols() -> None:
    facets = compile_task_facets(
        "Wire HybridRetriever into GroundTruthTreatment",
        _repository().documents,
    )

    exact = {symbol for facet in facets for symbol in facet.exact_symbols}
    assert {"HybridRetriever", "GroundTruthTreatment"} <= exact


def test_task_facets_keep_code_bearing_public_capability_paragraphs() -> None:
    documents = (
        _document("core/engine/src/context/mod.rs", "Context", "pub struct Context;"),
        _document("core/engine/src/context/mod.rs", "run_jobs", "pub fn run_jobs() {}"),
        _document("core/engine/src/script.rs", "Script", "pub struct Script;"),
        _document("core/engine/src/script.rs", "evaluate", "pub fn evaluate() {}"),
        _document("core/ast/src/source.rs", "Script", "pub struct Script;"),
        _document("core/ast/src/source.rs", "Module", "pub struct Module;"),
        _document("core/ast/src/source.rs", "evaluate", "pub fn evaluate() {}"),
    )

    facets = compile_task_facets(
        """Implement evaluation cancellation.

Public capabilities include
`Context::{run_jobs_with_evaluation, new_evaluation_handle}` and
`Script::evaluate_with_evaluation`.

Cancellation must be first-wins.""",
        documents,
    )

    unresolved = {symbol for facet in facets for symbol in facet.unresolved_symbols}
    owners = {symbol for facet in facets for symbol in facet.owning_symbols}
    exact = {symbol for facet in facets for symbol in facet.exact_symbols}
    roles = {facet.role.value for facet in facets if facet.owning_symbols}
    assert "Context::run_jobs_with_evaluation" in unresolved
    assert "Context::new_evaluation_handle" in unresolved
    assert "Script::evaluate_with_evaluation" in unresolved
    assert {"Context", "Script"} <= owners
    assert {"run_jobs", "evaluate"} <= exact
    assert "PUBLIC_SURFACE" in roles


def test_task_facets_split_multi_owner_api_surface_for_bounded_set_cover() -> None:
    documents = (
        _document("core/engine/src/context/mod.rs", "Context", "pub struct Context;"),
        _document("core/engine/src/context/mod.rs", "run_jobs", "pub fn run_jobs() {}"),
        _document("core/engine/src/module/mod.rs", "Module", "pub struct Module;"),
        _document(
            "core/engine/src/module/mod.rs",
            "load_link_evaluate",
            "pub fn load_link_evaluate() {}",
        ),
        _document("core/engine/src/script.rs", "Script", "pub struct Script;"),
        _document("core/engine/src/script.rs", "evaluate", "pub fn evaluate() {}"),
    )

    facets = compile_task_facets(
        "Public capabilities include "
        "`Context::run_jobs_with_evaluation`, "
        "`Module::load_link_evaluate_with_evaluation`, and "
        "`Script::evaluate_with_evaluation`.",
        documents,
    )

    owner_facets = {
        facet.owning_symbols[0]: facet
        for facet in facets
        if len(facet.owning_symbols) == 1
        and facet.owning_symbols[0] in {"Context", "Module", "Script"}
    }
    assert set(owner_facets) == {"Context", "Module", "Script"}
    assert owner_facets["Context"].exact_symbols == ("run_jobs",)
    assert owner_facets["Module"].exact_symbols == ("load_link_evaluate",)
    assert owner_facets["Script"].exact_symbols == ("evaluate",)


def test_owner_scoped_exact_analogs_survive_global_retrieval_crowding() -> None:
    documents = (
        _document("core/engine/src/context/mod.rs", "Context", "pub struct Context;"),
        _document("core/engine/src/context/mod.rs", "run_jobs", "pub fn run_jobs() {}"),
        _document("core/engine/src/module/mod.rs", "Module", "pub struct Module;"),
        _document(
            "core/engine/src/module/mod.rs",
            "load_link_evaluate",
            "pub fn load_link_evaluate() {}",
        ),
        _document("core/engine/src/script.rs", "Script", "pub struct Script;"),
        _document("core/engine/src/script.rs", "evaluate", "pub fn evaluate() {}"),
        *tuple(
            _document(
                f"aaa/noise_{index:03d}.rs",
                "evaluate",
                "pub fn evaluate() {}",
            )
            for index in range(160)
        ),
    )
    repository = HybridRepository(
        documents=documents,
        structural_links=(),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=len(documents),
        document_chars=sum(len(document.text) for document in documents),
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request(
            "Add `Context::run_jobs_with_evaluation`, "
            "`Script::evaluate_with_evaluation`, and "
            "`Module::load_link_evaluate_with_evaluation`."
        ),
    )

    assert {item.path for item in packet.primary_edit_targets} == {
        "core/engine/src/context/mod.rs",
        "core/engine/src/module/mod.rs",
        "core/engine/src/script.rs",
    }


def test_owner_scoped_exact_facts_are_seeded_when_rank_window_omits_them(
    monkeypatch,
) -> None:
    documents = (
        _document("core/engine/src/context/mod.rs", "Context", "pub struct Context;"),
        _document("core/engine/src/context/mod.rs", "run_jobs", "pub fn run_jobs() {}"),
        _document("core/engine/src/module/mod.rs", "Module", "pub struct Module;"),
        _document(
            "core/engine/src/module/mod.rs",
            "load_link_evaluate",
            "pub fn load_link_evaluate() {}",
        ),
        _document("core/engine/src/script.rs", "Script", "pub struct Script;"),
        _document("core/engine/src/script.rs", "evaluate", "pub fn evaluate() {}"),
    )
    original_retrieve = HybridRetriever.retrieve

    def crowded_retrieve(self, state, **kwargs):
        result = original_retrieve(self, state, **kwargs)
        visible = tuple(
            row
            for row in result.ranked_files
            if row.path == "core/engine/src/module/mod.rs"
        )
        return replace(
            result,
            ranked_files=visible,
            ranked_spans=tuple(row.representative for row in visible),
        )

    monkeypatch.setattr(HybridRetriever, "retrieve", crowded_retrieve)
    repository = HybridRepository(
        documents=documents,
        structural_links=(),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=3,
        document_chars=sum(len(document.text) for document in documents),
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request(
            "Add `Context::run_jobs_with_evaluation`, "
            "`Script::evaluate_with_evaluation`, and "
            "`Module::load_link_evaluate_with_evaluation`."
        ),
    )

    assert {item.path for item in packet.primary_edit_targets} == {
        "core/engine/src/context/mod.rs",
        "core/engine/src/module/mod.rs",
        "core/engine/src/script.rs",
    }


def test_file_location_cannot_substitute_for_owner_symbol_match() -> None:
    documents = (
        _document("core/ast/src/source.rs", "Module", "pub struct Module;"),
        _document("core/ast/src/source.rs", "Script", "pub struct Script;"),
        _document("core/ast/src/source.rs", "evaluate", "pub fn evaluate() {}"),
    )
    facets = compile_task_facets(
        "Add `Script::evaluate_with_evaluation`.",
        documents,
    )
    script_facet = next(
        facet
        for facet in facets
        if "Script::evaluate_with_evaluation" in facet.unresolved_symbols
    )

    matched = _matching_facet_ids(
        symbol="Module",
        path="core/ast/src/source.rs",
        facets=facets,
    )

    assert script_facet.facet_id not in matched


def test_qualified_owner_prefers_case_exact_type_over_lowercase_namesakes() -> None:
    documents = (
        _document("core/engine/src/context/mod.rs", "Context", "pub struct Context;"),
        _document("core/engine/src/context/mod.rs", "run_jobs", "pub fn run_jobs() {}"),
        _document("core/parser/src/error/mod.rs", "context", "fn context() {}"),
        _document("core/runtime/src/state.rs", "context", "fn context() {}"),
    )

    facets = compile_task_facets(
        "Add `Context::run_jobs_with_evaluation`.",
        documents,
    )
    scoped = next(
        facet
        for facet in facets
        if "Context::run_jobs_with_evaluation" in facet.unresolved_symbols
    )

    assert scoped.owning_symbols == ("Context",)
    assert scoped.owning_modules == ("core/engine/src/context/mod.rs",)


def test_qualified_unresolved_owner_does_not_claim_unrelated_leaf_symbol() -> None:
    documents = (
        _document("core/runtime/src/abort/mod.rs", "is_cancelled", "pub fn is_cancelled()"),
        _document("core/runtime/src/abort/mod.rs", "cancel", "pub fn cancel()"),
        _document("core/engine/src/context/mod.rs", "Context", "pub struct Context;"),
    )

    facets = compile_task_facets(
        "Add `EvaluationHandle::is_cancelled` and "
        "`EvaluationHandle::cancel_with_reason` to Context.",
        documents,
    )

    exact = {symbol for facet in facets for symbol in facet.exact_symbols}
    unresolved = {symbol for facet in facets for symbol in facet.unresolved_symbols}
    assert "is_cancelled" not in exact
    assert "cancel" not in exact
    assert "EvaluationHandle::is_cancelled" in unresolved


def test_compiler_rejects_same_named_member_outside_unresolved_owner_scope() -> None:
    repository = HybridRepository(
        documents=(
            _document(
                "core/runtime/src/abort/mod.rs",
                "is_cancelled",
                "pub fn is_cancelled() -> bool { false }",
            ),
            _document(
                "core/engine/src/context/mod.rs",
                "Context",
                "pub struct Context; pub fn run_jobs() {}",
            ),
            _document(
                "core/engine/src/context/mod.rs",
                "run_jobs",
                "pub fn run_jobs() {}",
            ),
            _document(
                "core/engine/src/context/mod.rs",
                "enqueue_job",
                "pub fn enqueue_job() {}",
            ),
            _document(
                "examples/src/bin/event_loop.rs",
                "enqueue_job",
                "pub fn enqueue_job() {}",
            ),
        ),
        structural_links=(),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=2,
        document_chars=160,
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request(
            "Add `EvaluationHandle::is_cancelled` and "
            "`Context::run_jobs_with_evaluation` and "
            "`Context::enqueue_job_with_evaluation`."
        ),
    )

    assert all(
        item.path
        not in {
            "core/runtime/src/abort/mod.rs",
            "examples/src/bin/event_loop.rs",
        }
        for item in packet.primary_edit_targets
    )
    assert any(
        item.path == "core/engine/src/context/mod.rs"
        for item in packet.primary_edit_targets
    )


def test_qualified_api_context_prevents_unqualified_clarification_from_global_binding() -> None:
    documents = (
        _document(
            "core/runtime/src/abort/mod.rs",
            "cancel",
            "pub fn cancel() -> bool { true }",
        ),
        _document(
            "core/runtime/src/abort/mod.rs",
            "is_cancelled",
            "pub fn is_cancelled() -> bool { false }",
        ),
        _document(
            "core/engine/src/context/mod.rs",
            "Context",
            "pub struct Context;",
        ),
    )
    task = (
        "Public capabilities include "
        "`EvaluationHandle::{cancel, cancel_with_reason, is_cancelled}`. "
        "The `cancel` call must be first-wins, and `cancel_with_reason` "
        "must preserve that result."
    )

    facets = compile_task_facets(task, documents)
    packet = RepositoryContextCompiler().compile(
        HybridRepository(
            documents=documents,
            structural_links=(),
            source_revision="source-1",
            complete=True,
            reason_codes=(),
            source_file_count=2,
            document_chars=160,
        ),
        _request(task),
    )

    assert all("cancel" not in facet.exact_symbols for facet in facets)
    assert all("is_cancelled" not in facet.exact_symbols for facet in facets)
    assert all(
        item.path != "core/runtime/src/abort/mod.rs"
        for item in packet.primary_edit_targets
    )


def test_compiler_separates_integration_callers_from_edit_targets() -> None:
    call = StructuralLink(
        source_path="src/job.rs",
        target_path="src/evaluation.rs",
        relation="CALLS",
        confidence=1.0,
        certified=True,
        verification_status="verified",
        source_symbol="run_jobs",
        target_symbol="evaluate",
        source_start_line=20,
        target_start_line=5,
        source_content_sha256="a" * 64,
        target_content_sha256="b" * 64,
        source_evidence_origin="preexisting_repository",
        target_evidence_origin="preexisting_repository",
        origin="program",
        resolution_outcome="exact",
        resolution_method="exact_symbol",
        candidate_count=1,
    )
    repository = HybridRepository(
        documents=(
            _document("src/evaluation.rs", "evaluate", "pub fn evaluate() {}"),
            _document("src/job.rs", "run_jobs", "pub fn run_jobs() { evaluate(); }"),
        ),
        structural_links=(call,),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=2,
        document_chars=96,
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request("Change `evaluate` cancellation behavior"),
    )

    assert [item.path for item in packet.inspection_integration] == ["src/job.rs"]
    assert packet.inspection_integration[0].localization_role == "INTEGRATION"
    assert all(item.path != "src/job.rs" for item in packet.primary_edit_targets)


def test_public_surface_survives_high_fan_in_relationship_budget() -> None:
    links = tuple(
        StructuralLink(
            source_path=f"examples/example_{index}.ts",
            target_path="src/container.ts",
            relation="CALLS",
            confidence=1.0,
            certified=True,
            verification_status="verified",
            source_symbol=f"example{index}",
            target_symbol="register",
            source_start_line=1,
            target_start_line=5,
            source_content_sha256=f"{index + 1:x}" * 64,
            target_content_sha256="a" * 64,
            source_evidence_origin="preexisting_repository",
            target_evidence_origin="preexisting_repository",
            origin="program",
            resolution_outcome="exact",
            resolution_method="exact_symbol",
            candidate_count=1,
        )
        for index in range(8)
    )
    public = StructuralLink(
        source_path="src/awilix.ts",
        target_path="src/container.ts",
        relation="RE_EXPORTS",
        confidence=1.0,
        certified=True,
        verification_status="verified",
        source_symbol="register",
        target_symbol="register",
        source_start_line=1,
        target_start_line=5,
        source_content_sha256="b" * 64,
        target_content_sha256="a" * 64,
        source_evidence_origin="preexisting_repository",
        target_evidence_origin="preexisting_repository",
        origin="program",
        resolution_outcome="exact",
        resolution_method="exact_symbol",
        candidate_count=1,
    )
    documents = (
        _document("src/container.ts", "register", "export function register() {}"),
        _document("src/awilix.ts", "register", "export { register } from './container'"),
        *(
            _document(
                f"examples/example_{index}.ts",
                f"example{index}",
                "register()",
            )
            for index in range(8)
        ),
    )
    repository = HybridRepository(
        documents=documents,
        structural_links=(*links, public),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=len(documents),
        document_chars=500,
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request("Change `register` and preserve the public API"),
    )

    assert [item.path for item in packet.inspection_public_surface] == ["src/awilix.ts"]


def test_compiler_marks_greenfield_rust_file_as_proposal_not_repository_fact() -> None:
    repository = HybridRepository(
        documents=(
            _document(
                "core/engine/src/job.rs",
                "run_jobs",
                "pub fn run_jobs(context: &mut Context) {}",
            ),
            _document(
                "core/engine/src/lib.rs",
                "Context",
                "pub struct Context; pub mod job;",
            ),
        ),
        structural_links=(),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=2,
        document_chars=120,
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request(
            "Add `EvaluationHandle::cancel_with_reason` and "
            "`run_jobs_with_evaluation` while preserving `run_jobs`."
        ),
    )

    assert packet.proposed_new_files == ("core/engine/src/evaluation.rs",)
    assert all(
        item.path != "core/engine/src/evaluation.rs"
        for item in packet.evidence_items
    )
    assert packet.uncovered_facets
