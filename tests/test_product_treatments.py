from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from gt_engine.graph_db_projection import (
    ImpactProjection,
    ProcessProjection,
    ProjectionReceipt,
    ProjectionStatus,
)
from gt_engine.hybrid_repository import HybridRepository
from gt_engine.hybrid_retrieval import (
    EvidenceOrigin,
    RepositoryDocument,
    StructuralLink,
)
from gt_engine.repository_context_compiler import (
    ContextEvidenceItem,
    ContextStatus,
    GTContextPacket,
    LocalizationRole,
    TaskFacet,
)
from gt_engine.repository_graph_service import GraphStatus
from gt_harness.treatments import (
    ActionObservation,
    BareTreatment,
    FeatureState,
    GroundTruthTreatment,
    TreatmentStatus,
    TreatmentUnavailableError,
    _bounded_token_count,
)


def test_bare_treatment_is_a_strict_no_op() -> None:
    treatment = BareTreatment()
    assert treatment.prepare("task") == ""
    assert treatment.before_model_call(1) == ""
    assert treatment.after_action("bash", {"command": "x"}, "ok", False) is None
    assert treatment.finalize(None)["provider_calls"] == 0


def test_successful_repository_read_requests_same_observation_context(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeReceipt:
        query_ready = True
        build_status = GraphStatus.READY
        source_revision = "revision-2"

    class FakeService:
        root = tmp_path

        def status(self):
            return FakeReceipt()

    source = tmp_path / "app.py"
    source.write_text("def answer():\n    return 42\n", encoding="utf-8")
    treatment = GroundTruthTreatment(tmp_path)
    treatment.service = FakeService()
    treatment.treatment_status = treatment.treatment_status.ACTIVE
    monkeypatch.setattr(
        GroundTruthTreatment,
        "_refresh_and_render_update",
        lambda self: (
            "<groundtruth-repository-context>real app fact"
            "</groundtruth-repository-context>"
        ),
    )

    augmentation = treatment.after_action(
        "bash",
        {"command": "sed -n '1,20p' app.py"},
        "app.py:1:def answer():",
        False,
    )

    assert augmentation is not None
    assert augmentation.content.endswith("</groundtruth-repository-context>")
    assert augmentation.source_revision


def test_update_suppresses_inspection_only_packet_instead_of_spending_delivery(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeReceipt:
        query_ready = True
        build_status = GraphStatus.READY
        repository = str(tmp_path)
        commit_sha = "a" * 40
        source_revision = "b" * 64
        graph_checksum_or_identity = "c" * 64
        degraded_reasons = ()
        receipt_schema = "gt.graph_receipt.v1"
        graph_builder_version = "test"

    class FakeService:
        root = tmp_path
        receipt_path = tmp_path / "graph-receipt.json"

        def status(self):
            return FakeReceipt()

    item = ContextEvidenceItem(
        kind="inspection_candidate",
        path="test/test_jinja2.py",
        start_line=1,
        end_line=1,
        symbol="test_error",
        relation="",
        confidence=None,
        verification_status="ranked",
        source_revision="b" * 64,
        graph_revision="c" * 64,
        evidence_sha256="d" * 64,
        decision_reason="hybrid_retrieval_inspection",
        completeness="ranked_candidate_not_edit_target",
    )
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "b" * 64},
        inspection_candidates=(item,),
        uncertainties=("no_decision_relevant_evidence",),
        evidence_items=(item,),
        coverage={"dense_index": {"status": "READY", "query_ready": True}},
    )
    treatment = GroundTruthTreatment(tmp_path)
    treatment.service = FakeService()
    treatment.treatment_status = treatment.treatment_status.ACTIVE
    treatment.context_dirty = True
    monkeypatch.setattr(
        GroundTruthTreatment,
        "_context",
        lambda self, **_kwargs: packet,
    )

    assert treatment._render(update=True, budget=4_000, delivered_before_call=2) == ""
    assert treatment.delivery_count == 0
    assert treatment.suppressed_inspection_only_updates == 1


def test_initial_context_abstains_without_decision_grade_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeReceipt:
        query_ready = True
        build_status = GraphStatus.READY
        repository = str(tmp_path)
        commit_sha = "a" * 40
        source_revision = "b" * 64
        graph_checksum_or_identity = "c" * 64
        degraded_reasons = ()

    class FakeService:
        root = tmp_path

        def status(self):
            return FakeReceipt()

    item = ContextEvidenceItem(
        kind="inspection_candidate",
        path="README.md",
        start_line=1,
        end_line=1,
        symbol="",
        relation="",
        confidence=None,
        verification_status="ranked",
        source_revision="b" * 64,
        graph_revision="c" * 64,
        evidence_sha256="d" * 64,
        decision_reason="hybrid_retrieval_inspection",
        completeness="ranked_candidate_not_edit_target",
    )
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "b" * 64},
        inspection_candidates=(item,),
        uncertainties=(
            "insufficient_independent_support",
            "no_decision_relevant_evidence",
            "no_complete_evidence",
        ),
        evidence_items=(item,),
        coverage={"dense_index": {"status": "READY", "query_ready": True}},
    )
    treatment = GroundTruthTreatment(tmp_path)
    treatment.service = FakeService()
    treatment.treatment_status = treatment.treatment_status.ACTIVE
    monkeypatch.setattr(
        GroundTruthTreatment,
        "_context",
        lambda self, **_kwargs: packet,
    )

    assert treatment._render(update=False, budget=4_000, delivered_before_call=1) == ""
    assert treatment.delivery_count == 0
    assert treatment.treatment_status.value == "NOT_APPLICABLE"
    assert treatment.suppressed_inspection_only_updates == 1
    assert treatment.errors == [
        "NOT_APPLICABLE:context_abstained:no_decision_grade_evidence"
    ]


def test_strong_task_path_inspection_is_delivered_without_edit_authority(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeReceipt:
        query_ready = True
        build_status = GraphStatus.READY
        repository = str(tmp_path)
        commit_sha = "a" * 40
        source_revision = "b" * 64
        graph_checksum_or_identity = "c" * 64
        degraded_reasons = ()

    class FakeService:
        root = tmp_path

        def status(self):
            return FakeReceipt()

    item = ContextEvidenceItem(
        kind="inspection_candidate",
        path="backend/handlers/multiAgentChat.ts",
        start_line=1,
        end_line=80,
        symbol="executeMultiAgentChat",
        relation="",
        confidence=None,
        verification_status="ranked",
        source_revision="b" * 64,
        graph_revision="c" * 64,
        evidence_sha256="d" * 64,
        decision_reason="task_path_phrase_inspection",
        completeness="ranked_candidate_not_edit_target",
    )
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "b" * 64},
        inspection_candidates=(item,),
        uncertainties=(
            "insufficient_independent_support",
            "no_decision_relevant_evidence",
            "no_complete_evidence",
        ),
        evidence_items=(item,),
        coverage={"dense_index": {"status": "READY", "query_ready": True}},
    )
    treatment = GroundTruthTreatment(tmp_path)
    treatment.service = FakeService()
    treatment.treatment_status = TreatmentStatus.ACTIVE
    monkeypatch.setattr(GroundTruthTreatment, "_context", lambda self, **_kwargs: packet)

    rendered = treatment._render(update=False, budget=4_000, delivered_before_call=1)

    assert "INSPECT_CANDIDATE_NOT_EDIT_AUTHORITY " in rendered
    assert "backend/handlers/multiAgentChat.ts" in rendered
    assert "EXACT_EDIT_TARGET" not in rendered
    assert treatment.treatment_status is TreatmentStatus.ACTIVE


