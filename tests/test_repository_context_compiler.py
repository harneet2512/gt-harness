from __future__ import annotations

import hashlib
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
    ContextEvidenceItem,
    ContextStatus,
    FacetCoverageStatus,
    LocalizationRole,
    RepositoryContextCompiler,
    RequirementCoverageStatus,
    RequirementIntent,
    _matching_facet_ids,
    _owner_path_scope_affinity,
    _owner_priority_key,
    compile_task_facets,
)
from gt_engine.task_contract import extract_task_contract


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


def _safe_self_link(document: RepositoryDocument) -> StructuralLink:
    digest = hashlib.sha256(document.text.encode("utf-8")).hexdigest()
    return StructuralLink(
        source_path=document.path,
        target_path=document.path,
        relation="REFERENCES",
        confidence=1.0,
        certified=True,
        verification_status="verified",
        source_symbol=document.symbol,
        target_symbol=document.symbol,
        source_start_line=document.start_line,
        target_start_line=document.start_line,
        source_content_sha256=digest,
        target_content_sha256=digest,
        source_evidence_origin="preexisting_repository",
        target_evidence_origin="preexisting_repository",
        origin="program",
        resolution_outcome="exact",
        resolution_method="exact_symbol",
        candidate_count=1,
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
        target.path != "src/groundtruth/pretask/hybrid.py" for target in packet.primary_edit_targets
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
        source_file_count=5,
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


def test_compiler_abstains_instead_of_sending_generic_symbols_for_unmatched_anchor() -> None:
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

    relationships = [item for item in packet.evidence_items if item.kind == "relationship"]
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
    assert all(item.path != "src/awilix.ts" for item in packet.primary_edit_targets)
    public_requirements = {
        item.requirement_id: item
        for item in packet.task_requirements
        if item.intent.value == "INSPECT_PUBLIC_SURFACE"
    }
    coverage = {item.requirement_id: item for item in packet.requirement_coverage}
    assert public_requirements
    assert all(
        coverage[identifier].status is RequirementCoverageStatus.COVERED
        and coverage[identifier].mechanism == "PUBLIC_SURFACE"
        for identifier in public_requirements
    )


def test_export_only_requirement_does_not_make_definition_an_edit_target() -> None:
    public_export = StructuralLink(
        source_path="src/index.ts",
        target_path="src/answer.ts",
        relation="RE_EXPORTS",
        confidence=1.0,
        certified=True,
        verification_status="verified",
        source_symbol="answer",
        target_symbol="answer",
        source_start_line=1,
        target_start_line=1,
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
            _document("src/answer.ts", "answer", "export const answer = 42"),
            _document("src/index.ts", "answer", "export { answer } from './answer'"),
        ),
        structural_links=(public_export,),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=2,
        document_chars=80,
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request("Export `answer` from the public API."),
    )

    assert packet.primary_edit_targets == ()
    assert [item.path for item in packet.inspection_public_surface] == ["src/index.ts"]


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


def test_test_subject_is_not_misclassified_as_validation_work() -> None:
    documents = (
        _document(
            "src/runner.py",
            "execute_task",
            "def execute_task():\n    return run_test_process()",
        ),
    )

    facets = compile_task_facets(
        "Fix the test runner timeout inside `execute_task`.",
        documents,
    )

    matching = [facet for facet in facets if "execute_task" in facet.exact_symbols]
    assert matching
    assert all(facet.role is LocalizationRole.EDIT for facet in matching)


def test_quoted_configuration_literal_is_not_promoted_to_exact_symbol() -> None:
    documents = (
        _document("src/parser.py", "parse_options", "def parse_options(): pass"),
        _document("src/noise.py", "strict", "def strict(): pass"),
    )

    facets = compile_task_facets(
        "Set the parser mode to `strict` in `parse_options`.",
        documents,
    )

    exact = {symbol for facet in facets for symbol in facet.exact_symbols}
    assert "parse_options" in exact
    assert "strict" not in exact


