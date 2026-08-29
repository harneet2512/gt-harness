from __future__ import annotations

import json
from dataclasses import replace

from gt_engine.hybrid_retrieval import (
    EvidenceAuthority,
    EvidenceOrigin,
    HybridRetrievalResult,
    HybridRetriever,
    RankedFile,
    RepositoryDocument,
    RetrievalCandidate,
    RetrievalChannel,
    RetrievalIntent,
    RetrievalState,
    StructuralLink,
)
from gt_engine.persistent_execution_state import (
    SELECT_CATALOG_TOOL_NAME,
    BootstrapCatalog,
    BootstrapCatalogItem,
    BootstrapMode,
    BootstrapStatus,
    CatalogItemKind,
    CompletionReadiness,
    ContextFrameKind,
    CurrentFocusKind,
    PersistentExecutionStateEngine,
    StatePhase,
    StateValidationStatus,
    attempted_bootstrap_item_ids,
    bootstrap_visible_item_ids,
    build_bootstrap_catalog,
    build_bootstrap_messages,
    build_select_catalog_tool,
    deterministic_bootstrap_selection,
    parse_bootstrap_selection,
)
from gt_engine.preflight import adapt_proposed_action
from gt_engine.repository_intelligence import RepositoryEvidence


def _document(path: str, symbol: str, line: int = 1) -> RepositoryDocument:
    return RepositoryDocument(
        path=path,
        start_line=line,
        end_line=line + 2,
        symbol=symbol,
        text=f"def {symbol}():\n    return None",
        provenance=("graph_node",),
    )


def _evidence(source_revision: str = "source-1") -> RepositoryEvidence:
    return RepositoryEvidence(
        available=True,
        graph_revision="graph-1",
        anchors=(
            {
                "path": "src/service.py",
                "line": 10,
                "symbol": "save_user",
                "semantic_certainty": 1.0,
                "retrieval_relevance": 1.0,
            },
        ),
        definitions=({"path": "src/service.py", "line": 10, "symbol": "save_user"},),
        callers=(
            {
                "path": "src/api.py",
                "line": 24,
                "symbol": "create_user",
                "target_path": "src/service.py",
                "target_symbol": "save_user",
            },
        ),
        project_checks=("pytest tests/test_service.py -q",),
        status="source_backed",
        source_revision=source_revision,
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
    )


def _links() -> tuple[StructuralLink, ...]:
    return (
        StructuralLink(
            source_path="src/api.py",
            target_path="src/service.py",
            relation="CALLS",
            confidence=1.0,
            provenance=("graph_edge:CALLS",),
            certified=True,
            source_symbol="create_user",
            target_symbol="save_user",
        ),
        StructuralLink(
            source_path="src/service.py",
            target_path="tests/test_service.py",
            relation="ASSERTED_BY",
            confidence=1.0,
            provenance=("resolved_test_assertion",),
            certified=True,
            source_symbol="save_user",
            target_symbol="test_save_user",
        ),
        StructuralLink(
            source_path="src/service.py",
            target_path="notes/history.md",
            relation="commit_set_cochange",
            confidence=1.0,
            provenance=("commit_set_cochange",),
            certified=False,
        ),
    )


def _catalog() -> BootstrapCatalog:
    return build_bootstrap_catalog(
        instruction="Fix save_user and run pytest tests/test_service.py -q.",
        evidence=_evidence(),
        documents=(
            _document("src/service.py", "save_user", 10),
            _document("src/api.py", "create_user", 24),
            _document("tests/test_service.py", "test_save_user", 5),
        ),
        structural_links=_links(),
        explicit_checks=("pytest tests/test_service.py -q",),
        task_deliverables=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
    )


def _proposed(
    command: str,
    *,
    source_revision: str = "source-1",
    call: int = 1,
    batch_index: int = 0,
    batch_size: int = 1,
):
    return adapt_proposed_action(
        {"command": command, "tool_call_id": f"action-{call}"},
        source_revision=source_revision,
        workspace_revision=f"workspace-{call}",
        model_call=call,
        batch_index=batch_index,
        batch_size=batch_size,
    )


def test_graph_first_catalog_contains_only_bounded_certified_inputs():
    catalog = _catalog()

    assert catalog.complete is True
    assert len(catalog.items) <= 32
    assert catalog.source_revision == "source-1"
    assert catalog.graph_revision == "graph-1"
    assert any(item.path == "src/service.py" for item in catalog.items)
    assert any(item.kind.value == "validation" for item in catalog.items)
    assert all("notes/history.md" not in item.anchors for item in catalog.items)
    assert all(item.item_id.startswith("pes-") for item in catalog.items)
    assert all(item.required for item in catalog.items if item.kind.value == "validation")


def test_inferred_project_check_is_a_nonblocking_candidate_not_a_task_requirement():
    catalog = build_bootstrap_catalog(
        instruction="Fix save_user.",
        evidence=_evidence(),
        documents=(_document("src/service.py", "save_user"),),
        structural_links=(),
        explicit_checks=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
    )
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=(),
        present_paths=("src/service.py",),
    )

    validation = next(item for item in catalog.items if item.kind.value == "validation")
    assert validation.label.startswith("Project validation candidate:")
    assert validation.required is False
    assert engine.snapshot.obligations == ()


def test_graph_incomplete_catalog_fails_closed_without_items():
    catalog = build_bootstrap_catalog(
        instruction="Fix save_user",
        evidence=_evidence("source-2"),
        documents=(_document("src/service.py", "save_user"),),
        structural_links=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=False,
    )

    assert catalog.complete is False
    assert catalog.items == ()
    assert "repository_corpus_incomplete" in catalog.reason_codes


def test_graph_without_task_conditioned_catalog_abstains_before_bootstrap():
    evidence = RepositoryEvidence(
        available=True,
        graph_revision="graph-1",
        status="source_backed",
        source_revision="source-1",
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
    )

    catalog = build_bootstrap_catalog(
        instruction="Fix the behavior.",
        evidence=evidence,
        documents=(_document("src/unrelated.py", "unrelated"),),
        structural_links=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
    )

    assert catalog.complete is False
    assert catalog.items == ()
    assert catalog.reason_codes == ("empty_catalog",)


def test_shared_hybrid_retrieval_seeds_bootstrap_when_legacy_graph_query_is_empty():
    evidence = RepositoryEvidence(
        available=True,
        graph_revision="graph-1",
        status="source_backed",
        source_revision="source-1",
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
    )
    documents = (
        _document("src/compressor.py", "write_compressed"),
        _document("src/unrelated.py", "unrelated"),
    )
    state = RetrievalState(
        task_text="Fix write_compressed output truncation.",
        intent=RetrievalIntent.IMPLEMENTATION_CONTEXT,
        source_revision="source-1",
    )
    result = HybridRetriever(documents).retrieve(
        state,
        channel_limit=20,
        top_k=8,
        selection_limit=2,
        token_budget=400,
    )

    catalog = build_bootstrap_catalog(
        instruction=state.task_text,
        evidence=evidence,
        documents=documents,
        structural_links=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
        initial_retrieval=result,
    )

    ranked = [item for item in catalog.items if item.retrieval_rank]
    assert catalog.complete is True
    assert ranked
    assert ranked[0].path == "src/compressor.py"
    assert ranked[0].certified is False
    assert "hybrid_ranked_candidate" in ranked[0].provenance
    assert {"lexical", "bm25"} <= set(ranked[0].support_channels)