def test_run_cli_records_not_applicable_treatment_and_runs_provider(
    tmp_path: Path, monkeypatch
) -> None:
    import gt_harness.miniswe_runner as runner
    from gt_harness.cli import _run_agent

    fake_agent = SimpleNamespace(
        config=SimpleNamespace(system_template="system", instance_template="task={{task}}")
    )
    monkeypatch.setattr(runner, "build_miniswe_agent", lambda **_kwargs: fake_agent)
    monkeypatch.setattr(
        runner,
        "run_miniswe_agent",
        lambda _agent, _task: SimpleNamespace(
            stop_reason="Submitted",
            iterations=1,
            total_input_tokens=1,
            total_output_tokens=1,
            total_cache_read_tokens=0,
            transcript=({"role": "assistant", "content": "done"},),
        ),
    )
    root = tmp_path / "not-code-cli"
    root.mkdir()
    output = tmp_path / "run-receipt.json"
    args = SimpleNamespace(
        task="Fix the parser",
        model="provider/model",
        base_url="https://provider.invalid",
        max_iterations=3,
        time_budget_seconds=30.0,
        root=str(root),
        temperature=0.0,
        run_id="not-applicable",
        task_id="task-not-applicable",
        trial_id="1",
        output=str(output),
        state_dir=None,
        treatment="groundtruth",
    )

    assert _run_agent(args) == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == "COMPLETED"
    assert receipt["provider_calls"] == 1
    assert receipt["treatment_receipt"]["treatment_status"] == "NOT_APPLICABLE"