def test_dependency_type_is_not_granted_edit_authority() -> None:
    documents = (
        _document(
            "src/adaptix/_internal/morphing/facade/provider.py",
            "name_mapping",
            "def name_mapping(*, alias_style=None): pass",
        ),
        _document(
            "src/adaptix/_internal/name_style.py",
            "NameStyle",
            "class NameStyle: pass",
        ),
    )
    task = (
        "`name_mapping` gains a new optional keyword argument `alias_style` "
        "(`NameStyle` value or values, default None)."
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

    authoritative = {symbol for facet in facets for symbol in facet.edit_symbols}
    assert "name_mapping" in authoritative
    assert "NameStyle" not in authoritative
    assert all(
        item.path != "src/adaptix/_internal/name_style.py" for item in packet.primary_edit_targets
    )


def test_explicit_callable_resolves_unique_host_adapter_function() -> None:
    documents = (
        _document("runtime/builtins.go", "loadFn", "func loadFn() {}"),
        _document("runtime/module.go", "module", "func module() {}"),
    )
    task = "Improve `load()` so module resolution is deterministic."

    packet = RepositoryContextCompiler().compile(
        HybridRepository(
            documents=documents,
            structural_links=(),
            source_revision="source-1",
            complete=True,
            reason_codes=(),
            source_file_count=2,
            document_chars=80,
        ),
        _request(task),
    )

    assert any(
        item.path == "runtime/builtins.go" and item.symbol == "loadFn"
        for item in packet.primary_edit_targets
    )


def test_preservation_clause_cannot_grant_edit_authority_to_named_symbol() -> None:
    documents = (
        _document("src/mapping.py", "name_mapping", "def name_mapping(): pass"),
        _document("src/name_style.py", "NameStyle", "class NameStyle: pass"),
    )
    task = (
        "Fix `name_mapping` so aliases are deterministic. "
        "The behavior must remain unaffected by `NameStyle`."
    )

    facets = compile_task_facets(task, documents)
    edit_symbols = {symbol for facet in facets for symbol in facet.edit_symbols}

    assert "name_mapping" in edit_symbols
    assert "NameStyle" not in edit_symbols


def test_value_kind_phrase_cannot_manufacture_generic_error_identity() -> None:
    documents = (
        _document("src/actions.py", "pin_action", "def pin_action(): pass"),
        _document("src/errors.py", "Error", "class Error: pass"),
    )
    task = "Fix `pin_action` and preserve the error kind used for action-pinning."

    facets = compile_task_facets(task, documents)
    named = {symbol for facet in facets for symbol in facet.exact_symbols}
    edit_symbols = {symbol for facet in facets for symbol in facet.edit_symbols}

    assert "pin_action" in edit_symbols
    assert "Error" not in named


def test_capitalized_generic_phrase_head_cannot_manufacture_error_identity() -> None:
    documents = (
        _document("error.go", "Error", "type Error struct {}"),
        _document("expr.go", "Error", "func (e *ExprError) Error() string"),
        _document("glob.go", "Error", "func (e *GlobError) Error() string"),
        _document("rule.go", "Error", "func (r *RuleBase) Error()"),
    )
    task = "Error messages should distinguish reusable workflows from step actions."

    facets = compile_task_facets(task, documents)
    named = {symbol for facet in facets for symbol in facet.exact_symbols}

    assert "Error" not in named


def test_prose_new_and_schema_words_do_not_mint_edit_authority() -> None:
    documents = (
        _document("lexer/lexer.go", "New", "func New() *Lexer { return &Lexer{} }"),
        _document("provider/overlay_schema.py", "Schema", "class Schema: pass"),
        _document("parser/parser.go", "Parser", "type Parser struct {}"),
    )
    task = (
        "New stepped range behavior must preserve existing indexing. "
        "Input JSON Schema exposes aliases as additional typed properties."
    )

    packet = RepositoryContextCompiler().compile(
        HybridRepository(
            documents=documents,
            structural_links=(),
            source_revision="source-1",
            complete=True,
            reason_codes=(),
            source_file_count=len(documents),
            document_chars=160,
        ),
        _request(task),
    )

    assert not packet.primary_edit_targets


def test_obligation_bound_dense_hit_is_typed_inspection_owner_not_edit() -> None:
    documents = (
        _document(
            "bandit/core/manager.py",
            "BanditManager",
            "class BanditManager:\n    def discover_files(self): pass",
        ),
        _document("bandit/plugins/noise.py", "noise", "def noise(): pass"),
    )
    task = "Unchanged files must return cached results during incremental analysis."
    base_request = _request(task)
    obligation_id = extract_task_contract(task).obligations[0].obligation_id
    request = replace(
        base_request,
        dense_candidates=(("bandit/core/manager.py", 0.92),),
        dense_candidate_requirements=(
            ("bandit/core/manager.py", (obligation_id,)),
        ),
        dense_index_receipt={"status": "READY", "query_ready": True},
        retrieval_mode="hybrid_required",
    )

    packet = RepositoryContextCompiler().compile(
        HybridRepository(
            documents=documents,
            structural_links=(),
            source_revision="source-1",
            complete=True,
            reason_codes=(),
            source_file_count=len(documents),
            document_chars=120,
        ),
        request,
    )

    owner = next(
        item
        for item in packet.inspection_implementation_owners
        if item.path == "bandit/core/manager.py"
    )
    assert owner.decision_reason == "dense_semantic_implementation_owner_candidate"
    assert owner.facet_ids
    assert packet.primary_edit_targets == ()


def test_unquoted_pascal_case_behavior_subjects_bind_existing_repository_types() -> None:
    documents = (
        _document("lib/config.js", "Config", "class Config {}"),
        _document("lib/launcher.js", "Launcher", "class Launcher {}"),
        _document("lib/utils/report-file.js", "ReportFile", "class ReportFile {}"),
        _document("lib/utils/reporter.js", "reporter", "module file"),
        _document("lib/utils/reporter.js", "Reporter", "class Reporter {}"),
        _document("examples/demo.js", "Reporter", "function Reporter() {}"),
        _document("tests/reporter_tests.js", "Reporter", "function Reporter() {}"),
    )
    task = (
        "The Reporter constructor validates this config and Reporter must partition output. "
        "Config adds template validation. Launcher adds getSanitizedName(). "
        "ReportFile constructor accepts expansion options."
    )

    packet = RepositoryContextCompiler().compile(
        HybridRepository(
            documents=documents,
            structural_links=(),
            source_revision="source-1",
            complete=True,
            reason_codes=(),
            source_file_count=len(documents),
            document_chars=240,
        ),
        _request(task),
    )

    assert packet.primary_edit_targets
    assert {item.path for item in packet.primary_edit_targets}.issubset(
        {
            "lib/config.js",
            "lib/launcher.js",
            "lib/utils/report-file.js",
            "lib/utils/reporter.js",
        }
    )
    assert any(item.path == "lib/utils/reporter.js" for item in packet.primary_edit_targets)
    assert any(item.symbol == "Reporter" for item in packet.primary_edit_targets)
    assert all(
        not item.path.startswith(("examples/", "tests/"))
        for item in packet.primary_edit_targets
    )


def test_symbol_sentence_after_contract_obligation_is_not_discarded_with_paragraph() -> None:
    documents = (
        _document("lib/config.js", "Config", "class Config {}"),
        _document("lib/utils/reporter.js", "Reporter", "class Reporter {}"),
    )
    task = (
        "Add bail_on_test_failure to config defaults. "
        "The Reporter constructor validates this config and Reporter must bail."
    )

    facets = compile_task_facets(task, documents)

    assert any("Reporter" in facet.exact_symbols for facet in facets)


def test_unresolved_qualified_member_keeps_owner_as_inspection_not_edit_authority() -> None:
    documents = (
        _document("lib/app.js", "App", "class App {}"),
        _document("lib/server/index.js", "Server", "class Server {}"),
    )
    task = (
        "App exposes resetBailState and resets the server via Server.resetAbort(), "
        "which does not exist yet."
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
            document_chars=80,
        ),
        _request(task),
    )

    assert all("Server" not in facet.edit_symbols for facet in facets)
    assert all(item.path != "lib/server/index.js" for item in packet.primary_edit_targets)
    assert any(
        item.path == "lib/server/index.js"
        for item in packet.inspection_implementation_owners
    )