def test_task_ranked_catalog_items_precede_generic_graph_candidates_in_visible_budget():
    generic_documents = tuple(
        _document(f"src/generic_{index}.py", f"generic_{index}") for index in range(12)
    )
    relevant = _document("src/module_cache.py", "require_cache_info")
    documents = (*generic_documents, relevant)
    evidence = RepositoryEvidence(
        available=True,
        graph_revision="graph-1",
        anchors=tuple(
            {
                "path": document.path,
                "line": document.start_line,
                "symbol": document.symbol,
            }
            for document in generic_documents
        ),
        status="source_backed",
        source_revision="source-1",
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
    )
    state = RetrievalState(
        task_text="Update `require_cache_info()` for ABS_MODULE_PATH.",
        intent=RetrievalIntent.IMPLEMENTATION_CONTEXT,
        source_revision="source-1",
    )
    result = HybridRetriever(documents).retrieve(state, selection_limit=2, token_budget=400)

    catalog = build_bootstrap_catalog(
        instruction=state.task_text,
        evidence=evidence,
        documents=documents,
        structural_links=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
        initial_retrieval=result,
    )
    relevant_item = next(item for item in catalog.items if item.path == "src/module_cache.py")
    messages = build_bootstrap_messages(
        task=state.task_text,
        catalog=catalog,
        max_input_tokens=2_000,
    )

    assert catalog.items.index(relevant_item) < next(
        index for index, item in enumerate(catalog.items) if item.path.startswith("src/generic_")
    )
    assert relevant_item.item_id in bootstrap_visible_item_ids(messages)


def test_valid_bootstrap_may_abstain_from_ranked_focus_without_forced_anchor():
    catalog = _catalog()

    selection = parse_bootstrap_selection(
        json.dumps(
            {
                "primary_focus_id": "",
                "ordered_item_ids": [],
                "risk_item_ids": [],
                "validation_item_ids": [],
            }
        ),
        catalog,
    )

    assert selection.valid is True
    assert selection.primary_focus_id == ""


def test_invalid_bootstrap_uses_deterministic_fallback_without_silencing_state():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=_links(),
        present_paths=("src/service.py",),
    )

    engine.apply_bootstrap(
        parse_bootstrap_selection("not-json", catalog),
        current_source_revision="source-1",
    )
    frame = engine.compile_context(
        current_source_revision="source-1", provider_call=1, max_tokens=512
    )

    assert engine.snapshot.bootstrap_status.value == "invalid_fallback"
    assert engine.snapshot.bootstrap_mode is BootstrapMode.DETERMINISTIC_FALLBACK
    assert engine.snapshot.primary_focus_id
    assert frame.kind is not ContextFrameKind.NONE
    assert "Required run_validation" in frame.rendered_text
    assert "deterministic_fallback" in frame.reason_codes


def test_deterministic_bootstrap_selection_is_valid_and_preserves_requirements():
    catalog = _catalog()

    selection = deterministic_bootstrap_selection(catalog)

    assert selection.valid is True
    assert selection.risk_item_ids == ()
    assert "deterministic_selected" in selection.reason_codes
    required = {item.item_id for item in catalog.items if item.required}
    assert required <= set(selection.ordered_item_ids) | set(selection.validation_item_ids)

    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=_links(),
        present_paths=("src/service.py",),
    )
    engine.apply_bootstrap(
        selection,
        current_source_revision="source-1",
        selection_mode=BootstrapMode.DETERMINISTIC_SELECTED,
    )
    assert engine.snapshot.bootstrap_status is BootstrapStatus.SELECTED
    assert engine.snapshot.bootstrap_mode is BootstrapMode.DETERMINISTIC_SELECTED


def test_invalid_bootstrap_ids_do_not_permanently_silence_context():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=_links(),
        present_paths=("src/service.py",),
    )
    selection = parse_bootstrap_selection(
        json.dumps(
            {
                "primary_focus_id": "unknown-id",
                "ordered_item_ids": [],
                "risk_item_ids": [],
                "validation_item_ids": [],
            }
        ),
        catalog,
    )

    engine.apply_bootstrap(selection, current_source_revision="source-1")
    frame = engine.compile_context(
        current_source_revision="source-1", provider_call=1, max_tokens=512
    )

    assert selection.reason_codes == ("unknown_catalog_id",)
    assert frame.kind is ContextFrameKind.INITIAL
    assert frame.rendered_text


def test_ranked_focus_is_never_rendered_as_certified_relevance():
    evidence = RepositoryEvidence(
        available=True,
        graph_revision="graph-1",
        status="source_backed",
        source_revision="source-1",
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
    )
    documents = (_document("src/compressor.py", "write_compressed"),)
    state = RetrievalState(
        task_text="Fix write_compressed output truncation.",
        intent=RetrievalIntent.IMPLEMENTATION_CONTEXT,
        source_revision="source-1",
    )
    result = HybridRetriever(documents).retrieve(state, token_budget=400)
    catalog = build_bootstrap_catalog(
        instruction=state.task_text,
        evidence=evidence,
        documents=documents,
        structural_links=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
        initial_retrieval=result,
    )
    ranked = next(item for item in catalog.items if item.retrieval_rank == 1)
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task=state.task_text,
        catalog=catalog,
        structural_links=(),
        present_paths=(ranked.path,),
    )
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": ranked.item_id,
                    "ordered_item_ids": [ranked.item_id],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )

    frame = engine.compile_context(
        current_source_revision="source-1", provider_call=1, max_tokens=512
    )

    assert frame.rendered_text == ""
    assert frame.kind is ContextFrameKind.NONE
    assert frame.reason_codes == ("no_certified_related_file",)
    assert engine.snapshot.current_focus_path == ""


def test_bootstrap_selection_is_strictly_catalog_bounded():
    catalog = _catalog()
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    check = next(item.item_id for item in catalog.items if item.kind.value == "validation")

    selection = parse_bootstrap_selection(
        json.dumps(
            {
                "primary_focus_id": focus,
                "ordered_item_ids": [focus, check],
                "risk_item_ids": [],
                "validation_item_ids": [check],
            }
        ),
        catalog,
    )

    assert selection.valid is True
    assert selection.primary_focus_id == focus
    assert selection.validation_item_ids == (check,)

    invalid = parse_bootstrap_selection(
        json.dumps(
            {
                "primary_focus_id": "invented-file",
                "ordered_item_ids": ["invented-file"],
                "risk_item_ids": [],
                "validation_item_ids": [],
            }
        ),
        catalog,
    )
    assert invalid.valid is False
    assert invalid.primary_focus_id == ""
    assert "unknown_catalog_id" in invalid.reason_codes


def test_bootstrap_transport_uses_select_catalog_with_bounded_source_evidence():
    catalog = _catalog()
    messages = build_bootstrap_messages(
        task="Fix save_user and its tests.",
        catalog=catalog,
        max_input_tokens=2_000,
    )

    serialized = json.dumps(messages, sort_keys=True)
    assert len(messages) == 2
    assert "primary_focus_id" in serialized
    assert "select_catalog" in serialized
    assert "bash tool" not in serialized.lower()
    assert "def save_user" in serialized
    from gt_engine.persistent_execution_state import _default_token_counter

    assert sum(_default_token_counter(item["content"]) for item in messages) <= 2_000
    assert "source_excerpt" in serialized
    assert "evidence_authority" in serialized
    assert bootstrap_visible_item_ids(messages)


def test_bootstrap_cannot_select_a_catalog_item_omitted_by_the_request_budget():
    catalog = _catalog()
    shown = frozenset({catalog.items[0].item_id})
    hidden = catalog.items[-1].item_id

    selection = parse_bootstrap_selection(
        json.dumps(
            {
                "primary_focus_id": hidden,
                "ordered_item_ids": [hidden],
                "risk_item_ids": [],
                "validation_item_ids": [],
            }
        ),
        catalog,
        visible_item_ids=shown,
    )

    assert selection.valid is False
    assert selection.reason_codes == ("unshown_catalog_id",)