def test_groundtruth_honors_private_state_directory(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "private-state"
    monkeypatch.setenv("GT_STATE_DIR", str(state))

    treatment = GroundTruthTreatment(tmp_path / "repository")

    assert treatment.service.state_dir == state.resolve()


def test_hybrid_required_fails_closed_when_dense_model_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeReceipt:
        query_ready = True
        build_status = GraphStatus.READY
        degraded_reasons = ()
        files_attempted = 1

    class FakeService:
        def status(self):
            return FakeReceipt()

    monkeypatch.delenv("GT_DENSE_MODEL_DIR", raising=False)
    treatment = GroundTruthTreatment(tmp_path, retrieval_mode="hybrid_required")
    treatment.service = FakeService()

    with pytest.raises(
        TreatmentUnavailableError,
        match="dense_retrieval_required:dense_model_not_configured",
    ):
        treatment._ensure_dense_ready()

    assert treatment.treatment_status.value == "FAILED"


def test_hybrid_required_abstains_when_repository_has_no_indexable_source(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeReceipt:
        query_ready = False
        build_status = GraphStatus.FAILED
        degraded_reasons = ("no_supported_source",)
        files_attempted = 0

    class FakeService:
        def status(self):
            return FakeReceipt()

    monkeypatch.delenv("GT_DENSE_MODEL_DIR", raising=False)
    treatment = GroundTruthTreatment(tmp_path, retrieval_mode="hybrid_required")
    treatment.service = FakeService()

    treatment._ensure_dense_ready()

    assert treatment.treatment_status.value == "NOT_APPLICABLE"
    assert treatment.errors == [
        "NOT_APPLICABLE:dense_retrieval_required:dense_model_not_configured"
    ]


def test_dense_retrieval_queries_each_requirement_and_rrf_fuses_results(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeReceipt:
        source_revision = "s" * 64
        graph_checksum_or_identity = "g" * 64

    class FakeService:
        root = tmp_path
        graph_path = tmp_path / "graph.sqlite3"

        def status(self):
            return FakeReceipt()

    class FakeDenseIndex:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def query(self, query: str, *, limit: int):
            self.queries.append(query)
            index = len(self.queries)
            candidates = (
                SimpleNamespace(path="src/shared.py", score=0.70),
                SimpleNamespace(path=f"src/only_{index}.py", score=0.95),
            )
            return SimpleNamespace(
                query_ready=True,
                status=SimpleNamespace(value="READY"),
                source_revision="s" * 64,
                model_identity="model",
                candidates=candidates[:limit],
                degraded_reasons=(),
            )

    captured: dict[str, object] = {}

    class FakeCompiler:
        def compile(self, _repository, request):
            captured["dense_candidates"] = request.dense_candidates
            return GTContextPacket(
                status=ContextStatus.ABSTAIN,
                repository_identity={"source_revision": "s" * 64},
            )

    monkeypatch.setattr(
        "gt_harness.treatments.build_query_hybrid_repository",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    treatment = GroundTruthTreatment(tmp_path)
    treatment.service = FakeService()
    dense_index = FakeDenseIndex()
    treatment.dense_index = dense_index
    treatment.compiler = FakeCompiler()
    treatment.task = "Implement parser recovery. Add renderer fallback."

    treatment._context(update=False, budget=4_000)

    assert len(dense_index.queries) >= 2
    assert dense_index.queries[0] != dense_index.queries[1]
    candidates = captured["dense_candidates"]
    assert candidates[0][0] == "src/shared.py"
    assert len(treatment.dense_query_receipts) == len(dense_index.queries)
    assert all(row["query_sha256"] for row in treatment.dense_query_receipts)


def test_groundtruth_observes_only_real_repository_paths_and_real_diagnostics(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".github" / "workflows" / "check.yml"
    source.parent.mkdir(parents=True)
    source.write_text("name: check\n", encoding="utf-8")
    treatment = GroundTruthTreatment(tmp_path)

    treatment.after_action(
        "bash",
        {"command": "rg error ."},
        ".github/workflows/check.yml:1:name: error-report\n../secret.py:1:error",
        False,
    )

    assert treatment.active_paths == [".github/workflows/check.yml"]
    assert treatment.diagnostics == []
    assert treatment.context_dirty is True

    treatment.context_dirty = False
    treatment.after_action("bash", {"command": "pwd"}, "ordinary output", False)
    assert treatment.context_dirty is False

    treatment.after_action(
        "bash",
        {"command": "pytest"},
        "FAILED tests/test_parser.py::test_parse - ValueError: broken",
        True,
    )
    assert treatment.diagnostics
    assert treatment.context_dirty is True

    treatment.context_dirty = False
    treatment.after_action(
        "bash",
        {"command": "python -m pytest tests/test_parser.py -q"},
        "3 passed in 0.12s",
        False,
    )
    assert treatment.diagnostics == []
    assert treatment.validation_state == "pass"
    assert treatment.context_dirty is True


def test_directory_listing_does_not_trigger_repository_context_update(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "feature.py"
    source.parent.mkdir()
    source.write_text("def feature():\n    return 1\n", encoding="utf-8")
    treatment = GroundTruthTreatment(tmp_path)

    augmentation = treatment.after_action(
        "bash",
        {"command": "ls -la src"},
        "src/feature.py\n",
        False,
    )

    assert augmentation is None
    assert treatment.active_paths == []
    assert treatment.context_dirty is False


def test_groundtruth_delivers_valid_composed_relationship_context(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeReceipt:
        query_ready = True
        build_status = GraphStatus.READY
        repository = str(tmp_path)
        commit_sha = "a" * 40
        source_revision = "b" * 64
        graph_checksum_or_identity = "c" * 64
        degraded_reasons = ()
        files_attempted = 2

        def as_dict(self):
            return {"build_status": self.build_status.value, "query_ready": True}

    class FakeService:
        root = tmp_path
        graph_path = tmp_path / "graph.sqlite3"

        def status(self):
            return FakeReceipt()

    def document(path: str, symbol: str, text: str) -> RepositoryDocument:
        return RepositoryDocument(
            path=path,
            symbol=symbol,
            text=text,
            start_line=1,
            end_line=1,
            provenance=("graph_node",),
            origin=EvidenceOrigin.PREEXISTING_REPOSITORY,
            origin_revision="b" * 64,
        )

    repository = HybridRepository(
        documents=(
            document("app.py", "answer", "def answer(): return 42"),
            document("caller.py", "invoke", "def invoke(): return answer()"),
        ),
        structural_links=(
            StructuralLink(
                source_path="caller.py",
                target_path="app.py",
                relation="CALLS",
                confidence=1.0,
                certified=True,
                verification_status="verified",
                source_symbol="invoke",
                target_symbol="answer",
                source_start_line=1,
                target_start_line=1,
                source_content_sha256="d" * 64,
                target_content_sha256="e" * 64,
                source_evidence_origin="preexisting_repository",
                target_evidence_origin="preexisting_repository",
                origin="program",
                resolution_outcome="exact",
                resolution_method="exact_symbol",
                candidate_count=1,
            ),
        ),
        source_revision="b" * 64,
        complete=True,
        reason_codes=(),
        source_file_count=2,
        document_chars=64,
    )
    monkeypatch.setattr(
        "gt_harness.treatments.build_query_hybrid_repository",
        lambda *_args, **_kwargs: repository,
    )

    treatment = GroundTruthTreatment(tmp_path)
    treatment.service = FakeService()
    treatment.task = "Change `answer` without breaking callers"

    rendered = treatment._render(update=False, budget=4_000, delivered_before_call=1)

    assert len(rendered) <= 4_000
    assert rendered.endswith("</groundtruth-repository-context>")
    assert 'schema="gt.agent_context.v6"' in rendered
    assert "REQUIREMENT facet-" in rendered
    assert "EXACT_EDIT_TARGET app.py:1#answer" in rendered
    assert any(
        "req=facet-" in line
        for line in rendered.splitlines()
        if line.startswith("EXACT_EDIT_TARGET")
    )
    assert "INSPECT_INTEGRATION caller.py:1#invoke" in rendered
    assert "VERIFIED_RELATION caller.py:invoke CALLS app.py:answer" in rendered
    assert not any(
        "req=unscoped" in line
        for line in rendered.splitlines()
        if line.startswith(("VERIFIED_RELATION", "SEMANTIC_FACT"))
    )
    assert "UNCERTAINTY graph_projection_failed" in rendered
    assert _bounded_token_count(rendered) <= 500
    treatment.max_delivery_count = 1
    treatment.context_dirty = True
    assert treatment._render(update=True, budget=4_000, delivered_before_call=2) == ""
    assert treatment.delivery_count == 1
    assert treatment.context_compile_count == 1
    assert "context_delivery_limit_reached" in treatment.errors


def test_provider_context_v6_keeps_edit_public_integration_and_new_file_roles_separate(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeReceipt:
        query_ready = True
        build_status = GraphStatus.READY
        repository = str(tmp_path)
        commit_sha = "a" * 40
        source_revision = "b" * 64
        graph_checksum_or_identity = "c" * 64
        degraded_reasons = ()

    class FakeService:
        root = tmp_path

        def status(self):
            return FakeReceipt()

    def item(path: str, symbol: str, role: str, digest: str) -> ContextEvidenceItem:
        return ContextEvidenceItem(
            kind=role.lower(),
            path=path,
            start_line=1,
            end_line=1,
            symbol=symbol,
            relation="",
            confidence=1.0,
            verification_status="verified",
            source_revision="b" * 64,
            graph_revision="c" * 64,
            evidence_sha256=digest * 64,
            decision_reason=f"verified_{role.lower()}",
            completeness="exact_identity",
            localization_role=role,
            facet_ids=(f"facet-{role.lower()}",),
        )

    edit = item("src/container.ts", "AwilixContainer", "EDIT", "d")
    public = item("src/awilix.ts", "AwilixContainer", "PUBLIC_SURFACE", "e")
    integration = item("src/load-modules.ts", "loadModules", "INTEGRATION", "f")
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "b" * 64},
        primary_edit_targets=(edit,),
        inspection_public_surface=(public,),
        inspection_integration=(integration,),
        proposed_new_files=("src/evaluation.rs",),
        uncovered_facets=("facet-new role=EDIT unresolved=EvaluationHandle",),
        evidence_items=(edit, public, integration),
        coverage={"dense_index": {"status": "DISABLED", "query_ready": False}},
    )
    treatment = GroundTruthTreatment(tmp_path)
    treatment.service = FakeService()
    treatment.treatment_status = treatment.treatment_status.ACTIVE
    monkeypatch.setattr(GroundTruthTreatment, "_context", lambda self, **_kwargs: packet)

    rendered = treatment._render(update=False, budget=6_000, delivered_before_call=1)

    assert 'schema="gt.agent_context.v6"' in rendered
    assert "EXACT_EDIT_TARGET src/container.ts:1#AwilixContainer" in rendered
    assert "INSPECT_PUBLIC_SURFACE src/awilix.ts:1#AwilixContainer" in rendered
    assert "INSPECT_INTEGRATION src/load-modules.ts:1#loadModules" in rendered
    assert "PROPOSED_NEW_FILE src/evaluation.rs fact=false" in rendered
    assert "UNCOVERED_FACET facet-new role=EDIT unresolved=EvaluationHandle" in rendered


def test_provider_context_v6_prunes_within_budget_without_losing_roles(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeReceipt:
        query_ready = True
        build_status = GraphStatus.READY
        repository = str(tmp_path)
        commit_sha = "a" * 40
        source_revision = "b" * 64
        graph_checksum_or_identity = "c" * 64
        degraded_reasons = ()

    class FakeService:
        root = tmp_path

        def status(self):
            return FakeReceipt()

    def item(index: int, role: str) -> ContextEvidenceItem:
        return ContextEvidenceItem(
            kind=role.lower(),
            path=f"packages/very_long_subsystem_name_{role.lower()}/source_{index}.ts",
            start_line=index + 1,
            end_line=index + 1,
            symbol=f"VeryLongRepositorySymbol{role}{index}",
            relation="",
            confidence=1.0,
            verification_status="verified",
            source_revision="b" * 64,
            graph_revision="c" * 64,
            evidence_sha256=f"{index:x}" * 64,
            decision_reason=f"verified_role_complete_{role.lower()}_task_facet",
            completeness="exact_identity",
            localization_role=role,
            facet_ids=(f"facet-{role.lower()}-{index}",),
        )

    edits = tuple(item(index, "EDIT") for index in range(3))
    public = tuple(item(index + 3, "PUBLIC_SURFACE") for index in range(6))
    integration = tuple(item(index + 9, "INTEGRATION") for index in range(6))
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "b" * 64},
        primary_edit_targets=edits,
        inspection_public_surface=public,
        inspection_integration=integration,
        proposed_new_files=(
            "packages/very_long_subsystem_name_edit/new_evaluation_handle.ts",
            "packages/very_long_subsystem_name_edit/new_cancellation_state.ts",
        ),
        uncovered_facets=tuple(
            f"facet-{index} role=EDIT unresolved=VeryLongUnresolvedSymbol{index}"
            for index in range(6)
        ),
        evidence_items=(*edits, *public, *integration),
        uncertainties=tuple(f"explicit_uncertainty_reason_{index}" for index in range(10)),
        coverage={"dense_index": {"status": "DISABLED", "query_ready": False}},
    )
    treatment = GroundTruthTreatment(tmp_path)
    treatment.service = FakeService()
    treatment.treatment_status = treatment.treatment_status.ACTIVE
    monkeypatch.setattr(GroundTruthTreatment, "_context", lambda self, **_kwargs: packet)

    rendered = treatment._render(update=False, budget=6_000, delivered_before_call=1)

    assert _bounded_token_count(rendered) <= treatment.start_token_budget
    assert "EXACT_EDIT_TARGET " in rendered
    assert "INSPECT_PUBLIC_SURFACE " in rendered
    assert "INSPECT_INTEGRATION " in rendered
    assert "PROPOSED_NEW_FILE " in rendered
    assert "UNCOVERED_FACET " in rendered


def test_complex_task_compaction_keeps_strong_facts_under_release_budget(
    tmp_path: Path, monkeypatch
) -> None:
    """Many unresolved requirements must not crowd out graph facts."""

    class FakeReceipt:
        query_ready = True
        build_status = GraphStatus.READY_WITH_DECLARED_LIMITATIONS
        repository = str(tmp_path)
        commit_sha = "a" * 40
        source_revision = "b" * 64
        graph_checksum_or_identity = "c" * 64
        degraded_reasons = ("generated:1", "excluded_directory_files:162")

    class FakeService:
        root = tmp_path

        def status(self):
            return FakeReceipt()

    target = ContextEvidenceItem(
        kind="symbol_identity",
        path="repl/repl.go",
        start_line=90,
        end_line=100,
        symbol="BeginRepl",
        relation="",
        confidence=1.0,
        verification_status="verified",
        source_revision="b" * 64,
        graph_revision="c" * 64,
        evidence_sha256="d" * 64,
        decision_reason="exact_task_symbol",
        completeness="exact_identity",
        facet_ids=("facet-repl",),
    )
    facets = (
        TaskFacet(
            facet_id="facet-repl",
            obligation_ids=("obligation-repl",),
            role=LocalizationRole.EDIT,
            exact_symbols=("BeginRepl",),
        ),
        *(
            TaskFacet(
                facet_id=f"facet-unresolved-{index}",
                obligation_ids=(f"obligation-{index}",),
                role=LocalizationRole.EDIT,
                unresolved_symbols=(f"VeryLongUnresolvedSymbol{index}",),
            )
            for index in range(12)
        ),
    )
    process = (
        "gt-process-strong lower_bound=true anchor=repl/repl.go#BeginRepl "
        "req=facet-repl main.go#main -> repl/repl.go#BeginRepl -> "
        "repl/repl.go#Run -> runner/runner.go#Run -> "
        "evaluator/evaluator.go#BeginEval -> evaluator/evaluator.go#Eval "
        "[edge=817,resolution=exact;edge=1233,resolution=exact;"
        "edge=1223,resolution=exact;edge=1240,resolution=exact]"
    )
    impact = (
        "gt-impact-strong anchor=repl/repl.go#BeginRepl req=facet-repl "
        "depth=1 CALLS main.go#main direction=reverse edge=817"
    )
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "b" * 64},
        task_facets=facets,
        primary_edit_targets=(target,),
        execution_paths=(process,),
        change_surface=(impact,),
        uncovered_facets=tuple(
            f"facet-unresolved-{index} role=EDIT "
            f"unresolved=VeryLongUnresolvedSymbol{index}"
            for index in range(12)
        ),
        evidence_items=(target,),
        projection_claim_ids=("gt-process-strong", "gt-impact-strong"),
        uncertainties=(
            "chunk_character_limit",
            "no_decision_relevant_evidence",
            "no_complete_evidence",
            "unverified_edge_rejected",
        ),
        coverage={"dense_index": {"status": "READY", "query_ready": True}},
    )
    treatment = GroundTruthTreatment(tmp_path)
    treatment.service = FakeService()
    treatment.treatment_status = TreatmentStatus.ACTIVE
    monkeypatch.setattr(GroundTruthTreatment, "_context", lambda self, **_kwargs: packet)

    rendered = treatment._render(update=False, budget=6_000, delivered_before_call=1)

    assert _bounded_token_count(rendered) <= 500
    assert "EXACT_EDIT_TARGET repl/repl.go:90#BeginRepl" in rendered
    assert "BOUNDED_PROCESS gt-process-strong" in rendered
    assert "BOUNDED_IMPACT gt-impact-strong" in rendered
    assert "REQUIREMENT facet-repl" in rendered
    assert "REQUIREMENT facet-unresolved-0" not in rendered


def test_provider_context_preserves_decision_facts_before_candidate_noise(
    tmp_path: Path, monkeypatch
) -> None:
    """The provider boundary, not the internal packet, defines feature delivery."""

    class FakeReceipt:
        query_ready = True
        build_status = GraphStatus.READY
        repository = str(tmp_path)
        commit_sha = "a" * 40
        source_revision = "b" * 64
        graph_checksum_or_identity = "c" * 64
        degraded_reasons = ()

    class FakeService:
        root = tmp_path

        def status(self):
            return FakeReceipt()

    def item(kind: str, path: str, symbol: str, digest: str) -> ContextEvidenceItem:
        return ContextEvidenceItem(
            kind=kind,
            path=path,
            start_line=1,
            end_line=1,
            symbol=symbol,
            relation="CALLS" if kind == "relationship" else "",
            confidence=1.0,
            verification_status="verified",
            source_revision="b" * 64,
            graph_revision="c" * 64,
            evidence_sha256=digest * 64,
            decision_reason=f"verified_{kind}",
            completeness="certified_direct_edge" if kind == "relationship" else "exact_identity",
            source_path="src/caller.ts" if kind == "relationship" else "",
            source_symbol="callFeature" if kind == "relationship" else "",
            source_excerpt="export function feature(value: string): Result",
            facet_ids=("requirement-1",),
        )

    edit = item("symbol_identity", "src/feature.ts", "feature", "a")
    public = item("public_surface", "src/index.ts", "feature", "b")
    integration = item("integration_surface", "src/caller.ts", "callFeature", "c")
    relation = item("relationship", "src/feature.ts", "feature", "d")
    relation_two = item("relationship", "src/parser.ts", "parseFeature", "f")
    semantic = item("semantic_fact", "src/feature.ts", "feature", "e")
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "b" * 64},
        primary_edit_targets=(edit,),
        inspection_public_surface=(public,),
        inspection_integration=(integration,),
        semantic_facts=("src/feature.ts:1 feature argument value flows to parse.value",),
        execution_paths=(
            "gt-process-one lower_bound=true src/caller.ts#callFeature -> src/feature.ts#feature",
        ),
        change_surface=(
            "gt-impact-one depth=1 CALLS src/caller.ts#callFeature direction=incoming",
        ),
        affected_tests=("tests/feature.test.ts",),
        validation_plan=("npm test -- tests/feature.test.ts checks requirement-1",),
        uncertainties=tuple(f"low_value_retrieval_uncertainty_{index}" for index in range(16)),
        evidence_items=(edit, public, integration, relation, relation_two, semantic),
        projection_claim_ids=("gt-process-one", "gt-impact-one"),
        coverage={
            "documents_considered": 400,
            "ranked_files": 40,
            "certified_edges_selected": 6,
            "rejected_edges": 120,
            "retrieval_mode": "hybrid_required",
            "dense_candidates": 12,
            "dense_index": {"status": "READY", "query_ready": True},
        },
    )
    treatment = GroundTruthTreatment(tmp_path)
    treatment.service = FakeService()
    treatment.treatment_status = treatment.treatment_status.ACTIVE
    treatment.start_token_budget = 500
    monkeypatch.setattr(GroundTruthTreatment, "_context", lambda self, **_kwargs: packet)

    rendered = treatment._render(update=False, budget=6_000, delivered_before_call=1)

    assert _bounded_token_count(rendered) <= treatment.start_token_budget
    assert "SEMANTIC_FACT " in rendered
    assert "BOUNDED_PROCESS " in rendered
    assert "BOUNDED_IMPACT " in rendered
    assert "AFFECTED_TEST " in rendered
    assert "VALIDATE " in rendered
    assert rendered.count("VERIFIED_RELATION ") == 2


def test_delivered_claim_ids_equal_provider_visible_claims_after_compaction(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeReceipt:
        query_ready = True
        build_status = GraphStatus.READY
        repository = str(tmp_path)
        commit_sha = "a" * 40
        source_revision = "b" * 64
        graph_checksum_or_identity = "c" * 64
        degraded_reasons = ()

    class FakeService:
        root = tmp_path

        def status(self):
            return FakeReceipt()

    edit = ContextEvidenceItem(
        kind="symbol_identity",
        path="src/feature.ts",
        start_line=1,
        end_line=1,
        symbol="feature",
        relation="",
        confidence=1.0,
        verification_status="verified",
        source_revision="b" * 64,
        graph_revision="c" * 64,
        evidence_sha256="a" * 64,
        decision_reason="exact_task_symbol",
        completeness="exact_identity",
    )
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "b" * 64},
        primary_edit_targets=(edit,),
        projection_claim_ids=("gt-process-not-serialized",),
        evidence_items=(edit,),
        uncertainties=tuple("noise-" + ("x" * 80) + str(index) for index in range(20)),
        coverage={"dense_index": {"status": "READY", "query_ready": True}},
    )
    treatment = GroundTruthTreatment(tmp_path)
    treatment.service = FakeService()
    treatment.treatment_status = treatment.treatment_status.ACTIVE
    monkeypatch.setattr(GroundTruthTreatment, "_context", lambda self, **_kwargs: packet)

    treatment._render(update=False, budget=6_000, delivered_before_call=1)

    visible = {"a" * 64}
    assert treatment.delivered_claim_ids == visible