def test_declared_domain_artifact_localizes_generic_named_owner_module() -> None:
    documents = (
        _document("katex.ts", "katex", "export const katex = {}"),
        _document(
            "src/functions/environment.ts",
            "environment",
            "export const environment = {}",
        ),
        _document("src/mathMLTree.ts", "mathMLTree", "export const mathMLTree = {}"),
        _document("src/environments/array.ts", "parseArray", "function parseArray() {}"),
        _document("src/buildMathML.ts", "buildMathML", "function buildMathML() {}"),
    )
    task = (
        "Add multicolumn support outside array-like environments. "
        "Supported environments: array, matrix, pmatrix, bmatrix. "
        "For MathML output, add columnspan and columnalign attributes."
    )

    packet = RepositoryContextCompiler().compile(
        HybridRepository(
            documents=documents,
            structural_links=tuple(_safe_self_link(document) for document in documents[:3]),
            source_revision="source-1",
            complete=True,
            reason_codes=(),
            source_file_count=len(documents),
            document_chars=240,
        ),
        _request(task),
    )

    assert packet.inspection_implementation_owners[0].path == "src/environments/array.ts"


def test_direct_owner_identity_outranks_partial_compound_path_match() -> None:
    documents = (
        _document(
            "lib/definition-syntax/SyntaxError.js",
            "SyntaxError",
            "export class SyntaxError {}",
        ),
        _document("lib/lexer/Lexer.js", "Lexer", "export class Lexer {}"),
        _document("lib/lexer/match.js", "internalMatch", "export function internalMatch() {}"),
    )
    task = (
        "Add two methods to the lexer: `expandShorthand(propertyName, value)` and "
        "`compressShorthand(propertyName, longhands)`. Each shorthand uses CSS syntax."
    )

    packet = RepositoryContextCompiler().compile(
        HybridRepository(
            documents=documents,
            structural_links=(_safe_self_link(documents[0]),),
            source_revision="source-1",
            complete=True,
            reason_codes=(),
            source_file_count=len(documents),
            document_chars=180,
        ),
        _request(task),
    )

    assert packet.inspection_implementation_owners[0].path == "lib/lexer/Lexer.js"