def test_bootstrap_goldens_reject_shell_and_old_bash_envelope():
    catalog = _catalog()
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    payload = {
        "primary_focus_id": focus,
        "ordered_item_ids": [focus],
        "risk_item_ids": [],
        "validation_item_ids": [],
    }

    heredoc = "cat <<'EOF'\n" + json.dumps(payload) + "\nEOF"
    echo_wrapped = "echo '" + json.dumps(payload) + "'"
    assert parse_bootstrap_selection(heredoc, catalog).reason_codes == ("invalid_json",)
    assert parse_bootstrap_selection(echo_wrapped, catalog).reason_codes == ("invalid_json",)
    assert parse_bootstrap_selection(json.dumps(payload)[:-8], catalog).reason_codes == (
        "invalid_json",
    )
    extra_keys = dict(payload)
    extra_keys["note"] = "extra"
    assert parse_bootstrap_selection(extra_keys, catalog).reason_codes == ("unknown_field",)
    assert parse_bootstrap_selection({"command": json.dumps(payload)}, catalog).reason_codes == (
        "unknown_tool",
    )


def test_bootstrap_drops_wrong_kind_validation_ids_without_zeroing_selection():
    catalog = _catalog()
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    check = next(item.item_id for item in catalog.items if item.kind.value == "validation")

    selection = parse_bootstrap_selection(
        {
            "primary_focus_id": focus,
            "ordered_item_ids": [focus],
            "risk_item_ids": [],
            "validation_item_ids": [focus, check],
        },
        catalog,
    )

    assert selection.valid is True
    assert selection.primary_focus_id == focus
    assert selection.validation_item_ids == (check,)


def test_empty_validation_item_ids_are_valid_when_catalog_has_none():
    catalog = BootstrapCatalog(
        source_revision="source-1",
        graph_source_revision="source-1",
        graph_revision="graph-1",
        items=(
            BootstrapCatalogItem(
                item_id="pes-aaaaaaaaaaaaaaaaaaaa",
                kind=CatalogItemKind.FOCUS,
                label="focus",
                path="src/a.py",
            ),
        ),
        complete=True,
    )

    selection = parse_bootstrap_selection(
        {
            "primary_focus_id": "pes-aaaaaaaaaaaaaaaaaaaa",
            "ordered_item_ids": ["pes-aaaaaaaaaaaaaaaaaaaa"],
            "risk_item_ids": [],
            "validation_item_ids": [],
        },
        catalog,
    )

    assert selection.valid is True
    assert selection.validation_item_ids == ()
    tool = build_select_catalog_tool(catalog.item_ids)
    assert tool["function"]["name"] == SELECT_CATALOG_TOOL_NAME
    assert "pes-aaaaaaaaaaaaaaaaaaaa" in tool["function"]["parameters"]["properties"][
        "primary_focus_id"
    ]["enum"]


def test_catalog_packing_keeps_certified_neighbors_inside_item_ceiling():
    documents = tuple(_document(f"src/f{index:02d}.py", f"sym{index:02d}") for index in range(40))
    evidence = RepositoryEvidence(
        available=True,
        graph_revision="graph-1",
        anchors=tuple(
            {"path": f"src/f{index:02d}.py", "line": 1, "symbol": f"sym{index:02d}"}
            for index in range(40)
        ),
        callers=(
            {
                "path": "src/f39.py",
                "line": 1,
                "symbol": "sym39",
                "target_path": "src/f00.py",
                "target_symbol": "sym00",
            },
        ),
        status="source_backed",
        source_revision="source-1",
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
    )
    catalog = build_bootstrap_catalog(
        instruction="Fix src/f00.py",
        evidence=evidence,
        documents=documents,
        structural_links=(
            StructuralLink(
                source_path="src/f39.py",
                target_path="src/f00.py",
                relation="CALLS",
                confidence=1.0,
                provenance=("graph_edge:CALLS",),
                certified=True,
                source_symbol="sym39",
                target_symbol="sym00",
            ),
        ),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
        max_items=8,
    )

    assert len(catalog.items) <= 8
    assert any(
        item.evidence_authority is EvidenceAuthority.CERTIFIED_RELATION
        for item in catalog.items
    )


def test_catalog_packing_keeps_hybrid_ranks_when_certified_imports_fill_ceiling():
    ranked_path = "src/ranked_impl.py"
    documents = (
        _document(ranked_path, "ranked_impl"),
        *(_document(f"src/dep{index:02d}.py", f"dep{index:02d}") for index in range(20)),
    )
    evidence = RepositoryEvidence(
        available=True,
        graph_revision="graph-1",
        status="source_backed",
        source_revision="source-1",
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
    )
    candidate = RetrievalCandidate(
        path=ranked_path,
        start_line=1,
        end_line=3,
        symbol="ranked_impl",
        text="def ranked_impl():\n    return None",
        channel=RetrievalChannel.LEXICAL,
        channel_rank=1,
        relation=None,
        provenance=("hybrid",),
        source_revision="source-1",
    )
    ranked = RankedFile(
        path=ranked_path,
        fused_score=1.0,
        channel_ranks=((RetrievalChannel.LEXICAL, 1),),
        representative=candidate,
        provenance=("hybrid",),
    )
    result = HybridRetrievalResult(
        ranked_files=(ranked,),
        ranked_spans=(candidate,),
        selected_context=(),
        abstained=False,
        reason_codes=(),
        channel_receipts=(),
        latency_ms=1.0,
        query_hash="q-hybrid-1",
        token_budget=400,
        selected_token_count=0,
    )
    catalog = build_bootstrap_catalog(
        instruction="Fix ranked_impl.",
        evidence=evidence,
        documents=documents,
        structural_links=tuple(
            StructuralLink(
                source_path=ranked_path,
                target_path=f"src/dep{index:02d}.py",
                relation="IMPORTS",
                confidence=1.0,
                provenance=("graph_edge:IMPORTS",),
                certified=True,
                source_symbol="ranked_impl",
                target_symbol=f"dep{index:02d}",
            )
            for index in range(20)
        ),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
        initial_retrieval=result,
        max_items=8,
    )

    hybrid = [item for item in catalog.items if item.retrieval_rank]
    assert len(catalog.items) <= 8
    assert hybrid
    assert hybrid[0].path == ranked_path
    assert "hybrid_ranked_candidate" in hybrid[0].provenance
    assert any(
        item.evidence_authority is EvidenceAuthority.CERTIFIED_RELATION
        for item in catalog.items
    )


def test_attempted_ids_survive_invalid_json_text():
    raw = 'echo pes-0123456789abcdef0123 && cat <<EOF\n{"x":1}\nEOF'
    assert attempted_bootstrap_item_ids(raw) == ("pes-0123456789abcdef0123",)