def test_persisted_graph_projection_uses_multiple_role_diverse_anchors(
    tmp_path: Path, monkeypatch
) -> None:
    def item(path: str, symbol: str, role: str, digest: str) -> ContextEvidenceItem:
        return ContextEvidenceItem(
            kind=role.lower(),
            path=path,
            start_line=1,
            end_line=1,
            symbol=symbol,
            relation="",
            confidence=1.0,
            verification_status="verified",
            source_revision="s",
            graph_revision="g",
            evidence_sha256=digest * 64,
            decision_reason="fixture",
            completeness="exact_identity",
            localization_role=role,
            facet_ids=(f"facet-{role.lower()}",),
        )

    edit = item("src/evaluation.rs", "evaluate", "EDIT", "d")
    integration = item("src/job.rs", "run_jobs", "INTEGRATION", "e")
    weak = ContextEvidenceItem(
        kind="inspection_candidate",
        path="src/source.rs",
        start_line=1,
        end_line=1,
        symbol="transition",
        relation="",
        confidence=0.9,
        verification_status="verified_source_identity",
        source_revision="s",
        graph_revision="g",
        evidence_sha256="f" * 64,
        decision_reason="hybrid_retrieval_inspection",
        completeness="ranked_candidate_not_edit_target",
        localization_role="UNCERTAIN",
    )
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "s"},
        primary_edit_targets=(edit,),
        inspection_integration=(integration,),
        inspection_candidates=(weak,),
        evidence_items=(edit, integration, weak),
    )
    projection_receipt = ProjectionReceipt(
        repository=str(tmp_path),
        commit_sha="a" * 40,
        source_revision="s",
        graph_identity="g",
        lower_bound=True,
        truncated=False,
        truncation_reasons=(),
        limits={},
    )
    calls: list[tuple[str, str, str]] = []

    class FakeProjector:
        def __init__(self, _service):
            pass

        def __enter__(self):
            calls.append(("session", "open", ""))
            return self

        def __exit__(self, *_args):
            calls.append(("session", "close", ""))

        def project_processes(self, symbol: str, *, file_path: str):
            calls.append(("process", file_path, symbol))
            return ProcessProjection(
                ProjectionStatus.READY,
                symbol,
                file_path,
                None,
                (),
                (),
                projection_receipt,
            )

        def project_impact(self, symbol: str, *, file_path: str):
            calls.append(("impact", file_path, symbol))
            return ImpactProjection(
                ProjectionStatus.READY,
                symbol,
                file_path,
                None,
                (),
                (),
                projection_receipt,
            )

    monkeypatch.setattr("gt_harness.treatments.PersistedGraphProjector", FakeProjector)
    treatment = GroundTruthTreatment(tmp_path)

    treatment._project_persisted_graph(packet)

    assert ("process", "src/evaluation.rs", "evaluate") in calls
    assert ("process", "src/job.rs", "run_jobs") in calls
    assert ("process", "src/source.rs", "transition") not in calls
    assert calls.count(("session", "open", "")) == 1
    assert calls.count(("session", "close", "")) == 1