def test_direct_owner_identity_outranks_broad_facet_cover() -> None:
    documents = (
        _document(
            "frontend/src/components/native/AgentHubPage.tsx",
            "AgentHubPage",
            "export function AgentHubPage() {}",
        ),
        _document(
            "backend/handlers/multiAgentChat.ts",
            "multiAgentChat",
            "export async function multiAgentChat() {}",
        ),
        _document(
            "backend/handlers/agentConversations.ts",
            "agentConversations",
            "export async function agentConversations() {}",
        ),
        _document(
            "ClaudeAgentHub/ClaudeAgentHub/Models/ChatMessage.swift",
            "ChatMessageType",
            "enum ChatMessageType {}",
        ),
    )
    task = (
        "Implement recursive agent delegation in the multi-agent chat flow. "
        "Handle unknown agents, sub-agent failures, and circular delegation; "
        "follow existing handler and registry patterns.\n\n"
        "Contract: delegation is triggered by delegate_task with agent_id and "
        "instructions. Feed one tool_result back so the conversation continues. "
        "Unknown agents emit an error; circular delegation must be rejected."
    )

    packet = RepositoryContextCompiler().compile(
        HybridRepository(
            documents=documents,
            structural_links=(),
            source_revision="source-1",
            complete=True,
            reason_codes=(),
            source_file_count=len(documents),
            document_chars=180,
        ),
        _request(task),
    )

    assert (
        packet.inspection_implementation_owners[0].path
        == "backend/handlers/multiAgentChat.ts"
    )


def test_distant_generic_words_do_not_create_scoped_path_owner() -> None:
    documents = (
        _document("src/cli.rs", "Opts", "pub struct Opts;"),
        _document("src/filter/size.rs", "size", "pub fn size() {}"),
        _document("src/filter/time.rs", "time", "pub fn time() {}"),
    )
    task = (
        "Add deterministic multi-key sorting. Support size and modified-time "
        "sort keys.\n\nKeep existing filtering semantics, ignore handling, "
        "and pattern matching unchanged."
    )

    packet = RepositoryContextCompiler().compile(
        HybridRepository(
            documents=documents,
            structural_links=(),
            source_revision="source-1",
            complete=True,
            reason_codes=(),
            source_file_count=len(documents),
            document_chars=120,
        ),
        _request(task),
    )

    assert all(
        item.path not in {"src/filter/size.rs", "src/filter/time.rs"}
        for item in packet.inspection_implementation_owners
    )


def test_repeated_generic_output_noun_does_not_poison_owner_ranking() -> None:
    item = ContextEvidenceItem(
        kind="inspection_candidate",
        path="src/output.rs",
        start_line=1,
        end_line=1,
        symbol="output",
        relation="",
        confidence=None,
        verification_status="rank_only",
        source_revision="source-1",
        graph_revision="graph-1",
        evidence_sha256="a" * 64,
        decision_reason="hybrid_rrf_implementation_owner_candidate",
        completeness="inspection_only",
    )
    task = (
        "Add deterministic multi-key sorting to standard search output. "
        "The CLI accepts repeatable --sort and output remains deterministic. "
        "Sorting controls require --sort and invalid options use existing CLI errors."
    )

    assert _owner_path_scope_affinity(item, task) == (0, 0, 0)