def test_state_is_reused_at_provider_preflight_postflight_and_rebase_boundaries():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user and run its test.",
        catalog=catalog,
        structural_links=_links(),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    check = next(item.item_id for item in catalog.items if item.kind.value == "validation")
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": focus,
                    "ordered_item_ids": [focus, check],
                    "risk_item_ids": [],
                    "validation_item_ids": [check],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )

    first = engine.compile_context(
        current_source_revision="source-1",
        provider_call=1,
        max_tokens=512,
    )
    assert "Bootstrap-selected next items:" not in first.rendered_text
    assert engine.mark_context_dispatched(first) is True
    read = _proposed("sed -n '1,120p' src/service.py")
    projection = engine.project_preflight(read, current_source_revision="source-1")
    engine.commit_postflight(
        read,
        returncode=0,
        output="def save_user(): pass",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="unknown",
    )
    second = engine.compile_context(
        current_source_revision="source-1",
        provider_call=2,
        max_tokens=256,
    )
    assert engine.mark_context_dispatched(second) is False
    edit = _proposed("sed -i 's/pass/return 1/' src/service.py", call=2)
    engine.project_preflight(edit, current_source_revision="source-1")
    engine.commit_postflight(
        edit,
        returncode=0,
        output="",
        changed_paths=("src/service.py",),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="unknown",
    )
    stale_graph_frame = engine.compile_context(
        current_source_revision="source-2",
        provider_call=3,
        max_tokens=256,
    )
    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=_links(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        changed_paths=("src/service.py",),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    third = engine.compile_context(
        current_source_revision="source-2",
        provider_call=4,
        max_tokens=256,
    )

    assert first.kind is ContextFrameKind.INITIAL
    assert projection.considered is True
    assert "src/service.py" in engine.snapshot.files_inspected
    assert "src/service.py" in engine.snapshot.files_modified
    assert engine.snapshot.phase is StatePhase.IMPLEMENTING
    assert engine.snapshot.current_focus_path == "src/service.py"
    assert engine.snapshot.validation.status is StateValidationStatus.PENDING
    assert {item.path for item in engine.snapshot.obligations if item.status.value == "open"} >= {
        "src/api.py",
        "tests/test_service.py",
    }
    assert second.kind is ContextFrameKind.NONE
    assert second.rendered_text == ""
    assert stale_graph_frame.kind is ContextFrameKind.NONE
    assert "graph_rebase_required" in stale_graph_frame.reason_codes
    assert third.source_revision == "source-2"
    assert "Related inspect_dependency: src/api.py" in third.rendered_text
    assert "Candidate implementation src/service.py:10" not in third.rendered_text
    assert engine.metrics["context_compilations"] == 4
    assert engine.metrics["preflight_projections"] == 2
    assert engine.metrics["postflight_commits"] == 2
    assert engine.metrics["graph_rebases"] == 1


def test_nonmaterial_action_keeps_version_and_abstains_without_stable_core():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=_links(),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": focus,
                    "ordered_item_ids": [focus],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )
    initial = engine.compile_context(
        current_source_revision="source-1", provider_call=1, max_tokens=512
    )
    assert engine.mark_context_dispatched(initial) is True
    version = engine.snapshot.version
    action = _proposed("echo ok")

    engine.project_preflight(action, current_source_revision="source-1")
    engine.commit_postflight(
        action,
        returncode=0,
        output="ok",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="unknown",
    )
    unchanged = engine.compile_context(
        current_source_revision="source-1", provider_call=2, max_tokens=512
    )

    assert initial.kind is ContextFrameKind.INITIAL
    assert engine.snapshot.version == version
    assert unchanged.kind is ContextFrameKind.NONE
    assert unchanged.rendered_text == ""
    assert unchanged.reason_codes == (
        "state_change_already_represented_or_not_model_material",
    )


def test_compiled_but_not_dispatched_claims_remain_eligible_until_dispatch():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=_links(),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": focus,
                    "ordered_item_ids": [focus],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )

    prepared = engine.compile_context(
        current_source_revision="source-1", provider_call=1, max_tokens=512
    )
    retried = engine.compile_context(
        current_source_revision="source-1", provider_call=2, max_tokens=512
    )

    assert prepared.kind is ContextFrameKind.INITIAL
    assert retried.kind is ContextFrameKind.INITIAL
    assert retried.claim_ids == prepared.claim_ids
    assert retried.rendered_text == prepared.rendered_text

    engine.mark_context_dispatched(prepared)
    stable = engine.compile_context(
        current_source_revision="source-1", provider_call=3, max_tokens=512
    )

    assert stable.kind is ContextFrameKind.NONE
    assert stable.rendered_text == ""


def test_same_revision_graph_rebase_is_a_semantic_noop():
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=_catalog(),
        structural_links=_links(),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    version = engine.snapshot.version
    transitions = engine.metrics["material_transitions"]

    engine.rebase_graph(
        evidence=_evidence("source-1"),
        structural_links=_links(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        graph_complete=True,
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )

    assert engine.snapshot.version == version
    assert engine.metrics["material_transitions"] == transitions


def test_repeated_failure_abstains_after_critical_dispatch_without_repeating_core():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=_links(),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": focus,
                    "ordered_item_ids": [focus],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )
    action = _proposed("pytest tests/test_service.py -q")
    engine.commit_postflight(
        action,
        returncode=1,
        output="tests/test_service.py:9: AssertionError: expected 2",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="fail",
    )
    critical = engine.compile_context(
        current_source_revision="source-1", provider_call=1, max_tokens=512
    )
    assert critical.kind is ContextFrameKind.CRITICAL
    assert engine.mark_context_dispatched(critical) is True

    unchanged = engine.compile_context(
        current_source_revision="source-1", provider_call=2, max_tokens=512
    )

    assert unchanged.kind is ContextFrameKind.NONE
    assert unchanged.rendered_text == ""


def test_unchanged_state_after_read_abstains_without_repeating_focus_excerpt():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=_links(),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": focus,
                    "ordered_item_ids": [focus],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )
    initial = engine.compile_context(
        current_source_revision="source-1", provider_call=1, max_tokens=512
    )
    assert engine.mark_context_dispatched(initial) is True
    read = _proposed("sed -n '1,80p' src/service.py")
    engine.commit_postflight(
        read,
        returncode=0,
        output="def save_user(): pass",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="unknown",
    )

    core = engine.compile_context(
        current_source_revision="source-1", provider_call=2, max_tokens=512
    )

    assert core.kind is ContextFrameKind.NONE
    assert core.rendered_text == ""


def test_graph_rebase_does_not_render_catalog_labels_from_an_old_graph_revision():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=_links(),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    stale_next = next(item.item_id for item in catalog.items if item.path == "src/api.py")
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": focus,
                    "ordered_item_ids": [focus, stale_next],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )
    edit = _proposed("sed -i 's/pass/return 1/' src/service.py")
    engine.commit_postflight(
        edit,
        returncode=0,
        output="",
        changed_paths=("src/service.py",),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="unknown",
    )
    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        changed_paths=("src/service.py",),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )

    delta = engine.compile_context(
        current_source_revision="source-2", provider_call=1, max_tokens=256
    )

    assert "Bootstrap-selected next items:" not in delta.rendered_text
    assert "create_user" not in delta.rendered_text


def test_graph_rebase_recomputes_advisory_obligations_from_current_edges_only():
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=_catalog(),
        structural_links=_links(),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    edit = _proposed("sed -i 's/pass/return 1/' src/service.py")
    engine.commit_postflight(
        edit,
        returncode=0,
        output="",
        changed_paths=("src/service.py",),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="unknown",
    )
    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        changed_paths=("src/service.py",),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )

    assert not any(
        item.status.value == "open" and item.relation != "task_requirement"
        for item in engine.snapshot.obligations
    )
    frame = engine.compile_context(
        current_source_revision="source-2", provider_call=1, max_tokens=256
    )
    assert "(calls from" not in frame.rendered_text
    assert "(test_assertion from" not in frame.rendered_text