def test_persisted_graph_projection_reserves_anchor_for_each_available_role(
    tmp_path: Path, monkeypatch
) -> None:
    def item(index: int, role: str) -> ContextEvidenceItem:
        return ContextEvidenceItem(
            kind=role.lower(),
            path=f"src/{role.lower()}_{index}.py",
            start_line=1,
            end_line=1,
            symbol=f"{role.title()}{index}",
            relation="",
            confidence=1.0,
            verification_status="verified",
            source_revision="s",
            graph_revision="g",
            evidence_sha256=f"{index:x}" * 64,
            decision_reason="fixture",
            completeness="exact_identity",
            localization_role=role,
            facet_ids=(f"facet-{role.lower()}-{index}",),
        )

    edits = tuple(item(index, "EDIT") for index in range(3))
    public = item(4, "PUBLIC_SURFACE")
    integration = item(5, "INTEGRATION")
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "s"},
        primary_edit_targets=edits,
        inspection_public_surface=(public,),
        inspection_integration=(integration,),
        evidence_items=(*edits, public, integration),
    )
    projection_receipt = ProjectionReceipt(
        repository=str(tmp_path),
        commit_sha="a" * 40,
        source_revision="s",
        graph_identity="g",
        lower_bound=True,
        truncated=False,
        truncation_reasons=(),
        limits={},
    )
    calls: list[tuple[str, str]] = []

    class FakeProjector:
        def __init__(self, _service):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def project_processes(self, symbol: str, *, file_path: str):
            calls.append((file_path, symbol))
            return ProcessProjection(
                ProjectionStatus.READY,
                symbol,
                file_path,
                None,
                (),
                (),
                projection_receipt,
            )

        def project_impact(self, symbol: str, *, file_path: str):
            return ImpactProjection(
                ProjectionStatus.READY,
                symbol,
                file_path,
                None,
                (),
                (),
                projection_receipt,
            )

    monkeypatch.setattr("gt_harness.treatments.PersistedGraphProjector", FakeProjector)

    GroundTruthTreatment(tmp_path)._project_persisted_graph(packet)

    assert (public.path, public.symbol) in calls
    assert (integration.path, integration.symbol) in calls
    assert len(calls) == 4