def test_owner_priority_prefers_task_local_artifacts_over_incidental_symbol_matches() -> None:
    def candidate(path: str, symbol: str) -> ContextEvidenceItem:
        return ContextEvidenceItem(
            kind="inspection_candidate",
            path=path,
            start_line=1,
            end_line=1,
            symbol=symbol,
            relation="",
            confidence=None,
            verification_status="rank_only",
            source_revision="source-1",
            graph_revision="graph-1",
            evidence_sha256=hashlib.sha256(path.encode("utf-8")).hexdigest(),
            decision_reason="task_path_implementation_owner_candidate",
            completeness="inspection_only",
        )

    katex_task = (
        "KaTeX lacks support for spanning columns. Add multicolumn alignment content. "
        "Throw ParseError for use outside array-like environments. "
        "For MathML output, add columnspan and columnalign attributes."
    )
    katex_candidates = (
        candidate("src/buildMathML.ts", "buildMathML"),
        candidate("src/environments/array.ts", "handler"),
        candidate("src/mathMLTree.ts", "mathMLTree"),
    )
    actionlint_task = (
        "Add action pinning checks for step actions and job-level reusable workflow "
        "uses references. Skip local refs and distinguish reusable workflows in errors."
    )
    actionlint_candidates = (
        candidate("rule_workflow_call.go", "isWorkflowCallUsesLocalFormat"),
        candidate("reusable_workflow.go", "newNullLocalReusableWorkflowCache"),
        candidate("rule_action.go", "RuleAction"),
    )

    assert min(
        katex_candidates,
        key=lambda item: _owner_priority_key(item, katex_task, frozenset()),
    ).path == "src/environments/array.ts"
    assert min(
        actionlint_candidates,
        key=lambda item: _owner_priority_key(item, actionlint_task, frozenset()),
    ).path == "reusable_workflow.go"
    assert min(
        tuple(reversed(actionlint_candidates)),
        key=lambda item: _owner_priority_key(item, actionlint_task, frozenset()),
    ).path == "reusable_workflow.go"
    renamed_katex_candidates = (
        candidate("checkout/vendor/src/buildMathML.ts", "buildMathML"),
        candidate("checkout/vendor/src/environments/array.ts", "handler"),
        candidate("checkout/vendor/src/mathMLTree.ts", "mathMLTree"),
    )
    assert min(
        renamed_katex_candidates,
        key=lambda item: _owner_priority_key(item, katex_task, frozenset()),
    ).path == "checkout/vendor/src/environments/array.ts"


def test_complete_compound_artifact_phrase_outranks_incidental_helper_symbol() -> None:
    documents = (
        _document(
            "reusable_workflow.go",
            "newNullLocalReusableWorkflowCache",
            "func newNullLocalReusableWorkflowCache() {}",
        ),
        _document(
            "rule_workflow_call.go",
            "isWorkflowCallUsesLocalFormat",
            "func isWorkflowCallUsesLocalFormat() {}",
        ),
        _document("rule_action.go", "RuleAction", "type RuleAction struct {}"),
    )
    task = (
        "Add action pinning checks for step actions and job-level reusable workflow "
        "uses references. Skip local refs and distinguish reusable workflows in errors."
    )

    packet = RepositoryContextCompiler().compile(
        HybridRepository(
            documents=documents,
            structural_links=(),
            source_revision="source-1",
            complete=True,
            reason_codes=(),
            source_file_count=len(documents),
            document_chars=180,
        ),
        _request(task),
    )

    assert packet.inspection_implementation_owners[0].path == "reusable_workflow.go"