def test_executed_batched_validation_is_committed_at_the_current_revision():
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user and run its test.",
        catalog=_catalog(),
        structural_links=_links(),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    edit = _proposed("sed -i 's/pass/return 1/' src/service.py", call=1)
    validation = _proposed(
        "pytest tests/test_service.py -q",
        call=1,
        batch_index=1,
        batch_size=2,
    )
    engine.commit_postflight(
        edit,
        returncode=0,
        output="",
        changed_paths=("src/service.py",),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="unknown",
    )
    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=_links(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        changed_paths=("src/service.py",),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )

    engine.commit_postflight(
        validation,
        returncode=0,
        output="1 passed",
        changed_paths=(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="pass",
        validation_check_id="pytest tests/test_service.py -q",
    )

    assert engine.snapshot.validation.status is StateValidationStatus.PASS
    assert engine.snapshot.validation.source_revision == "source-2"
    assert engine.receipts[-1]["disposition"] == "committed"


def test_same_revision_rebase_preserves_bootstrap_selected_optional_focus():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=_links(),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": focus,
                    "ordered_item_ids": [focus],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )
    before = engine.snapshot

    engine.rebase_graph(
        evidence=_evidence("source-1"),
        structural_links=_links(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        graph_complete=True,
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )

    assert engine.snapshot.version == before.version
    assert engine.snapshot.current_focus_id == focus
    assert engine.snapshot.ordered_item_ids == (focus,)


def test_identity_only_bootstrap_focus_does_not_echo_checkout_span():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user and run its test.",
        catalog=catalog,
        structural_links=_links(),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": focus,
                    "ordered_item_ids": [focus],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )

    frame = engine.compile_context(
        current_source_revision="source-1",
        provider_call=1,
        max_tokens=512,
    )

    # Identity-only focus may fall back to a certified related preexisting file,
    # but must never echo the identity-only checkout span as repository novelty.
    assert frame.kind is ContextFrameKind.INITIAL
    assert "Certified related repository file:" in frame.rendered_text
    assert "Candidate implementation src/service.py:10#save_user" not in frame.rendered_text
    assert "def save_user():" not in frame.rendered_text
    assert all(
        item.get("path") != "src/service.py" or item.get("authority") == "certified_relation"
        for item in frame.selected_evidence
    )

    read = _proposed("sed -n '1,40p' src/service.py")
    engine.commit_postflight(
        read,
        returncode=0,
        output="def save_user():\n    return None",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="unknown",
    )
    after_read = engine.compile_context(
        current_source_revision="source-1",
        provider_call=2,
        max_tokens=512,
    )

    assert "Bootstrap-selected repository context" not in after_read.rendered_text
    assert "def save_user():" not in after_read.rendered_text
    assert after_read.selected_evidence == ()


def test_bootstrap_selected_symbol_uses_its_exact_span_in_a_multi_symbol_file():
    catalog = build_bootstrap_catalog(
        instruction="Fix save_user in src/service.py.",
        evidence=_evidence(),
        documents=(
            RepositoryDocument(
                path="src/service.py",
                start_line=1,
                end_line=2,
                symbol="unrelated_helper",
                text="def unrelated_helper():\n    return 'wrong span'",
                provenance=("graph_node",),
            ),
            RepositoryDocument(
                path="src/service.py",
                start_line=10,
                end_line=12,
                symbol="save_user",
                text="def save_user():\n    return 'selected span'",
                provenance=("graph_node",),
            ),
        ),
        structural_links=(),
        explicit_checks=(),
        task_deliverables=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
    )
    focus = next(
        item
        for item in catalog.items
        if item.path == "src/service.py" and item.symbol == "save_user"
    )

    assert focus.source_start_line == 10
    assert focus.source_end_line == 12
    assert "selected span" in focus.source_excerpt
    assert "wrong span" not in focus.source_excerpt


def test_graph_discovery_after_edit_updates_state_without_a_second_model_plan():
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=_catalog(),
        structural_links=(),
        present_paths=("src/service.py", "src/api.py"),
    )
    edit = _proposed("sed -i 's/pass/return 1/' src/service.py")
    engine.commit_postflight(
        edit,
        returncode=0,
        output="",
        changed_paths=("src/service.py",),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="unknown",
    )
    discovered = StructuralLink(
        source_path="src/api.py",
        target_path="src/service.py",
        relation="CALLS",
        confidence=1.0,
        provenance=("graph_edge:CALLS",),
        certified=True,
    )

    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=(discovered,),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        changed_paths=("src/service.py",),
        present_paths=("src/service.py", "src/api.py"),
    )

    graph_obligations = [item for item in engine.snapshot.obligations if item.relation == "calls"]
    assert len(graph_obligations) == 1
    assert graph_obligations[0].path == "src/api.py"
    assert graph_obligations[0].blocking is False
    assert engine.metrics["bootstrap_applications"] == 0
    assert engine.metrics["graph_rebases"] == 1


def test_new_model_authored_graph_endpoint_never_becomes_repository_advice():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=(),
        present_paths=("src/service.py", "src/api.py"),
    )
    engine.apply_bootstrap(
        parse_bootstrap_selection("not-json", catalog),
        current_source_revision="source-1",
    )
    initial = engine.compile_context(
        current_source_revision="source-1", provider_call=1, max_tokens=512
    )
    engine.mark_context_dispatched(initial)

    created = _proposed("printf 'pass\\n' > scratch_probe.py")
    engine.commit_postflight(
        created,
        returncode=0,
        output="",
        changed_paths=("scratch_probe.py",),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="unknown",
    )
    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=(
            StructuralLink(
                source_path="scratch_probe.py",
                target_path="src/service.py",
                relation="CALLS",
                confidence=1.0,
                provenance=("graph_edge:CALLS",),
                certified=True,
            ),
        ),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        changed_paths=("scratch_probe.py",),
        present_paths=("src/service.py", "src/api.py", "scratch_probe.py"),
    )

    obligation = next(
        item for item in engine.snapshot.obligations if item.relation == "calls"
    )
    assert obligation.source_origin is EvidenceOrigin.MODEL_AUTHORED
    assert obligation.path_origin is EvidenceOrigin.PREEXISTING_REPOSITORY
    frame = engine.compile_context(
        current_source_revision="source-2", provider_call=2, max_tokens=512
    )
    assert "scratch_probe.py" not in frame.rendered_text
    assert all(
        row.get("origin") != EvidenceOrigin.MODEL_AUTHORED.value
        for row in frame.claim_metadata
    )


def test_relational_profile_keeps_advisory_obligations_controller_only():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=(),
        present_paths=("src/service.py", "src/api.py"),
    )
    engine.apply_bootstrap(
        parse_bootstrap_selection("not-json", catalog),
        current_source_revision="source-1",
    )
    initial = engine.compile_context(
        current_source_revision="source-1", provider_call=1, max_tokens=512
    )
    engine.mark_context_dispatched(initial)
    edit = _proposed("sed -i 's/pass/return 1/' src/service.py")
    engine.commit_postflight(
        edit,
        returncode=0,
        output="",
        changed_paths=("src/service.py",),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="unknown",
    )
    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=_links()[:1],
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        changed_paths=("src/service.py",),
        present_paths=("src/service.py", "src/api.py"),
    )

    frame = engine.compile_context(
        current_source_revision="source-2",
        provider_call=2,
        max_tokens=512,
        include_advisory_obligations=False,
    )

    assert frame.rendered_text == ""
    assert frame.kind is ContextFrameKind.NONE
    assert any(item.relation == "calls" for item in engine.snapshot.obligations)