def test_treatment_adds_manifest_public_surface_as_inspection_only(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "container.ts").write_text(
        "export interface Container {}\n", encoding="utf-8"
    )
    (tmp_path / "src" / "index.ts").write_text(
        "export { Container } from './container'\n", encoding="utf-8"
    )
    (tmp_path / "package.json").write_text(
        '{"exports":"./src/index.ts"}', encoding="utf-8"
    )
    edit = ContextEvidenceItem(
        kind="symbol_identity",
        path="src/container.ts",
        start_line=1,
        end_line=1,
        symbol="Container",
        relation="",
        confidence=1.0,
        verification_status="verified",
        source_revision="s",
        graph_revision="g",
        evidence_sha256="d" * 64,
        decision_reason="exact_task_symbol",
        completeness="exact_identity",
        facet_ids=("facet-container",),
    )
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "s", "graph_revision": "g"},
        primary_edit_targets=(edit,),
        evidence_items=(edit,),
    )
    treatment = GroundTruthTreatment(tmp_path)

    updated = treatment._resolve_public_surfaces(packet)

    assert [item.path for item in updated.inspection_public_surface] == [
        "src/index.ts"
    ]
    assert updated.inspection_public_surface[0].localization_role == "PUBLIC_SURFACE"
    assert updated.inspection_public_surface[0].facet_ids == edit.facet_ids
    assert updated.primary_edit_targets == (edit,)


def test_treatment_prioritizes_manifest_surface_over_incidental_reexport(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "container.ts").write_text(
        "export function register() {}\n", encoding="utf-8"
    )
    (tmp_path / "src" / "awilix.ts").write_text(
        "export { register } from './container'\n", encoding="utf-8"
    )
    (tmp_path / "src" / "incidental.ts").write_text(
        "export const unrelated = 1\n", encoding="utf-8"
    )
    (tmp_path / "package.json").write_text(
        '{"exports":"./src/awilix.ts"}', encoding="utf-8"
    )
    edit = ContextEvidenceItem(
        kind="symbol_identity",
        path="src/container.ts",
        start_line=1,
        end_line=1,
        symbol="register",
        relation="",
        confidence=1.0,
        verification_status="verified",
        source_revision="s",
        graph_revision="g",
        evidence_sha256="d" * 64,
        decision_reason="exact_task_symbol",
        completeness="exact_identity",
    )
    incidental = ContextEvidenceItem(
        kind="public_surface",
        path="src/incidental.ts",
        start_line=1,
        end_line=1,
        symbol="unrelated",
        relation="RE_EXPORTS",
        confidence=1.0,
        verification_status="verified",
        source_revision="s",
        graph_revision="g",
        evidence_sha256="e" * 64,
        decision_reason="certified_reexport_public_surface",
        completeness="certified_public_surface_edge",
        localization_role="PUBLIC_SURFACE",
    )
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "s", "graph_revision": "g"},
        primary_edit_targets=(edit,),
        inspection_public_surface=(incidental,),
        evidence_items=(edit, incidental),
    )

    updated = GroundTruthTreatment(tmp_path)._resolve_public_surfaces(packet)

    assert [item.path for item in updated.inspection_public_surface] == [
        "src/awilix.ts",
        "src/incidental.ts",
    ]


def test_feature_receipt_tracks_delivery_and_behavioral_follow_through(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeReceipt:
        query_ready = True
        build_status = GraphStatus.READY
        repository = str(tmp_path)
        commit_sha = "a" * 40
        source_revision = "b" * 64
        graph_checksum_or_identity = "c" * 64
        degraded_reasons = ()

    class FakeService:
        root = tmp_path

        def status(self):
            return FakeReceipt()

    source = tmp_path / "src" / "index.ts"
    source.parent.mkdir()
    source.write_text("export const answer = 42\n", encoding="utf-8")
    public = ContextEvidenceItem(
        kind="public_surface",
        path="src/index.ts",
        start_line=1,
        end_line=1,
        symbol="answer",
        relation="",
        confidence=1.0,
        verification_status="verified",
        source_revision="b" * 64,
        graph_revision="c" * 64,
        evidence_sha256="d" * 64,
        decision_reason="package_manifest_entrypoint",
        completeness="existing_public_surface_file",
        localization_role="PUBLIC_SURFACE",
        facet_ids=("facet-answer",),
    )
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "b" * 64},
        inspection_public_surface=(public,),
        evidence_items=(public,),
        coverage={"dense_index": {"status": "DISABLED", "query_ready": False}},
    )
    treatment = GroundTruthTreatment(tmp_path)
    treatment.service = FakeService()
    treatment.treatment_status = treatment.treatment_status.ACTIVE
    monkeypatch.setattr(GroundTruthTreatment, "_context", lambda self, **_kwargs: packet)

    treatment._render(update=False, budget=4_000, delivered_before_call=1)
    assert treatment.feature_states["public_surface"] == "AVAILABLE_TO_AGENT"

    treatment.after_action(
        "bash",
        {"command": "sed -n '1,20p' src/index.ts"},
        "src/index.ts:1:export const answer = 42",
        False,
    )
    assert treatment.feature_states["public_surface"] == "FOLLOWED"

    treatment.after_action(
        "bash",
        {"command": "pytest -q"},
        "1 passed",
        False,
    )
    assert treatment.feature_states["public_surface"] == "FOLLOWED"


def test_provider_delivery_receipts_reconcile_visible_facts_and_call_timing(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeReceipt:
        query_ready = True
        build_status = GraphStatus.READY
        repository = str(tmp_path)
        commit_sha = "a" * 40
        source_revision = "b" * 64
        graph_checksum_or_identity = "c" * 64
        degraded_reasons = ()
        receipt_schema = "gt.graph_receipt.v1"
        graph_builder_version = "test"

    class FakeService:
        root = tmp_path
        receipt_path = tmp_path / "graph-receipt.json"

        def status(self):
            return FakeReceipt()

    source = tmp_path / "src" / "engine.py"
    source.parent.mkdir()
    source.write_text("def execute_task():\n    return 1\n", encoding="utf-8")
    identity = ContextEvidenceItem(
        kind="symbol_identity",
        path="src/engine.py",
        start_line=1,
        end_line=2,
        symbol="execute_task",
        relation="",
        confidence=1.0,
        verification_status="verified",
        source_revision="b" * 64,
        graph_revision="c" * 64,
        evidence_sha256="d" * 64,
        decision_reason="exact_task_symbol",
        completeness="exact_identity",
        facet_ids=("facet-execute",),
    )
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "b" * 64},
        primary_edit_targets=(identity,),
        evidence_items=(identity,),
        coverage={"dense_index": {"status": "DISABLED", "query_ready": False}},
    )
    treatment = GroundTruthTreatment(tmp_path)
    treatment.service = FakeService()
    treatment.treatment_status = TreatmentStatus.ACTIVE
    monkeypatch.setattr(GroundTruthTreatment, "_context", lambda self, **_kwargs: packet)

    treatment._render(update=False, budget=4_000, delivered_before_call=1)
    treatment.before_model_call(1)
    treatment.context_dirty = True
    augmentation = treatment.after_actions(
        (
            ActionObservation(
                name="bash",
                arguments={"command": "sed -n '1,20p' src/engine.py"},
                output="first observation",
                is_error=False,
            ),
            ActionObservation(
                name="bash",
                arguments={"command": "sed -n '1,20p' src/engine.py"},
                output="final observation",
                is_error=False,
            ),
        )
    )
    assert augmentation is not None
    assert augmentation.observation_count == 2
    assert augmentation.raw_output_sha256 == hashlib.sha256(
        b"final observation"
    ).hexdigest()
    assert augmentation.turn_observations_sha256
    receipt = treatment.finalize(None)

    assert receipt["delivery_calls"] == [1, 2]
    assert [row["delivered_before_call"] for row in receipt["provider_delivery_receipts"]] == [1, 2]
    serialized = {
        claim
        for row in receipt["provider_delivery_receipts"]
        for claim in row["serialized_claim_ids"]
    }
    assert serialized == set(receipt["delivered_claim_ids"])
    assert receipt["delivery_reconciliation"] == "PASS"