def test_api_words_do_not_manufacture_unscoped_module_owners() -> None:
    documents = (
        _document("core/engine/src/builtins/error/eval.rs", "eval", "pub fn eval() {}"),
        _document(
            "core/engine/src/value/conversions/convert.rs",
            "convert",
            "pub fn convert() {}",
        ),
        _document(
            "core/engine/src/builtins/array_buffer/shared.rs",
            "shared",
            "pub fn shared() {}",
        ),
        _document("core/engine/src/job.rs", "job", "pub fn run_jobs() {}"),
    )
    task = (
        "Add `Context::eval_with_evaluation`. Return an Error-like value. "
        "The cancellation reason accepts any value convertible into the engine "
        "value type, and handle-aware APIs take handles by shared reference."
    )

    packet = RepositoryContextCompiler().compile(
        HybridRepository(
            documents=documents,
            structural_links=(),
            source_revision="source-1",
            complete=True,
            reason_codes=(),
            source_file_count=len(documents),
            document_chars=180,
        ),
        _request(task),
    )

    assert not {
        "core/engine/src/builtins/error/eval.rs",
        "core/engine/src/value/conversions/convert.rs",
        "core/engine/src/builtins/array_buffer/shared.rs",
    } & {item.path for item in packet.inspection_implementation_owners}


def test_owner_obligation_coverage_breaks_equal_identity_affinity() -> None:
    documents = (
        _document(
            "bandit/plugins/markupsafe_markup_xss.py",
            "markupsafe_markup_xss",
            "def markupsafe_markup_xss(context): pass",
        ),
        _document(
            "bandit/plugins/injection_sql.py",
            "injection_sql",
            "def injection_sql(context): pass",
        ),
    )
    task = (
        "Bandit's injection checks only work on string literals; user input "
        "flowing through variables to sinks goes undetected.\n\n"
        "Taint propagates through assignments and calls. Parameterized SQL "
        "queries and escaping are safe.\n\n"
        "Add Bandit plugins: B620 for SQL injection sinks execute and "
        "executemany, and B624 for exact markupsafe.Markup XSS sinks."
    )

    packet = RepositoryContextCompiler().compile(
        HybridRepository(
            documents=documents,
            structural_links=(),
            source_revision="source-1",
            complete=True,
            reason_codes=(),
            source_file_count=len(documents),
            document_chars=120,
        ),
        _request(task),
    )

    assert (
        packet.inspection_implementation_owners[0].path
        == "bandit/plugins/injection_sql.py"
    )


def test_argument_noun_is_not_granted_edit_authority() -> None:
    documents = (
        _document(
            "core/engine/src/context/mod.rs",
            "Context",
            "pub struct Context;",
        ),
        _document(
            "core/runtime/src/interval.rs",
            "handle",
            "fn handle() {}",
        ),
    )
    task = (
        "Implement evaluation cancellation with parent/child handles and checkpoints. "
        "Handle clones must share the same cancellation state and reason lineage. "
        "Add `Context::run_jobs_with_evaluation`. APIs that run under a "
        "handle must take the `handle` by shared reference."
    )

    packet = RepositoryContextCompiler().compile(
        HybridRepository(
            documents=documents,
            structural_links=(),
            source_revision="source-1",
            complete=True,
            reason_codes=(),
            source_file_count=2,
            document_chars=120,
        ),
        _request(task),
    )

    assert any(item.symbol == "Context" for item in packet.primary_edit_targets)
    assert all(item.symbol != "handle" for item in packet.primary_edit_targets)
    assert all(
        item.symbol != "handle" for item in packet.inspection_implementation_owners
    )


def test_qualified_owner_prefers_its_namesake_module_over_homonym() -> None:
    documents = (
        _document(
            "core/engine/src/script.rs",
            "Script",
            "pub struct Script;",
        ),
        _document(
            "core/engine/src/builtins/locale/options.rs",
            "Script",
            "enum Script { Latin, Cyrillic }",
        ),
    )
    task = "Add `Script::evaluate_with_evaluation` with cancellation checkpoints."

    packet = RepositoryContextCompiler().compile(
        HybridRepository(
            documents=documents,
            structural_links=(),
            source_revision="source-1",
            complete=True,
            reason_codes=(),
            source_file_count=2,
            document_chars=100,
        ),
        _request(task),
    )

    assert [item.path for item in packet.primary_edit_targets] == ["core/engine/src/script.rs"]


def test_new_snake_case_api_does_not_promote_case_mismatched_analog() -> None:
    documents = (
        _document("object/object.go", "Reset", "func (e *Environment) Reset() {}"),
        _document("evaluator/functions.go", "require", "func require() {}"),
    )

    facets = compile_task_facets(
        "Expose `reset_require_cache()` and `require_cache_info()`.",
        documents,
    )

    exact = {symbol for facet in facets for symbol in facet.exact_symbols}
    unresolved = {symbol for facet in facets for symbol in facet.unresolved_symbols}
    assert "Reset" not in exact
    assert "reset_require_cache" in unresolved
    assert "require" in exact