def test_graph_advisories_do_not_create_a_false_submit_block():
    catalog = build_bootstrap_catalog(
        instruction="Fix save_user.",
        evidence=_evidence(),
        documents=(
            _document("src/service.py", "save_user"),
            _document("src/api.py", "create_user"),
        ),
        structural_links=_links()[:1],
        explicit_checks=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
    )
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=_links()[:1],
        present_paths=("src/service.py", "src/api.py"),
    )
    edit = _proposed("sed -i 's/pass/return 1/' src/service.py")
    engine.commit_postflight(
        edit,
        returncode=0,
        output="",
        changed_paths=("src/service.py",),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="unknown",
    )
    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=_links()[:1],
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        changed_paths=("src/service.py",),
        present_paths=("src/service.py", "src/api.py"),
    )
    submit = _proposed(
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        source_revision="source-2",
        call=2,
    )

    projection = engine.project_preflight(submit, current_source_revision="source-2")

    assert projection.open_obligation_ids
    assert projection.blocking_obligation_ids == ()
    assert projection.material_contradiction is False


def test_successful_current_validation_advances_ready_without_inventing_completion():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user and run its test.",
        catalog=catalog,
        structural_links=_links(),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    edit = _proposed("sed -i 's/pass/return 1/' src/service.py")
    engine.commit_postflight(
        edit,
        returncode=0,
        output="",
        changed_paths=("src/service.py",),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="unknown",
    )
    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=_links(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
    )
    test_action = _proposed("pytest tests/test_service.py -q", source_revision="source-2", call=2)
    engine.commit_postflight(
        test_action,
        returncode=0,
        output="1 passed",
        changed_paths=(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="pass",
    )

    assert engine.snapshot.validation.status is StateValidationStatus.PASS
    assert engine.snapshot.validation.source_revision == "source-2"
    assert engine.snapshot.phase in {StatePhase.VALIDATING, StatePhase.READY_TO_SUBMIT}
    assert engine.snapshot.current_failure is None


def test_same_revision_graph_refresh_does_not_reopen_satisfied_dependency_obligation():
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user and run its test.",
        catalog=_catalog(),
        structural_links=_links(),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    edit = _proposed("sed -i 's/pass/return 1/' src/service.py")
    engine.commit_postflight(
        edit,
        returncode=0,
        output="",
        changed_paths=("src/service.py",),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="unknown",
    )
    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=_links(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        changed_paths=("src/service.py",),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    test_action = _proposed(
        "pytest tests/test_service.py -q", source_revision="source-2", call=2
    )
    engine.commit_postflight(
        test_action,
        returncode=0,
        output="1 passed",
        changed_paths=(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="pass",
        validation_check_id="pytest tests/test_service.py -q",
    )
    satisfied = next(
        item
        for item in engine.snapshot.obligations
        if item.kind == "validate_related_test" and item.path == "tests/test_service.py"
    )
    assert satisfied.status.value == "satisfied"

    version = engine.snapshot.version
    transitions = engine.metrics["material_transitions"]
    obligations = engine.snapshot.obligations

    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=_links(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        changed_paths=("src/service.py",),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )

    assert engine.snapshot.version == version
    assert engine.metrics["material_transitions"] == transitions
    assert engine.snapshot.obligations == obligations

    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=_links(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        changed_paths=("src/service.py",),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )

    after_refresh = next(
        item
        for item in engine.snapshot.obligations
        if item.obligation_id == satisfied.obligation_id
    )
    assert after_refresh.status.value == "satisfied"


def test_canonical_declared_check_satisfies_obligation_through_shell_wrapper():
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user and run its test.",
        catalog=_catalog(),
        structural_links=_links(),
        present_paths=("src/service.py", "tests/test_service.py"),
    )
    wrapped = _proposed("timeout 120 pytest tests/test_service.py -q")

    engine.commit_postflight(
        wrapped,
        returncode=0,
        output="1 passed",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="pass",
        validation_check_id="pytest tests/test_service.py -q",
    )

    required_checks = [
        item
        for item in engine.snapshot.obligations
        if item.kind == "run_validation" and item.blocking
    ]
    assert len(required_checks) == 1
    assert required_checks[0].status.value == "satisfied"


def test_failed_validation_is_current_and_visible_in_critical_frame():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=_links(),
        present_paths=("src/service.py",),
    )
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": focus,
                    "ordered_item_ids": [focus],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )
    action = _proposed("pytest tests/test_service.py -q")
    engine.commit_postflight(
        action,
        returncode=1,
        output="tests/test_service.py:9: AssertionError: expected 2",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="fail",
    )
    frame = engine.compile_context(
        current_source_revision="source-1", provider_call=1, max_tokens=512
    )

    assert engine.snapshot.validation.status is StateValidationStatus.FAIL
    assert engine.snapshot.current_failure is not None
    assert frame.kind is ContextFrameKind.CRITICAL
    assert "AssertionError" in frame.rendered_text
    assert "pytest tests/test_service.py -q" in frame.rendered_text


def test_failure_already_in_provider_history_is_not_repeated_in_state_frame():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=_links(),
        present_paths=("src/service.py",),
    )
    engine.apply_bootstrap(
        parse_bootstrap_selection("not-json", catalog),
        current_source_revision="source-1",
    )
    engine.commit_postflight(
        _proposed("pytest tests/test_service.py -q"),
        returncode=1,
        output="tests/test_service.py:9: AssertionError: expected 2",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="fail",
    )

    frame = engine.compile_context(
        current_source_revision="source-1",
        provider_call=1,
        max_tokens=512,
        provider_messages=(
            {
                "role": "tool",
                "content": "tests/test_service.py:9: AssertionError: expected 2",
            },
        ),
    )

    assert "Current validation failure" not in frame.rendered_text
    assert "provider_history_already_contains_evidence" in frame.reason_codes


def test_unattributed_validation_exit_does_not_manufacture_failure():
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=_catalog(),
        structural_links=_links(),
        present_paths=("src/service.py",),
    )
    action = _proposed("pytest tests/test_service.py -q")

    engine.commit_postflight(
        action,
        returncode=1,
        output="the shell exit was not attributable",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="unknown",
    )

    assert engine.snapshot.validation.status is StateValidationStatus.PENDING
    assert engine.snapshot.current_failure is None
    assert engine.snapshot.last_transition == "validation_outcome_unattributed"


def test_reading_required_deliverable_does_not_satisfy_production_obligation():
    catalog = build_bootstrap_catalog(
        instruction="Write reports/final.json.",
        evidence=_evidence(),
        documents=(_document("src/service.py", "save_user"),),
        structural_links=(),
        task_deliverables=("reports/final.json",),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
    )
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Write reports/final.json.",
        catalog=catalog,
        structural_links=(),
        present_paths=("src/service.py", "reports/final.json"),
    )
    read = _proposed("cat reports/final.json")

    engine.commit_postflight(
        read,
        returncode=0,
        output="{}",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="unknown",
    )

    obligation = next(item for item in engine.snapshot.obligations if item.blocking)
    assert obligation.kind == "produce_deliverable"
    assert obligation.status.value == "open"


def test_stale_revision_never_mutates_or_emits_state():
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=_catalog(),
        structural_links=_links(),
        present_paths=("src/service.py",),
    )
    before = engine.snapshot
    stale = _proposed("sed -n '1,20p' src/service.py", source_revision="stale")

    projection = engine.project_preflight(stale, current_source_revision="source-1")
    engine.commit_postflight(
        stale,
        returncode=0,
        output="data",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="unknown",
    )
    frame = engine.compile_context(current_source_revision="other", provider_call=1, max_tokens=512)

    assert projection.considered is False
    assert "stale_proposed_revision" in projection.reason_codes
    assert engine.snapshot == before
    assert frame.rendered_text == ""
    assert "stale_source_revision" in frame.reason_codes


def test_non_app_workspace_root_is_canonicalized_for_state_paths():
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=_catalog(),
        structural_links=_links(),
        present_paths=("src/service.py",),
        workspace_root="/workspace/project",
    )
    action = _proposed("sed -n '1,20p' /workspace/project/src/service.py")

    projection = engine.project_preflight(action, current_source_revision="source-1")
    engine.commit_postflight(
        action,
        returncode=0,
        output="def save_user(): pass",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="unknown",
    )

    assert projection.target_paths == ("src/service.py",)
    assert engine.snapshot.files_inspected == ("src/service.py",)