def test_candidate_only_feature_cannot_be_reported_as_followed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "src" / "decoy.py"
    path.parent.mkdir()
    path.write_text("def decoy():\n    return 1\n", encoding="utf-8")
    treatment = GroundTruthTreatment(tmp_path)
    treatment._feature_transition(
        "semantic_facts",
        FeatureState.CANDIDATE,
        paths=("src/decoy.py",),
    )

    treatment.after_action(
        "bash",
        {"command": "sed -n '1,20p' src/decoy.py"},
        "src/decoy.py:1:def decoy():",
        False,
    )

    assert treatment.feature_states["semantic_facts"] == "CANDIDATE"

def test_feature_receipt_does_not_attribute_unrelated_edit_to_observed_path(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeReceipt:
        query_ready = True
        build_status = GraphStatus.READY
        repository = str(tmp_path)
        commit_sha = "a" * 40
        source_revision = "b" * 64
        graph_checksum_or_identity = "c" * 64
        degraded_reasons = ()
        git_status_paths: tuple[str, ...] = ()

    class FakeService:
        root = tmp_path

        def status(self):
            return FakeReceipt()

    source = tmp_path / "src" / "index.ts"
    source.parent.mkdir()
    source.write_text("export const answer = 42\n", encoding="utf-8")
    public = ContextEvidenceItem(
        kind="public_surface",
        path="src/index.ts",
        start_line=1,
        end_line=1,
        symbol="answer",
        relation="",
        confidence=1.0,
        verification_status="verified",
        source_revision="b" * 64,
        graph_revision="c" * 64,
        evidence_sha256="d" * 64,
        decision_reason="package_manifest_entrypoint",
        completeness="existing_public_surface_file",
        localization_role="PUBLIC_SURFACE",
        facet_ids=("facet-answer",),
    )
    packet = GTContextPacket(
        status=ContextStatus.READY,
        repository_identity={"source_revision": "b" * 64},
        inspection_public_surface=(public,),
        evidence_items=(public,),
        coverage={"dense_index": {"status": "DISABLED", "query_ready": False}},
    )
    treatment = GroundTruthTreatment(tmp_path)
    treatment.service = FakeService()
    treatment.treatment_status = treatment.treatment_status.ACTIVE
    monkeypatch.setattr(GroundTruthTreatment, "_context", lambda self, **_kwargs: packet)
    treatment._render(update=False, budget=4_000, delivered_before_call=1)

    (tmp_path / "other.ts").write_text("export const other = 1\n", encoding="utf-8")
    FakeReceipt.build_status = GraphStatus.STALE
    FakeReceipt.git_status_paths = ("other.ts",)
    treatment.treatment_status = treatment.treatment_status.NOT_APPLICABLE
    treatment.after_action(
        "bash",
        {"command": "sed -n '1,20p' src/index.ts"},
        "src/index.ts:1:export const answer = 42",
        False,
    )

    assert treatment.feature_states["public_surface"] == "FOLLOWED"


def test_bounded_token_count_does_not_undercount_long_underscored_identifiers() -> None:
    identifier = "this_is_a_very_long_repository_symbol_name"

    assert _bounded_token_count(identifier) >= (len(identifier) + 3) // 4


@pytest.mark.real_graph
def test_groundtruth_treatment_rebuilds_and_delivers_updated_real_graph(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)
    (root / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
    (root / "caller.py").write_text(
        "from app import answer\n\ndef invoke():\n    return answer()\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)

    treatment = GroundTruthTreatment(root, state_dir=tmp_path / "state")
    initial = treatment.prepare("Change `answer` without breaking invoke")
    compile_count = treatment.context_compile_count
    delivery_count = treatment.delivery_count
    assert treatment.prepare("Change `answer` without breaking invoke") == initial
    assert treatment.context_compile_count == compile_count
    assert treatment.delivery_count == delivery_count
    initial_revision = treatment.finalize(None)["source_revision"]
    assert "EXACT_EDIT_TARGET app.py:1#answer" in initial
    assert "BOUNDED_PROCESS gt-process-" in initial

    (root / "app.py").write_text(
        "def answer(value: int = 1):\n    return 41 + value\n",
        encoding="utf-8",
    )
    augmentation = treatment.after_action(
        "edit_file",
        {"path": "app.py"},
        "updated app.py",
        False,
    )
    assert augmentation is not None, json.dumps(treatment.finalize(None), sort_keys=True)
    updated = augmentation.content
    assert treatment.before_model_call(2) == ""
    assert updated, json.dumps(treatment.finalize(None), sort_keys=True)
    assert len(updated) <= treatment.update_char_budget
    assert 'kind="repository_update"' in updated
    receipt = treatment.finalize(None)
    assert receipt["treatment_status"] == "ACTIVE"
    assert receipt["delivery_count"] == 2
    assert receipt["source_revision"] != initial_revision
    assert receipt["initial_context"] == initial
    assert receipt["initial_context_sha256"]
    assert augmentation.context_token_count <= treatment.update_token_budget
    assert "context_budget_too_small" not in receipt["errors"]
    assert receipt["delivery_receipts"][-1]["same_observation"] is True
    assert treatment.feature_states["exact_edit_targets"] == "EDITED"

    treatment.after_action("bash", {"command": "pytest -q"}, "1 passed", False)
    assert treatment.feature_states["exact_edit_targets"] == "EDITED"


@pytest.mark.real_graph
def test_groundtruth_treatment_refreshes_after_delivery_budget_is_exhausted(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)
    (root / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)

    treatment = GroundTruthTreatment(root, state_dir=tmp_path / "state")
    treatment.prepare("Change `answer`")
    initial_revision = treatment.finalize(None)["source_revision"]
    treatment.max_delivery_count = treatment.delivery_count

    (root / "app.py").write_text(
        "def answer(value: int = 1):\n    return 41 + value\n",
        encoding="utf-8",
    )
    augmentation = treatment.after_action(
        "bash",
        {"command": "python changed app.py"},
        "updated app.py",
        False,
    )

    assert augmentation is None
    assert treatment.before_model_call(2) == ""
    receipt = treatment.finalize(None)
    assert receipt["graph_status"] in {"READY", "READY_WITH_DECLARED_LIMITATIONS"}
    assert receipt["source_revision"] != initial_revision


def test_official_bare_and_groundtruth_arms_use_the_identical_agent_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    import gt_harness.miniswe_runner as runner
    from gt_harness.cli import _run_agent

    captures: list[dict[str, object]] = []

    def build(**kwargs):
        captures.append(kwargs)
        return SimpleNamespace(
            config=SimpleNamespace(
                system_template="miniswe-system",
                instance_template="miniswe-task={{task}}",
            ),
            treatment=kwargs["treatment"],
        )

    def run(agent, _task):
        return SimpleNamespace(
            stop_reason="Submitted",
            iterations=1,
            total_input_tokens=10,
            total_output_tokens=2,
            total_cache_read_tokens=0,
            transcript=({"role": "assistant", "content": "done"},),
        )

    monkeypatch.setattr(runner, "build_miniswe_agent", build)
    monkeypatch.setattr(runner, "run_miniswe_agent", run)
    common = {
        "task": "Fix the parser",
        "model": "provider/model",
        "base_url": "https://provider.invalid",
        "max_iterations": 30,
        "time_budget_seconds": 120.0,
        "root": str(tmp_path),
        "temperature": 0.0,
        "run_id": None,
        "output": None,
        "state_dir": None,
    }
    assert _run_agent(SimpleNamespace(**common, treatment="bare")) == 0
    assert _run_agent(SimpleNamespace(**common, treatment="groundtruth")) == 0

    assert captures[0]["model"] == captures[1]["model"]
    assert captures[0]["base_url"] == captures[1]["base_url"]
    assert captures[0]["max_iterations"] == captures[1]["max_iterations"]
    assert captures[0]["time_budget_seconds"] == captures[1]["time_budget_seconds"]
    assert isinstance(captures[0]["treatment"], BareTreatment)
    assert isinstance(captures[1]["treatment"], GroundTruthTreatment)
    run_root = tmp_path / ".groundtruth" / "runs"
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in run_root.glob("*.json")]
    assert len(receipts) == 2
    assert {receipt["treatment"] for receipt in receipts} == {"bare", "groundtruth"}
    assert all(receipt["treatment_receipt_present"] for receipt in receipts)
    assert all(receipt["resolved"] is None for receipt in receipts)
    assert all(receipt["task_fingerprint"] for receipt in receipts)
    assert all(receipt["task_id"].startswith("task-") for receipt in receipts)
    assert all(receipt["trial_id"] == "1" for receipt in receipts)
    assert all(
        receipt["agent_scaffold"] == "minisweagent.agents.default.DefaultAgent"
        for receipt in receipts
    )
    assert all(receipt["agent_scaffold_version"] == "2.4.6" for receipt in receipts)
    assert len({receipt["system_prompt_sha256"] for receipt in receipts}) == 1
    assert len({receipt["tool_policy_sha256"] for receipt in receipts}) == 1
    assert len({receipt["repository_start"]["source_revision"] for receipt in receipts}) == 1


def test_run_cli_checkpoints_receipt_before_agent_finishes(
    tmp_path: Path, monkeypatch
) -> None:
    import gt_harness.miniswe_runner as runner
    from gt_harness.cli import _run_agent

    output = tmp_path / "gt-run.json"

    def build(**kwargs):
        return SimpleNamespace(
            config=SimpleNamespace(system_template="system", instance_template="task"),
            on_message=kwargs["on_message"],
            treatment=kwargs["treatment"],
        )

    def run(agent, _task):
        initial = json.loads(output.read_text(encoding="utf-8"))
        assert initial["status"] == "RUNNING"
        assert initial["provider_calls"] == 0
        agent.on_message({"role": "assistant", "content": "working", "extra": {}})
        checkpoint = json.loads(output.read_text(encoding="utf-8"))
        assert checkpoint["status"] == "RUNNING"
        assert checkpoint["provider_calls"] == 1
        return SimpleNamespace(
            stop_reason="Submitted",
            iterations=1,
            total_input_tokens=12,
            total_output_tokens=3,
            total_cache_read_tokens=0,
            transcript=({"role": "assistant", "content": "working"},),
        )

    monkeypatch.setattr(runner, "build_miniswe_agent", build)
    monkeypatch.setattr(runner, "run_miniswe_agent", run)
    args = SimpleNamespace(
        task="Do work",
        model="provider/model",
        base_url="https://provider.invalid",
        max_iterations=3,
        time_budget_seconds=30.0,
        root=str(tmp_path),
        temperature=0.0,
        run_id="checkpoint",
        task_id="task-checkpoint",
        trial_id="1",
        output=str(output),
        state_dir=None,
        treatment="bare",
    )

    assert _run_agent(args) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "COMPLETED"
    if os.name != "nt":
        assert output.stat().st_mode & stat.S_IROTH


def test_run_cli_setup_failure_preserves_reason_and_treatment_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    import gt_harness.miniswe_runner as runner
    from gt_harness.cli import _run_agent

    output = tmp_path / "gt-run.json"

    def build(**kwargs):
        return SimpleNamespace(
            config=SimpleNamespace(system_template="system", instance_template="task"),
            treatment=kwargs["treatment"],
        )

    def fail_prepare(self, _task):
        self.treatment_status = TreatmentStatus.FAILED
        self.errors.append("FAILED:context_budget_too_small")
        raise TreatmentUnavailableError("FAILED:context_budget_too_small")

    monkeypatch.setattr(runner, "build_miniswe_agent", build)
    monkeypatch.setattr(GroundTruthTreatment, "prepare", fail_prepare)
    args = SimpleNamespace(
        task="Do complex work",
        model="provider/model",
        base_url="https://provider.invalid",
        max_iterations=3,
        time_budget_seconds=30.0,
        root=str(tmp_path),
        temperature=0.0,
        run_id="setup-error",
        task_id="task-setup-error",
        trial_id="1",
        output=str(output),
        state_dir=None,
        treatment="groundtruth",
    )

    assert _run_agent(args) == 1
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == "ERROR"
    assert receipt["error_type"] == "TreatmentUnavailableError"
    assert receipt["error"] == "FAILED:context_budget_too_small"
    assert receipt["treatment_receipt_present"] is True
    assert "FAILED:context_budget_too_small" in receipt["treatment_receipt"]["errors"]


def test_run_cli_preserves_provider_usage_and_transcript_on_runtime_error(
    tmp_path: Path, monkeypatch
) -> None:
    import gt_harness.miniswe_runner as runner
    from gt_harness.cli import _run_agent

    output = tmp_path / "gt-run.json"

    trajectory_path = None

    def build(**kwargs):
        nonlocal trajectory_path
        trajectory_path = kwargs["trajectory_path"]
        return SimpleNamespace(
            config=SimpleNamespace(system_template="system", instance_template="task"),
            on_message=kwargs["on_message"],
            treatment=kwargs["treatment"],
        )

    def run(agent, _task):
        agent.on_message(
            {
                "role": "assistant",
                "content": "working",
                "extra": {
                    "response": {
                        "usage": {
                            "prompt_tokens": 12,
                            "completion_tokens": 3,
                            "prompt_tokens_details": {"cached_tokens": 4},
                        }
                    }
                },
            }
        )
        agent.on_message(
            {
                "role": "tool",
                "content": "changed app.py",
                "extra": {"returncode": 0},
            }
        )
        assert trajectory_path is not None
        trajectory_path.write_text(
            json.dumps({"info": {"model_stats": {"api_calls": 2}}, "messages": []}),
            encoding="utf-8",
        )
        raise TreatmentUnavailableError("FAILED:unobserved_repository_change")

    monkeypatch.setattr(runner, "build_miniswe_agent", build)
    monkeypatch.setattr(runner, "run_miniswe_agent", run)
    args = SimpleNamespace(
        task="Do work",
        model="provider/model",
        base_url="https://provider.invalid",
        max_iterations=3,
        time_budget_seconds=30.0,
        root=str(tmp_path),
        temperature=0.0,
        run_id="runtime-error",
        task_id="task-runtime-error",
        trial_id="1",
        output=str(output),
        state_dir=None,
        treatment="bare",
    )

    assert _run_agent(args) == 1
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == "ERROR"
    assert receipt["provider_calls"] == 2
    assert receipt["input_tokens"] == 12
    assert receipt["output_tokens"] == 3
    assert receipt["cached_tokens"] == 4
    assert [event.get("role") for event in receipt["transcript"][:2]] == [
        "assistant",
        "tool",
    ]