def test_new_api_does_not_promote_generic_unqualified_prefixes() -> None:
    documents = (
        _document("web/static/bundle.js", "delete", "function delete() {}"),
        _document("src/format.py", "format", "def format(): pass"),
        _document("src/task.py", "task", "def task(): pass"),
        _document("src/agent.ts", "agent", "const agent = registry.get(id)"),
    )

    facets = compile_task_facets(
        "Add `delete_snapshot`, `format_snapshot_task_list`, `task_id`, and `agent_id`.",
        documents,
    )

    exact = {symbol for facet in facets for symbol in facet.exact_symbols}
    unresolved = {symbol for facet in facets for symbol in facet.unresolved_symbols}
    assert not {"agent", "delete", "format", "task"} & exact
    assert {
        "delete_snapshot",
        "format_snapshot_task_list",
        "task_id",
        "agent_id",
    } <= unresolved


def test_symbol_bearing_paragraph_does_not_duplicate_extracted_obligation() -> None:
    documents = (
        _document(
            "src/monitor.py",
            "format_running_task_list",
            "def format_running_task_list(): pass",
        ),
    )

    facets = compile_task_facets(
        "Monitor methods must mirror `format_running_task_list`.\n\n"
        "Add `capture_snapshot` and return its ID.",
        documents,
    )

    matching = [facet for facet in facets if "format_running_task_list" in facet.exact_symbols]
    assert len(matching) == 1


def test_multi_token_task_path_match_remains_inspection_evidence() -> None:
    registry_link = StructuralLink(
        source_path="backend/handlers/multiAgentChat.ts",
        target_path="backend/providers/registry.ts",
        relation="IMPORTS",
        confidence=1.0,
        certified=True,
        verification_status="verified",
        source_symbol="multiAgentChat",
        target_symbol="globalRegistry",
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
    documents = (
        _document(
            "backend/handlers/multiAgentChat.ts",
            "executeMultiAgentChat",
            "async function executeMultiAgentChat(agentId: string) { return agentId; }",
        ),
        _document(
            "backend/handlers/chat.ts",
            "agent",
            "const agent = availableAgents.find(item => item.id === agentId);",
        ),
        _document("backend/lambda.ts", "lambda", "export const lambda = true;"),
        _document(
            "backend/providers/registry.ts",
            "globalRegistry",
            "export const globalRegistry = new ProviderRegistry();",
        ),
        _document(
            "ClaudeAgentHub/ClaudeAgentHub/ViewModels/ChatViewModel.swift",
            "sendMessage",
            "func sendMessage() { let agentId = selectedAgent.id }",
        ),
    )
    repository = HybridRepository(
        documents=documents,
        structural_links=(registry_link,),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=3,
        document_chars=sum(len(document.text) for document in documents),
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        replace(
            _request(
                "Implement recursive agent delegation in the multi-agent chat flow. "
                "The delegate_task input contains agent_id."
            ),
            dense_candidates=(
                (
                    "ClaudeAgentHub/ClaudeAgentHub/ViewModels/ChatViewModel.swift",
                    0.99,
                ),
                ("backend/handlers/multiAgentChat.ts", 0.80),
            ),
        ),
    )

    assert not packet.primary_edit_targets
    candidate = packet.inspection_implementation_owners[0]
    assert candidate.path == "backend/handlers/multiAgentChat.ts"
    assert candidate.decision_reason == "task_path_implementation_owner_candidate"
    assert candidate.localization_role == "IMPLEMENTATION_OWNER"
    assert candidate.facet_ids
    assert [item.path for item in packet.inspection_integration] == [
        "backend/providers/registry.ts"
    ]
    assert packet.inspection_integration[0].facet_ids == candidate.facet_ids
    assert any(
        item.kind == "relationship"
        and item.path == "backend/providers/registry.ts"
        and item.facet_ids == candidate.facet_ids
        for item in packet.evidence_items
    )


def test_long_distinctive_task_path_token_is_inspection_not_edit_authority() -> None:
    documents = (
        _document(
            "lib/lexer/shorthand.js",
            "shorthand",
            "export function expandShorthand(property) { return property; }",
        ),
        _document(
            "lib/__tests/lexer-match-property.js",
            "lexerMatchProperty",
            "describe('property matching', () => {});",
        ),
        _document("lib/lexer/Lexer.js", "Lexer", "export class Lexer {}"),
    )
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
        _request("Implement shorthand expansion and compression."),
    )

    assert not packet.primary_edit_targets
    candidate = packet.inspection_implementation_owners[0]
    assert candidate.path == "lib/lexer/shorthand.js"
    assert candidate.decision_reason == "task_path_implementation_owner_candidate"
    assert candidate.facet_ids
    assert all("/__tests/" not in item.path for item in packet.inspection_implementation_owners)