def test_deterministic_replay_produces_identical_state_and_context():
    def run_once():
        engine = PersistentExecutionStateEngine.initialize_from_graph(
            task="Fix save_user.",
            catalog=_catalog(),
            structural_links=_links(),
            present_paths=("src/service.py",),
        )
        action = _proposed("sed -n '1,20p' src/service.py")
        engine.project_preflight(action, current_source_revision="source-1")
        engine.commit_postflight(
            action,
            returncode=0,
            output="def save_user(): pass",
            changed_paths=(),
            current_source_revision="source-1",
            current_graph_revision="graph-1",
            validation_status="unknown",
        )
        frame = engine.compile_context(
            current_source_revision="source-1", provider_call=1, max_tokens=256
        )
        return engine.snapshot.as_dict(), frame.as_dict(), engine.receipts

    assert run_once() == run_once()


def test_raw_commands_and_outputs_are_not_persisted_in_state_receipts():
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=_catalog(),
        structural_links=_links(),
        present_paths=("src/service.py",),
    )
    action = _proposed("python -c \"SECRET = 'do-not-store'\"")
    engine.project_preflight(action, current_source_revision="source-1")
    engine.commit_postflight(
        action,
        returncode=1,
        output="SECRET_OUTPUT do-not-store",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="unknown",
    )

    serialized = json.dumps(
        {"state": engine.snapshot.as_dict(), "receipts": engine.receipts}, sort_keys=True
    )
    assert "do-not-store" not in serialized
    assert "SECRET_OUTPUT" not in serialized


def test_state_receipt_exposes_the_determinism_boundary_field_by_field():
    snapshot = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=_catalog(),
        structural_links=_links(),
        present_paths=("src/service.py",),
    ).snapshot.as_dict()

    authorities = snapshot["field_authority"]
    assert authorities["primary_focus_id"] == "bootstrap_selected"
    assert authorities["current_focus"] == "executor_observed"
    assert authorities["phase"] == "deterministic_mutable"
    assert authorities["task_digest"] == "immutable_input"


def test_external_artifact_and_cache_paths_never_become_repository_focus():
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=_catalog(),
        structural_links=_links(),
        present_paths=("src/service.py",),
    )
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": "",
                    "ordered_item_ids": [],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            engine.catalog,
        ),
        current_source_revision="source-1",
    )

    for call, path in enumerate(
        (
            "/tmp/probe.py",
            "/root/.bash_profile",
            "/etc/bash.bashrc",
            "__pycache__/service.pyc",
            "build/service.bin",
            "data/results.json",
        ),
        start=1,
    ):
        engine.commit_postflight(
            _proposed(f"cat {path}", call=call),
            returncode=0,
            output="observed",
            changed_paths=(),
            current_source_revision="source-1",
            current_graph_revision="graph-1",
            validation_status="unknown",
        )
        assert engine.snapshot.current_focus is not None
        assert engine.snapshot.current_focus.kind is not CurrentFocusKind.REPOSITORY_SOURCE
        frame = engine.compile_context(
            current_source_revision="source-1", provider_call=call, max_tokens=512
        )
        assert "repository focus" not in frame.rendered_text.lower()


def test_task_deliverable_focus_is_labeled_as_task_status_not_repository_evidence():
    catalog = build_bootstrap_catalog(
        instruction="Write reports/final.json.",
        evidence=_evidence(),
        documents=(_document("src/service.py", "save_user"),),
        structural_links=(),
        task_deliverables=("reports/final.json",),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
    )
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Write reports/final.json.",
        catalog=catalog,
        structural_links=(),
        present_paths=("src/service.py",),
    )
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": "",
                    "ordered_item_ids": [],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )
    engine.commit_postflight(
        _proposed("cat reports/final.json"),
        returncode=0,
        output="{}",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="unknown",
    )
    frame = engine.compile_context(
        current_source_revision="source-1", provider_call=1, max_tokens=512
    )

    assert engine.snapshot.current_focus is not None
    assert engine.snapshot.current_focus.kind is CurrentFocusKind.TASK_DELIVERABLE
    assert "Current task execution status:" in frame.rendered_text
    assert "deliverable" in frame.rendered_text.lower()
    assert "repository focus" not in frame.rendered_text.lower()


def test_passing_self_authored_smoke_test_cannot_authorize_readiness():
    evidence = RepositoryEvidence(
        available=True,
        graph_revision="graph-1",
        status="source_backed",
        source_revision="source-1",
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
    )
    catalog = build_bootstrap_catalog(
        instruction="Implement src/service.py",
        evidence=evidence,
        documents=(
            RepositoryDocument(
                "src/service.py",
                "def service(): return 1",
                symbol="service",
                origin=EvidenceOrigin.MODEL_AUTHORED,
                origin_revision="source-2",
            ),
        ),
        structural_links=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
    )
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Implement src/service.py",
        catalog=catalog,
        structural_links=(),
        present_paths=("src/service.py",),
    )
    edit = _proposed("touch src/service.py")
    engine.commit_postflight(
        edit,
        returncode=0,
        output="",
        changed_paths=("src/service.py",),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="unknown",
    )
    engine.rebase_graph(
        evidence=replace(evidence, source_revision="source-2", graph_revision="graph-2"),
        structural_links=(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        present_paths=("src/service.py",),
    )
    engine.commit_postflight(
        _proposed("python smoke_test.py", source_revision="source-2", call=2),
        returncode=0,
        output="PASS: looks good",
        changed_paths=(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="pass",
        validation_check_id=None,
    )

    assert engine.snapshot.observed_validation.status is StateValidationStatus.PASS
    assert engine.snapshot.declared_validation.status is StateValidationStatus.UNKNOWN
    assert engine.snapshot.completion_readiness is CompletionReadiness.NOT_READY
    assert engine.snapshot.phase is not StatePhase.READY_TO_SUBMIT


def test_model_authored_bootstrap_focus_never_renders_its_source_excerpt():
    evidence = RepositoryEvidence(
        available=True,
        graph_revision="graph-1",
        anchors=({"path": "app.py", "line": 1, "symbol": "main"},),
        status="source_backed",
        source_revision="source-1",
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
    )
    catalog = build_bootstrap_catalog(
        instruction="Create app.py",
        evidence=evidence,
        documents=(
            RepositoryDocument(
                "app.py",
                "def main(): return 1",
                symbol="main",
                origin=EvidenceOrigin.MODEL_AUTHORED,
                origin_revision="source-1",
            ),
        ),
        structural_links=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
    )
    focus = next(item for item in catalog.items if item.kind.value == "focus")
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Create app.py",
        catalog=catalog,
        structural_links=(),
        present_paths=("app.py",),
        path_origins={"app.py": EvidenceOrigin.MODEL_AUTHORED},
    )
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": focus.item_id,
                    "ordered_item_ids": [focus.item_id],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )
    frame = engine.compile_context(
        current_source_revision="source-1", provider_call=1, max_tokens=512
    )

    assert focus.origin is EvidenceOrigin.MODEL_AUTHORED
    assert frame.selected_evidence == ()
    assert "def main()" not in frame.rendered_text