def test_single_channel_strong_filename_match_is_an_owner_candidate() -> None:
    repository = HybridRepository(
        documents=(
            _document(
                "parser/default_args.go",
                "parseDefaultArguments",
                "func parseDefaultArguments() {}",
            ),
            _document("vm/runtime.go", "run", "func run() {}"),
        ),
        structural_links=(),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=2,
        document_chars=90,
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request("Support default function arguments in the parser."),
    )

    assert "parser/default_args.go" in {
        item.path for item in packet.inspection_implementation_owners
    }


def test_single_underscore_tests_directory_is_validation_not_owner() -> None:
    repository = HybridRepository(
        documents=(
            _document(
                "lib/__tests/lexer-shorthand.js",
                "testShorthand",
                "test('shorthand', () => {})",
            ),
        ),
        structural_links=(),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=1,
        document_chars=50,
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request("Implement shorthand expansion and compression."),
    )

    assert not packet.inspection_implementation_owners


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
            row for row in result.ranked_files if row.path == "core/engine/src/module/mod.rs"
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
        facet for facet in facets if "Script::evaluate_with_evaluation" in facet.unresolved_symbols
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
        facet for facet in facets if "Context::run_jobs_with_evaluation" in facet.unresolved_symbols
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
        item.path == "core/engine/src/context/mod.rs" for item in packet.primary_edit_targets
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
    assert all(item.path != "core/runtime/src/abort/mod.rs" for item in packet.primary_edit_targets)


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
    assert all(item.path != "core/engine/src/evaluation.rs" for item in packet.evidence_items)
    assert any(
        item.status is FacetCoverageStatus.COVERED_NEW_FILE_PRECEDENT
        and item.paths == ("core/engine/src/evaluation.rs",)
        for item in packet.facet_coverage
    )
    coverage_by_requirement = {item.requirement_id: item for item in packet.requirement_coverage}
    requirements_by_entity = {item.entity: item for item in packet.task_requirements}
    assert (
        coverage_by_requirement[
            requirements_by_entity["EvaluationHandle::cancel_with_reason"].requirement_id
        ].mechanism
        == "NEW_FILE_PRECEDENT"
    )
    assert (
        coverage_by_requirement[requirements_by_entity["run_jobs"].requirement_id].mechanism
        == "EXACT_EDIT"
    )
    unresolved = coverage_by_requirement[
        requirements_by_entity["run_jobs_with_evaluation"].requirement_id
    ]
    assert unresolved.status is RequirementCoverageStatus.UNCOVERED
    assert unresolved.requirement_id in packet.uncovered_requirements


def test_public_entrypoint_constraint_is_delivered_without_edit_authority() -> None:
    document = _document(
        "repl/repl.go",
        "BeginRepl",
        "func BeginRepl(args []string, version string) {}",
    )
    repository = HybridRepository(
        documents=(document,),
        structural_links=(),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=1,
        document_chars=len(document.text),
    )

    packet = RepositoryContextCompiler().compile(
        repository,
        _request("Preserve the public `BeginRepl(args []string, version string)` signature."),
    )

    assert packet.primary_edit_targets == ()
    assert [item.path for item in packet.inspection_public_surface] == ["repl/repl.go"]
    requirement = next(item for item in packet.task_requirements if item.entity == "BeginRepl")
    assert requirement.intent is RequirementIntent.PRESERVE


def test_behavior_clause_is_not_fabricated_as_an_edit_identity() -> None:
    packet = RepositoryContextCompiler().compile(
        _repository(),
        _request("Ensure equivalent paths reuse one cache entry."),
    )

    responsibility = next(
        item for item in packet.task_requirements if item.entity == "repository-responsibility"
    )
    assert responsibility.intent is RequirementIntent.BEHAVIOR