def test_declared_current_validation_can_authorize_readiness():
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user and run its test.",
        catalog=_catalog(),
        structural_links=(),
        present_paths=("src/service.py",),
    )
    engine.commit_postflight(
        _proposed("touch src/service.py"),
        returncode=0,
        output="",
        changed_paths=("src/service.py",),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="unknown",
    )
    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        present_paths=("src/service.py",),
    )
    engine.commit_postflight(
        _proposed("pytest tests/test_service.py -q", source_revision="source-2", call=2),
        returncode=0,
        output="1 passed",
        changed_paths=(),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="pass",
        validation_check_id="pytest tests/test_service.py -q",
    )

    assert engine.snapshot.declared_validation.status is StateValidationStatus.PASS
    assert engine.snapshot.completion_readiness is CompletionReadiness.READY
    assert engine.snapshot.phase is StatePhase.READY_TO_SUBMIT


def test_failure_diagnostic_skips_leading_success_banner():
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=_catalog(),
        structural_links=(),
        present_paths=("src/service.py",),
    )
    engine.commit_postflight(
        _proposed("pytest tests/test_service.py -q"),
        returncode=1,
        output="PASS: setup completed\ntrace detail\nAssertionError: expected 2, got 1\n1 failed",
        changed_paths=(),
        current_source_revision="source-1",
        current_graph_revision="graph-1",
        validation_status="fail",
    )

    assert engine.snapshot.current_failure is not None
    assert engine.snapshot.current_failure.diagnostic == "1 failed"


def test_visible_state_uses_neutral_headers_without_evaluator_branding():
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=_catalog(),
        structural_links=_links(),
        present_paths=("src/service.py", "src/api.py"),
    )
    engine.apply_bootstrap(
        parse_bootstrap_selection("not-json", engine.catalog),
        current_source_revision="source-1",
    )
    frame = engine.compile_context(
        current_source_revision="source-1", provider_call=1, max_tokens=512
    )
    lowered = frame.rendered_text.lower()

    assert (
        "current task execution status:" in lowered
        or "repository facts for the next decision:" in lowered
    )
    assert "groundtruth" not in lowered
    assert "hidden evaluator" not in lowered
    assert "reference implementation" not in lowered
    assert "repository-grounded" not in lowered


def test_import_advisories_stay_private_while_calls_remain_provider_visible():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=(
            *_links(),
            StructuralLink(
                source_path="src/service.py",
                target_path="tests/test_service.py",
                relation="IMPORTS",
                confidence=1.0,
                provenance=("graph_edge:IMPORTS",),
                certified=True,
            ),
        ),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": focus,
                    "ordered_item_ids": [focus],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )
    first = engine.compile_context(
        current_source_revision="source-1",
        provider_call=1,
        max_tokens=512,
    )
    assert engine.mark_context_dispatched(first) is True
    edit = _proposed("sed -i 's/pass/return 1/' src/service.py", call=1)
    engine.project_preflight(edit, current_source_revision="source-1")
    engine.commit_postflight(
        edit,
        returncode=0,
        output="",
        changed_paths=("src/service.py",),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="unknown",
    )
    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=(
            *_links(),
            StructuralLink(
                source_path="src/service.py",
                target_path="tests/test_service.py",
                relation="IMPORTS",
                confidence=1.0,
                provenance=("graph_edge:IMPORTS",),
                certified=True,
            ),
        ),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        changed_paths=("src/service.py",),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    frame = engine.compile_context(
        current_source_revision="source-2",
        provider_call=2,
        max_tokens=256,
    )

    import_obligations = [
        item
        for item in engine.snapshot.obligations
        if str(item.relation or "").lower() in {"imports", "imported_by"}
    ]
    assert import_obligations, "imports may remain private controller state"
    assert "IMPORTS" not in frame.rendered_text
    assert "imports from" not in frame.rendered_text.lower()
    assert "Related inspect_dependency: src/api.py" in frame.rendered_text


def test_implements_advisories_stay_private_while_calls_remain_provider_visible():
    catalog = _catalog()
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=(
            *_links(),
            StructuralLink(
                source_path="src/service.py",
                target_path="src/api.py",
                relation="IMPLEMENTS",
                confidence=1.0,
                provenance=("graph_edge:IMPLEMENTS",),
                certified=True,
            ),
        ),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": focus,
                    "ordered_item_ids": [focus],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )
    first = engine.compile_context(
        current_source_revision="source-1",
        provider_call=1,
        max_tokens=512,
    )
    assert engine.mark_context_dispatched(first) is True
    edit = _proposed("sed -i 's/pass/return 1/' src/service.py", call=1)
    engine.project_preflight(edit, current_source_revision="source-1")
    engine.commit_postflight(
        edit,
        returncode=0,
        output="",
        changed_paths=("src/service.py",),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        validation_status="unknown",
    )
    engine.rebase_graph(
        evidence=_evidence("source-2"),
        structural_links=(
            *_links(),
            StructuralLink(
                source_path="src/service.py",
                target_path="src/api.py",
                relation="IMPLEMENTS",
                confidence=1.0,
                provenance=("graph_edge:IMPLEMENTS",),
                certified=True,
            ),
        ),
        current_source_revision="source-2",
        current_graph_revision="graph-2",
        graph_complete=True,
        changed_paths=("src/service.py",),
        present_paths=("src/service.py", "src/api.py", "tests/test_service.py"),
    )
    frame = engine.compile_context(
        current_source_revision="source-2",
        provider_call=2,
        max_tokens=256,
    )

    assert any(
        str(item.relation or "").lower() == "implements"
        for item in engine.snapshot.obligations
    )
    assert "IMPLEMENTS" not in frame.rendered_text
    assert "implements from" not in frame.rendered_text.lower()
    assert "Related inspect_dependency: src/api.py" in frame.rendered_text


def test_implements_only_neighbor_is_not_dumped_as_certified_related_file():
    evidence = replace(
        _evidence(),
        callers=(),
    )
    catalog = build_bootstrap_catalog(
        instruction="Fix save_user.",
        evidence=evidence,
        documents=(
            _document("src/service.py", "save_user", 10),
            _document("src/api.py", "create_user", 24),
        ),
        structural_links=(
            StructuralLink(
                source_path="src/service.py",
                target_path="src/api.py",
                relation="IMPLEMENTS",
                confidence=1.0,
                provenance=("graph_edge:IMPLEMENTS",),
                certified=True,
                source_symbol="save_user",
                target_symbol="UserAPI",
            ),
        ),
        explicit_checks=(),
        task_deliverables=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
    )
    engine = PersistentExecutionStateEngine.initialize_from_graph(
        task="Fix save_user.",
        catalog=catalog,
        structural_links=(
            StructuralLink(
                source_path="src/service.py",
                target_path="src/api.py",
                relation="IMPLEMENTS",
                confidence=1.0,
                provenance=("graph_edge:IMPLEMENTS",),
                certified=True,
                source_symbol="save_user",
                target_symbol="UserAPI",
            ),
        ),
        present_paths=("src/service.py", "src/api.py"),
    )
    focus = next(item.item_id for item in catalog.items if item.path == "src/service.py")
    engine.apply_bootstrap(
        parse_bootstrap_selection(
            json.dumps(
                {
                    "primary_focus_id": focus,
                    "ordered_item_ids": [focus],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            ),
            catalog,
        ),
        current_source_revision="source-1",
    )
    frame = engine.compile_context(
        current_source_revision="source-1",
        provider_call=1,
        max_tokens=512,
    )

    assert any(
        item.relation == "implements"
        and item.evidence_authority is EvidenceAuthority.CERTIFIED_RELATION
        for item in catalog.items
    )
    assert "Certified related repository file:" not in frame.rendered_text
    assert "implements" not in frame.rendered_text.lower()
    assert "src/api.py" not in frame.rendered_text
    assert frame.kind is ContextFrameKind.NONE
    assert "no_material_certified_localization" in frame.reason_codes
