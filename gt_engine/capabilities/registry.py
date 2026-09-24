"""Canonical capability registry (plan item E).

One entry per capability exposed by ``gt_engine/capabilities/`` (the facade
surface) plus one entry per certified typed kind (the model-facing surface).
Both levels are registered deliberately: a facade is a host-side handle on a
capability, while the kind is the same capability as the model can invoke it
through the ``groundtruth`` tool. The mapping between them is recorded in
``typed_kinds`` / ``facades`` so the registry can be checked both ways.

``state`` is the highest rung the capability's substance truthfully reaches
today, judged on the ``implementation`` anchor — not on the facade object:

- ``COMPUTED``      — the engine computes it; the package does not expose it.
- ``AVAILABLE``     — exposed via the package API; its own output is never
                      delivered into model-visible text.
- ``SELECTED``      — chosen as a delivery candidate (none registered today).
- ``DELIVERED``     — admitted as a delivery (none registered today).
- ``MODEL_FACING``  — its substance is actually delivered into model-visible
                      output through ``delivery_path``.

``delivery_path`` names the lane that carries the substance: ``none``,
``typed_on_request`` (the model-selected ``groundtruth`` tool observation),
``gateway_auto:<producer>`` (a queued delivery admitted through
``admit_decision_packet``/``admit_model_visible_delivery``), ``plan_block``,
``steer``, or ``gate``.

Every ``implementation`` anchor is ``<file-or-module>:<qualname>`` verified
against real code by ``tests/canonical/test_registry_truth.py``.
"""
from __future__ import annotations

from dataclasses import dataclass

CLASSIFICATIONS = frozenset(
    {
        "INTELLIGENCE/STATE",
        "DELIVERY POLICY",
        "AGENT CONTROL",
        "BENCHMARK SCAFFOLD",
        "LEGACY",
    }
)

STATES = frozenset(
    {"COMPUTED", "AVAILABLE", "SELECTED", "DELIVERED", "MODEL_FACING"}
)

_DELIVERY_PATH_PREFIXES = ("none", "typed_on_request", "gateway_auto:", "plan_block", "steer", "gate")

TYPED_DISPATCH = "gt_engine/miniswe_typed_actions.py:execute_typed_action"

_TYPED_FACADE_LIMIT = (
    "host-side facade shares the certified pipeline "
    "(capabilities/_query.run_typed -> execute_typed_action); the facade "
    "object itself emits no model-facing text - delivery happens only when "
    "the model selects the groundtruth tool"
)


@dataclass(frozen=True, slots=True)
class CapabilityEntry:
    """One registered capability (facade surface or certified kind)."""

    name: str
    classification: str  # one of CLASSIFICATIONS
    state: str  # highest of STATES the implementation truthfully reaches
    delivery_path: str  # none | typed_on_request | gateway_auto:<p> | plan_block | steer | gate
    implementation: str  # canonical <file|module>:<qualname> anchor
    tests: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    facade: str | None = None  # gt_engine/capabilities/ function anchor
    facade_pending: bool = False  # plan-D name not yet present in the tree
    typed_kinds: tuple[str, ...] = ()  # certified kinds this entry delegates to
    facades: tuple[str, ...] = ()  # facade names delegating to this kind


def _e(
    name: str,
    classification: str,
    state: str,
    delivery_path: str,
    implementation: str,
    *,
    facade: str | None = None,
    facade_pending: bool = False,
    typed_kinds: tuple[str, ...] = (),
    facades: tuple[str, ...] = (),
    tests: tuple[str, ...] = (),
    limitations: tuple[str, ...] = (),
) -> CapabilityEntry:
    return CapabilityEntry(
        name=name,
        classification=classification,
        state=state,
        delivery_path=delivery_path,
        implementation=implementation,
        facade=facade,
        facade_pending=facade_pending,
        typed_kinds=typed_kinds,
        facades=facades,
        tests=tests,
        limitations=limitations,
    )


_F = "gt_engine/capabilities/freshness.py"
_L = "gt_engine/capabilities/localization.py"
_S = "gt_engine/capabilities/structure.py"
_A = "gt_engine/capabilities/analysis.py"
_C = "gt_engine/capabilities/change.py"
_R = "gt_engine/capabilities/runtime.py"

FACADE_ENTRIES: tuple[CapabilityEntry, ...] = (
    # ------------------------------------------------------------------ freshness
    _e(
        "freshness.index_revision",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        f"{_F}:index_revision",
        facade=f"{_F}:index_revision",
        tests=("tests/canonical/test_capability_facades.py::test_freshness_facade_reports_revisions_and_postures",),
        limitations=(
            "pure EngineState/adapter attribute read; the revision value rides "
            "inside typed-action honesty envelopes but no revision report is "
            "ever delivered to the model",
        ),
    ),
    _e(
        "freshness.graph_state",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        "gt_engine/engine_state.py:EngineState.query_snapshot",
        facade=f"{_F}:graph_state",
        tests=("tests/canonical/test_capability_facades.py::test_freshness_facade_reports_revisions_and_postures",),
        limitations=(
            "the same snapshot the adapter's graph_query_snapshot serves the "
            "typed path's graph binding; the state report itself is never "
            "rendered to the model",
        ),
    ),
    _e(
        "freshness.amend_state",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        "gt_engine/miniswe_integration.py:MiniSweAdapter._sync_amend_graph",
        facade=f"{_F}:amend_state",
        tests=("tests/test_miniswe_integration.py::test_an_edit_takes_the_amend_path_and_the_journal_says_which",),
        limitations=(
            "the amend mechanism is live on every recorded edit transaction "
            "and its outcome is journaled, never delivered",
        ),
    ),
    _e(
        "freshness.fallback_state",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        "gt_engine/miniswe_integration.py:MiniSweAdapter._recovery_build_inline",
        facade=f"{_F}:fallback_state",
        tests=(
            "tests/test_miniswe_integration.py::test_a_recovery_build_defers_while_the_memory_window_is_open",
            "tests/test_miniswe_integration.py::test_recovery_suspension_silences_the_per_action_resync_loop",
        ),
        limitations=(
            "recovery/rebuild streaks and the suspended flag are "
            "adapter-internal bookkeeping, journaled but never delivered",
        ),
    ),
    _e(
        "freshness.unit_state",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        "gt_engine/gt_session.py:GTSession.unit_state",
        facade=f"{_F}:unit_state",
        tests=(
            "tests/canonical/test_capability_facades.py::test_freshness_facade_reports_revisions_and_postures",
            "tests/canonical/test_unit_staleness.py::test_admitted_unit_is_stale_after_a_workspace_edit",
            "tests/canonical/test_unit_staleness.py::test_is_stale_is_none_for_unknown_or_undecidable_units",
        ),
        limitations=(
            "read-only freshness view; demotion/collapse consume "
            "_context_unit_rendered live-byte records, not this report",
        ),
    ),
    # ---------------------------------------------------------------- localization
    _e(
        "localization.lexical_search",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "typed_on_request",
        TYPED_DISPATCH,
        facade=f"{_L}:lexical_search",
        typed_kinds=("exact_literal_search",),
        tests=(
            "tests/canonical/test_capability_facades.py::test_lexical_search_runs_the_certified_literal_kind",
            "tests/test_typed_graph_dispatch.py::test_exact_literal_search_dispatches_replace_with_matches",
        ),
        limitations=(
            _TYPED_FACADE_LIMIT,
            "exact kind: 20-match/256-byte-line caps; a '.' scope also scans "
            ".git/ (documented producer defect)",
        ),
    ),
    _e(
        "localization.hybrid_rank",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "gateway_auto:localization",
        "gt_engine/retrieval.py:hybrid_rank",
        facade=f"{_L}:hybrid_rank",
        tests=(
            "tests/test_hybrid_retrieval.py::test_hybrid_rank_survives_null_bm25_lexical",
            "tests/test_miniswe_integration.py::test_lexical_localization_is_stable_advisory_and_includes_dirty_files",
            "tests/test_miniswe_integration.py::test_stale_or_unreadable_graph_localization_falls_back_to_lexical",
            "tests/canonical/test_capability_facades.py::test_localization_hybrid_rank_direct",
        ),
        limitations=(
            "the model receives the compacted top-4 render (<=1400 B) the "
            "task-start/drift localization lane produces from this ranking - "
            "never the raw HybridRanking; admission fire-once and soft/hard "
            "task ceilings bound redelivery",
        ),
    ),
    _e(
        "localization.definition",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "typed_on_request",
        TYPED_DISPATCH,
        facade=f"{_L}:definition",
        typed_kinds=("definition",),
        tests=(
            "tests/canonical/test_capability_facades.py::test_typed_facade_reports_unavailable_when_repo_root_missing",
            "tests/test_typed_graph_dispatch.py::test_definition_returns_the_graph_recorded_definition",
        ),
        limitations=(_TYPED_FACADE_LIMIT, "partial semantics: name-level resolution"),
    ),
    _e(
        "localization.references",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "typed_on_request",
        TYPED_DISPATCH,
        facade=f"{_L}:references",
        typed_kinds=("references",),
        tests=("tests/test_typed_graph_dispatch.py::test_references_groups_incoming_edges_by_type",),
        limitations=(
            _TYPED_FACADE_LIMIT,
            "partial semantics: name-level resolution; bands silently keep 20 "
            "rows while labelling the answer exact (documented wheel defect)",
        ),
    ),
    # ------------------------------------------------------------------ structure
    _e(
        "structure.callers",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "typed_on_request",
        TYPED_DISPATCH,
        facade=f"{_S}:callers",
        typed_kinds=("callers",),
        tests=("tests/test_typed_graph_dispatch.py::test_callers_walks_incoming_calls_edges_depth_banded",),
        limitations=(
            _TYPED_FACADE_LIMIT,
            "partial semantics: name-level resolution; 20 rows per band "
            "(documented wheel defect)",
        ),
    ),
    _e(
        "structure.callees",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "typed_on_request",
        TYPED_DISPATCH,
        facade=f"{_S}:callees",
        typed_kinds=("symbol_context",),
        tests=("tests/test_typed_graph_dispatch.py::test_symbol_context_returns_360_degree_view",),
        limitations=(
            _TYPED_FACADE_LIMIT,
            "no callee kind exists; surfaces the certified symbol_context "
            "answer's callees band verbatim",
        ),
    ),
    _e(
        "structure.symbol_context",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "typed_on_request",
        TYPED_DISPATCH,
        facade=f"{_S}:symbol_context",
        typed_kinds=("symbol_context",),
        tests=("tests/test_typed_graph_dispatch.py::test_symbol_context_returns_360_degree_view",),
        limitations=(_TYPED_FACADE_LIMIT, "partial semantics: name-level resolution"),
    ),
    _e(
        "structure.processes",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "typed_on_request",
        TYPED_DISPATCH,
        facade=f"{_S}:processes",
        typed_kinds=("processes",),
        tests=(
            "tests/test_typed_graph_dispatch.py::test_processes_lists_the_detected_entry_to_terminal_flow",
            "tests/test_typed_graph_dispatch.py::test_processes_concept_filter_matches_flow_members",
        ),
        limitations=(_TYPED_FACADE_LIMIT, "partial semantics"),
    ),
    _e(
        "structure.communities",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        f"{_S}:communities",
        facade=f"{_S}:communities",
        tests=(
            "tests/canonical/test_capability_facades.py::test_communities_reads_producer_tables",
            "tests/canonical/test_capability_facades.py::test_graph_backed_facades_abstain_without_a_graph",
        ),
        limitations=(
            "read-only lookup over producer-published communities/"
            "community_members tables; consumed downstream by "
            "graph_context.build_graph_projection - no model-facing render "
            "of community data observed",
        ),
    ),
    _e(
        "structure.framework_relationships",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "typed_on_request",
        TYPED_DISPATCH,
        facade=f"{_S}:framework_relationships",
        typed_kinds=("route_map", "api_impact", "tool_map", "symbol_context"),
        tests=(
            "tests/test_typed_graph_real_producer.py::test_route_map_reads_producer_route_edges",
            "tests/test_typed_graph_real_producer.py::test_api_impact_attributes_the_client_consumer",
            "tests/test_typed_graph_real_producer.py::test_tool_map_executes_and_names_what_it_cannot_see",
        ),
        limitations=(
            _TYPED_FACADE_LIMIT,
            "target shape picks the kind (path-like -> route_map, symbol -> "
            "symbol_context); kind override is constrained to route_map, "
            "api_impact, tool_map",
        ),
    ),
    # ------------------------------------------------------------------- analysis
    _e(
        "analysis.cfg",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        "groundtruth.runtime.cfg_store:analyze_stored",
        facade=f"{_A}:cfg",
        tests=(
            "tests/canonical/test_capability_facades.py::test_graph_backed_facades_abstain_without_a_graph",
            "tests/canonical/test_capability_facades.py::test_analysis_dataflow_facades_on_persisted_go_cfg",
            "tests/canonical/test_capability_facades.py::test_analysis_facades_abstain_honestly_on_python_function",
        ),
        limitations=(
            "wheel stored-CFG analysis over the persisted graph; abstains "
            "when the symbol is unresolved, the graph is absent, or the wheel "
            "is missing; no model-facing delivery",
        ),
    ),
    _e(
        "analysis.reaching_definitions",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        "groundtruth.runtime.cfg_store:analyze_stored",
        facade=f"{_A}:reaching_definitions",
        tests=(
            "tests/canonical/test_capability_facades.py::test_analysis_dataflow_facades_on_persisted_go_cfg",
            "tests/canonical/test_capability_facades.py::test_analysis_facades_abstain_honestly_on_python_function",
        ),
        limitations=(
            "same stored-CFG pipeline as analysis.cfg; host-side only — "
            "positive coverage is the persisted-Go-CFG leg, Python abstains "
            "no_persisted_cfg",
        ),
    ),
    _e(
        "analysis.control_dependence",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        "groundtruth.runtime.cfg_store:analyze_stored",
        facade=f"{_A}:control_dependence",
        tests=(
            "tests/canonical/test_capability_facades.py::test_analysis_dataflow_facades_on_persisted_go_cfg",
            "tests/canonical/test_capability_facades.py::test_analysis_facades_abstain_honestly_on_python_function",
        ),
        limitations=(
            "same stored-CFG pipeline as analysis.cfg; host-side only — "
            "positive coverage is the persisted-Go-CFG leg, Python abstains "
            "no_persisted_cfg",
        ),
    ),
    _e(
        "analysis.slice",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "typed_on_request",
        TYPED_DISPATCH,
        facade=f"{_A}:slice",
        typed_kinds=("slice",),
        tests=(
            "tests/test_typed_graph_real_producer.py::test_slice_backward_over_the_python_source_substrate",
            "tests/test_typed_graph_real_producer.py::test_interprocedural_slice_honours_its_bounds_and_flags_name_matching",
        ),
        limitations=(
            _TYPED_FACADE_LIMIT,
            "partial semantics: CFG substrate only; interprocedural hops are "
            "name-matched",
        ),
    ),
    _e(
        "analysis.callable_values",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        f"{_A}:callable_values",
        facade=f"{_A}:callable_values",
        tests=(
            "tests/canonical/test_capability_facades.py::test_callable_values_reads_retained_candidates",
            "tests/canonical/test_capability_facades.py::test_graph_backed_facades_abstain_without_a_graph",
        ),
        limitations=(
            "reads producer-retained resolution_callsites/"
            "resolution_candidates tables; an empty candidate set is reported "
            "as an omission, never fabricated",
        ),
    ),
    _e(
        "analysis.taint",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "typed_on_request",
        TYPED_DISPATCH,
        facade=f"{_A}:taint",
        typed_kinds=("taint",),
        tests=(
            "tests/test_typed_graph_real_producer.py::test_taint_is_symbol_reachability_with_its_limits_named",
            "tests/test_typed_graph_real_producer.py::test_taint_statement_dataflow_reaches_the_sink_param",
            "tests/test_typed_graph_real_producer.py::test_taint_statement_dataflow_sanitizer_cuts_the_safe_leg",
            "tests/test_typed_graph_real_producer.py::test_taint_statement_dataflow_sanitizer_scopes_to_resolved_symbol",
            "tests/test_typed_graph_real_producer.py::test_taint_statement_dataflow_skips_constant_arguments",
            "tests/test_typed_graph_real_producer.py::test_taint_statement_dataflow_names_attribute_flow",
            "tests/test_typed_graph_real_producer.py::test_taint_statement_dataflow_names_non_python_scope",
        ),
        limitations=(
            _TYPED_FACADE_LIMIT,
            "partial semantics: symbol-level CALLS reachability plus "
            "statement-level dataflow for Python sources only (def-use "
            "propagation over resolved callsites; unresolved callees, "
            "non-Python functions, unbound *args/**kwargs, and "
            "object-attribute state crossing functions are named omissions, "
            "and seeds are unresolvable uses in the source function); "
            "sanitizer entries resolve to graph nodes — file.py:name scopes "
            "to one definition while a bare name denotes every callable with "
            "that name (named sanitizer_name_ambiguous); several sources "
            "map to one query per source",
        ),
    ),
    # --------------------------------------------------------------------- change
    _e(
        "change.edit_transaction",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        "gt_engine/miniswe_integration.py:MiniSweAdapter.record_edit_transaction",
        facade=f"{_C}:edit_transaction",
        tests=("tests/canonical/test_capability_facades.py::test_edit_transaction_reads_journal_and_cas",),
        limitations=(
            "journal row + CAS payload of the recorded edit transaction; "
            "derived syntax_result evidence is delivered through the post-edit "
            "lane but the transaction record itself is never rendered",
        ),
    ),
    _e(
        "change.patch_impact",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "typed_on_request",
        TYPED_DISPATCH,
        facade=f"{_C}:patch_impact",
        typed_kinds=("patch_impact",),
        tests=("tests/test_typed_graph_dispatch.py::test_patch_impact_is_conservatively_incomplete_and_augments",),
        limitations=(_TYPED_FACADE_LIMIT, "partial semantics: conservatively incomplete"),
    ),
    _e(
        "change.route_impact",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "typed_on_request",
        TYPED_DISPATCH,
        facade=f"{_C}:route_impact",
        typed_kinds=("api_impact",),
        tests=("tests/test_typed_graph_real_producer.py::test_api_impact_attributes_the_client_consumer",),
        limitations=(
            _TYPED_FACADE_LIMIT,
            "partial semantics: fixed framework manifest; MIDDLEWARE_ON not "
            "surfaced",
        ),
    ),
    _e(
        "change.shape_change",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "typed_on_request",
        TYPED_DISPATCH,
        facade=f"{_C}:shape_change",
        typed_kinds=("shape_check",),
        tests=(
            "tests/test_typed_graph_real_producer.py::test_shape_check_runs_the_interface_conformance_check",
            "tests/test_typed_graph_real_producer.py::test_shape_check_reports_the_missing_interface_method",
            "tests/test_typed_graph_real_producer.py::test_shape_check_passes_a_conforming_class_with_an_empty_method",
        ),
        limitations=(
            _TYPED_FACADE_LIMIT,
            "partial semantics: conformance counts callable members only — "
            "interface property signatures are unchecked; each IMPLEMENTS/"
            "DECLARED_IMPLEMENTS edge emits its own verdict row, so one "
            "logical check can appear twice; the producer resolves interface "
            "targets by bare name across languages, so a same-named "
            "interface in another language adds bogus verdicts "
            "(strict-xfail-pinned in "
            "test_shape_check_does_not_follow_cross_language_interface_edges)",
        ),
    ),
    _e(
        "change.affected_tests",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        "gt_engine/miniswe_covering.py:_symbols_for_files",
        facade=f"{_C}:affected_tests",
        tests=(
            "tests/canonical/test_capability_facades.py::test_graph_backed_facades_abstain_without_a_graph",
            "tests/test_miniswe_runtime.py::test_covering_selection_does_not_open_stale_graph",
        ),
        limitations=(
            "selection only: _symbols_for_files + wheel select_covering_tests "
            "compose the same front half the live covering lane runs "
            "pre-execution; the lane continues into run_covering_tests and "
            "only a failing verdict becomes a covering_red dose - the raw "
            "selection is never delivered",
        ),
    ),
    # -------------------------------------------------------------------- runtime
    _e(
        "runtime.last_test_result",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "gateway_auto:execution_evidence",
        "gt_engine/miniswe_integration.py:MiniSweAdapter.record_execution_evidence",
        facade=f"{_R}:last_test_result",
        tests=("tests/canonical/test_capability_facades.py::test_last_test_result_and_verification_state",),
        limitations=(
            "the model receives the rendered [GT_EXECUTION_EVIDENCE] line "
            "queued as an execution_evidence candidate; the journal row, CAS "
            "artifact and raw output the facade returns stay host-side",
        ),
    ),
    _e(
        "runtime.covering_tests",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        "gt_engine/miniswe_covering.py:_symbols_for_files",
        facade=f"{_R}:covering_tests",
        tests=("tests/test_miniswe_runtime.py::test_covering_selection_does_not_open_stale_graph",),
        limitations=(
            "delegates to change.affected_tests; same selection-only limit - "
            "no execution, no delivery of the selection itself",
        ),
    ),
    _e(
        "runtime.failure_fingerprint",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        "gt_engine/bridge.py:failure_fingerprint",
        facade=f"{_R}:failure_fingerprint",
        tests=("tests/canonical/test_capability_facades.py::test_failure_fingerprint_and_repeated_failure_state",),
        limitations=(
            "the live recovery-steer lane fingerprints via the wheel's "
            "canonical_test_failure_fingerprint, a different implementation - "
            "bridge.failure_fingerprint is production-parity code used by the "
            "bridge episode machinery and is never emitted to the model",
        ),
    ),
    _e(
        "runtime.repeated_failure_state",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "steer",
        "gt_engine/miniswe_integration.py:MiniSweAdapter.note_failure_fingerprint",
        facade=f"{_R}:repeated_failure_state",
        tests=(
            "tests/canonical/test_capability_facades.py::test_failure_fingerprint_and_repeated_failure_state",
            "tests/test_miniswe_integration.py::test_recovery_steer_scheduled_on_recurring_failure_after_edit",
            "tests/test_miniswe_runtime.py::test_recovery_retries_through_real_admission_and_transport_hooks",
        ),
        limitations=(
            "the model receives only the bounded GT_RECOVERY steer (<=2 per "
            "task) carrying the fingerprint hash and workspace epoch; the raw "
            "recurrence map and delivered counter stay host-side",
        ),
    ),
    _e(
        "runtime.verification_state",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "gateway_auto:execution_evidence",
        "gt_engine/miniswe_integration.py:MiniSweAdapter._classify_execution_vs_baseline",
        facade=f"{_R}:verification_state",
        tests=(
            "tests/canonical/test_capability_facades.py::test_last_test_result_and_verification_state",
            "tests/test_baseline_classification.py::test_directory_run_covering_the_known_suite_writes_suite_truth",
        ),
        limitations=(
            "recorded baseline-vs-current classification, not a fresh "
            "verdict; the model sees the baseline clause inside the "
            "[GT_EXECUTION_EVIDENCE] line, not the state dict",
        ),
    ),
)


def _kind(
    kind: str,
    *,
    facades: tuple[str, ...] = (),
    tests: tuple[str, ...] = (),
    limitations: tuple[str, ...] = (),
) -> CapabilityEntry:
    return _e(
        f"kind.{kind}",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "typed_on_request",
        TYPED_DISPATCH,
        typed_kinds=(kind,),
        facades=facades,
        tests=tests,
        limitations=limitations,
    )


KIND_ENTRIES: tuple[CapabilityEntry, ...] = (
    _kind(
        "exact_literal_search",
        facades=("localization.lexical_search",),
        tests=(
            "tests/test_typed_graph_dispatch.py::test_exact_literal_search_dispatches_replace_with_matches",
            "tests/test_miniswe_typed_actions.py::test_exact_literal_action_is_snapshot_bound_and_canonical",
        ),
        limitations=(
            "exact semantics; explicit scopes only, 20 matches, 256 B/line; "
            "scope '.' also scans .git/ (documented producer defect)",
        ),
    ),
    _kind(
        "syntax",
        facades=(),
        tests=(
            "tests/test_typed_graph_dispatch.py::test_syntax_query_dispatches_replace_for_certified_extension",
            "tests/test_miniswe_typed_actions.py::test_uncertified_syntax_language_is_not_dispatched",
        ),
        limitations=(
            "exact semantics; parse-only over go/js/py/rb/ts certified "
            "extensions; no facade exposes it",
        ),
    ),
    _kind(
        "patch_impact",
        facades=("change.patch_impact",),
        tests=("tests/test_typed_graph_dispatch.py::test_patch_impact_is_conservatively_incomplete_and_augments",),
        limitations=("partial semantics: conservatively incomplete, augments only",),
    ),
    _kind(
        "verification_status",
        facades=(),
        tests=("tests/test_typed_graph_dispatch.py::test_verification_status_green_is_execution_specific",),
        limitations=(
            "execution_specific semantics bound to command and revision; no "
            "facade exposes it (runtime.verification_state reads the recorded "
            "journal classification instead)",
        ),
    ),
    _kind(
        "definition",
        facades=("localization.definition",),
        tests=("tests/test_typed_graph_dispatch.py::test_definition_returns_the_graph_recorded_definition",),
        limitations=("partial semantics: name-level resolution",),
    ),
    _kind(
        "references",
        facades=("localization.references",),
        tests=("tests/test_typed_graph_dispatch.py::test_references_groups_incoming_edges_by_type",),
        limitations=(
            "partial semantics: name-level resolution; bands silently keep "
            "20 rows while labelling the answer exact (documented wheel defect)",
        ),
    ),
    _kind(
        "callers",
        facades=("structure.callers",),
        tests=(
            "tests/test_typed_graph_dispatch.py::test_callers_walks_incoming_calls_edges_depth_banded",
            "tests/test_typed_graph_dispatch.py::test_callers_alias_find_callers_dispatches_as_callers",
        ),
        limitations=(
            "partial semantics: name-level resolution; 20 rows per band "
            "(documented wheel defect)",
        ),
    ),
    _kind(
        "symbol_context",
        facades=(
            "structure.symbol_context",
            "structure.callees",
            "structure.framework_relationships",
        ),
        tests=("tests/test_typed_graph_dispatch.py::test_symbol_context_returns_360_degree_view",),
        limitations=("partial semantics: name-level resolution",),
    ),
    _kind(
        "processes",
        facades=("structure.processes",),
        tests=(
            "tests/test_typed_graph_dispatch.py::test_processes_lists_the_detected_entry_to_terminal_flow",
            "tests/test_typed_graph_dispatch.py::test_processes_concept_filter_matches_flow_members",
        ),
        limitations=("partial semantics",),
    ),
    _kind(
        "route_map",
        facades=("structure.framework_relationships",),
        tests=(
            "tests/test_typed_graph_real_producer.py::test_route_map_reads_producer_route_edges",
            "tests/test_typed_graph_real_producer.py::test_route_map_survives_an_api_call_only_route",
        ),
        limitations=(
            "partial semantics: fixed framework manifest; MIDDLEWARE_ON not "
            "surfaced; an API_CALL-only route yields anchor line 0 and the "
            "answer becomes producer_not_supported (documented wheel defect)",
        ),
    ),
    _kind(
        "api_impact",
        facades=("change.route_impact", "structure.framework_relationships"),
        tests=("tests/test_typed_graph_real_producer.py::test_api_impact_attributes_the_client_consumer",),
        limitations=("partial semantics: fixed framework manifest; MIDDLEWARE_ON not surfaced",),
    ),
    _kind(
        "taint",
        facades=("analysis.taint",),
        tests=(
            "tests/test_typed_graph_real_producer.py::test_taint_is_symbol_reachability_with_its_limits_named",
            "tests/test_typed_graph_real_producer.py::test_taint_statement_dataflow_reaches_the_sink_param",
            "tests/test_typed_graph_real_producer.py::test_taint_statement_dataflow_sanitizer_cuts_the_safe_leg",
            "tests/test_typed_graph_real_producer.py::test_taint_statement_dataflow_sanitizer_scopes_to_resolved_symbol",
            "tests/test_typed_graph_real_producer.py::test_taint_statement_dataflow_skips_constant_arguments",
            "tests/test_typed_graph_real_producer.py::test_taint_statement_dataflow_names_attribute_flow",
            "tests/test_typed_graph_real_producer.py::test_taint_statement_dataflow_names_non_python_scope",
        ),
        limitations=(
            "partial semantics: symbol-level CALLS reachability plus "
            "statement-level dataflow for Python sources only (def-use "
            "propagation over resolved callsites; unresolved callees, "
            "non-Python functions, unbound *args/**kwargs, and "
            "object-attribute state crossing functions are named omissions, "
            "and seeds are unresolvable uses in the source function); "
            "sanitizer entries resolve to graph nodes — file.py:name scopes "
            "to one definition while a bare name denotes every callable with "
            "that name (named sanitizer_name_ambiguous); not a sound "
            "over-approximation",
        ),
    ),
    _kind(
        "rename",
        facades=(),
        tests=("tests/test_typed_graph_real_producer.py::test_rename_previews_the_graph_edit_sites",),
        limitations=("partial semantics; no facade exposes it",),
    ),
    _kind(
        "shape_check",
        facades=("change.shape_change",),
        tests=(
            "tests/test_typed_graph_real_producer.py::test_shape_check_runs_the_interface_conformance_check",
            "tests/test_typed_graph_real_producer.py::test_shape_check_reports_the_missing_interface_method",
            "tests/test_typed_graph_real_producer.py::test_shape_check_passes_a_conforming_class_with_an_empty_method",
        ),
        limitations=(
            "partial semantics: conformance counts callable members only — "
            "interface property signatures are unchecked; each IMPLEMENTS/"
            "DECLARED_IMPLEMENTS edge emits its own verdict row, so one "
            "logical check can appear twice; the producer resolves interface "
            "targets by bare name across languages, so a same-named "
            "interface in another language adds bogus verdicts "
            "(strict-xfail-pinned in "
            "test_shape_check_does_not_follow_cross_language_interface_edges)",
        ),
    ),
    _kind(
        "tool_map",
        facades=("structure.framework_relationships",),
        tests=(
            "tests/test_typed_graph_real_producer.py::test_tool_map_executes_and_names_what_it_cannot_see",
            "tests/test_typed_graph_real_producer.py::test_tool_map_detects_a_function_registered_as_an_mcp_tool",
        ),
        limitations=(
            "partial semantics: external decorators bind through "
            "occurrence nodes (name-only, uncertified); registration "
            "sites without decorators (server.add_tool(f) calls) are "
            "untracked by design",
        ),
    ),
    _kind(
        "slice",
        facades=("analysis.slice",),
        tests=(
            "tests/test_typed_graph_real_producer.py::test_slice_backward_over_the_python_source_substrate",
            "tests/test_typed_graph_real_producer.py::test_slice_over_the_persisted_go_cfg",
            "tests/test_typed_graph_real_producer.py::test_interprocedural_slice_honours_its_bounds_and_flags_name_matching",
        ),
        limitations=(
            "partial semantics: CFG substrate only; interprocedural hops are "
            "name-matched",
        ),
    ),
)

_RTC = "tests/canonical/test_runtime_components.py"

# Runtime components — the §6 'additional' rows that are mechanisms rather
# than facades or certified kinds. ``state`` is judged on the same rule as
# every other entry: the highest rung the mechanism's substance truthfully
# reaches. Gates, governors, and history bookkeeping never render their own
# output into model-visible text, so they sit at AVAILABLE even though the
# runtime reaches them; advisory payloads that DO ride an admitted lane are
# MODEL_FACING and the call-graph truth test verifies the path.
COMPONENT_ENTRIES: tuple[CapabilityEntry, ...] = (
    _e(
        "task_contract",
        "AGENT CONTROL",
        "AVAILABLE",
        "none",
        "gt_engine/task_contract.py:extract_task_contract",
        tests=(f"{_RTC}::test_task_contract_extraction",),
        limitations=(
            "runner-side machinery: the obligations:task context unit is "
            "admitted through miniswe_gt_run, not the canonical runtime — "
            "extraction is verbatim-line anchored, not a semantic proof of "
            "coverage",
        ),
    ),
    _e(
        "persistent_plan",
        "AGENT CONTROL",
        "AVAILABLE",
        "none",
        "gt_engine/persistent_plan/deterministic.py:build_deterministic_plan",
        tests=(f"{_RTC}::test_persistent_plan_ledger_and_deterministic_plan",),
        limitations=(
            "runner-side machinery: the plan_cursor:task context unit is "
            "admitted through miniswe_gt_run, not the canonical runtime; "
            "rows carry checks only when a test command was discovered",
        ),
    ),
    _e(
        "plan_gate",
        "AGENT CONTROL",
        "AVAILABLE",
        "gate",
        "gt_engine/persistent_plan/gate.py:decide",
        tests=(f"{_RTC}::test_plan_gate_decision_is_pure_policy",),
        limitations=(
            "pure decision function; its verdict steers submission and is "
            "never itself model-visible text",
        ),
    ),
    _e(
        "churn_governor",
        "AGENT CONTROL",
        "AVAILABLE",
        "steer",
        "gt_engine/churn_governor.py:ChurnGovernor.observe",
        tests=(f"{_RTC}::test_churn_governor_steer_then_abort",),
        limitations=(
            "returns steer/abort signals the caller applies; the signal "
            "itself is not a delivery",
        ),
    ),
    _e(
        "submit_finalization",
        "AGENT CONTROL",
        "AVAILABLE",
        "none",
        "gt_engine/gt_session.py:GTSession._finalization_candidate",
        tests=(f"{_RTC}::test_finalization_candidate_renders_advisory",),
        limitations=(
            "runner-side machinery: the advisory is emitted through the "
            "plan-submit path in miniswe_gt_run, not the canonical runtime; "
            "observes collector-shaped commit bytes only — never commits or "
            "assesses correctness on the agent's behalf",
        ),
    ),
    _e(
        "select_catalog",
        "DELIVERY POLICY",
        "MODEL_FACING",
        "gateway_auto:sealed",
        "gt_engine/gt_session.py:GTSession.prepare_select_catalog",
        tests=(f"{_RTC}::test_select_catalog_certify_accept_consume",),
        limitations=(
            "one certified task-start offer per run; abstains honestly on a "
            "stale or graph-incomplete bootstrap",
        ),
    ),
    _e(
        "history_supersession",
        "DELIVERY POLICY",
        "AVAILABLE",
        "none",
        "gt_engine/gt_session.py:GTSession.demote_overbudget_context_units",
        tests=(f"{_RTC}::test_history_supersession_and_demotion",),
        limitations=(
            "internal history bookkeeping: superseded/demoted units queue "
            "onto the drain and collapse to CAS pointers — demotion is not "
            "deletion and renders no standalone output",
        ),
    ),
    _e(
        "drift_relocalization",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        "gt_engine/miniswe_integration.py:MiniSweAdapter.localization_drift_pending",
        tests=(f"{_RTC}::test_drift_relocalization_flags",),
        limitations=(
            "pending flags only; the re-localization they trigger ships "
            "through the localization delivery path, not this mechanism",
        ),
    ),
    _e(
        "reactive_syntax",
        "INTELLIGENCE/STATE",
        "MODEL_FACING",
        "gateway_auto:sealed",
        "gt_engine/runtime_observation.py:compile_transaction_artifacts",
        tests=(
            f"{_RTC}::test_reactive_syntax_verdict_producer_path",
            f"{_RTC}::test_reactive_syntax_verdict_fallback_path",
        ),
        limitations=(
            "syntax verdicts are exact producer-anchored parses; graph caller "
            "rows are graph_recorded, never claimed complete",
        ),
    ),
    _e(
        "recovery_suspension",
        "AGENT CONTROL",
        "AVAILABLE",
        "none",
        "gt_engine/miniswe_integration.py:MiniSweAdapter._count_recovery_outcome",
        tests=(f"{_RTC}::test_recovery_suspension_at_episode_cap",),
        limitations=(
            "suspends the rebuild loop after the episode cap; consumers "
            "degrade to the no-graph path for the rest of the episode",
        ),
    ),
    _e(
        "cochange_priors",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        "gt_engine/cochange_evidence.py:run_cochange_prior",
        tests=(f"{_RTC}::test_cochange_priors_on_real_graph",),
        limitations=(
            "co-change improves rank but can never certify a delivery by "
            "itself; quiet on repositories without git history",
        ),
    ),
    _e(
        "context_admission",
        "DELIVERY POLICY",
        "AVAILABLE",
        "none",
        "gt_engine/gt_session.py:GTSession.admit_decision_packet",
        tests=(
            "tests/canonical/test_unit_staleness.py::test_admitted_unit_is_stale_after_a_workspace_edit",
        ),
        limitations=(
            "the admission owner every delivered lane passes through; its "
            "decisions are admission records, not delivered text",
        ),
    ),
    _e(
        "incremental_amend",
        "INTELLIGENCE/STATE",
        "AVAILABLE",
        "none",
        "gt_engine/miniswe_integration.py:MiniSweAdapter._sync_amend_graph",
        tests=(
            "tests/canonical/test_runtime_intelligence.py::test_sync_amend_adopts_or_journals_refusal",
        ),
        limitations=(
            "batch-path amend only: the certified producer declares "
            "batch_parser_node_reuse_v1, not incremental_amend_in_place; "
            "parents past SYNC_AMEND_MAX_GRAPH_BYTES defer to the "
            "coordinator's clock, and uncertifiable parents refuse by name",
        ),
    ),
)

CAPABILITY_REGISTRY: tuple[CapabilityEntry, ...] = (
    *FACADE_ENTRIES,
    *KIND_ENTRIES,
    *COMPONENT_ENTRIES,
)

REGISTRY: dict[str, CapabilityEntry] = {e.name: e for e in CAPABILITY_REGISTRY}

if len(REGISTRY) != len(CAPABILITY_REGISTRY):  # pragma: no cover - import-time guard
    raise RuntimeError("duplicate capability registry names")


def entries() -> tuple[CapabilityEntry, ...]:
    return CAPABILITY_REGISTRY


def by_state(state: str) -> tuple[CapabilityEntry, ...]:
    return tuple(e for e in CAPABILITY_REGISTRY if e.state == state)


def typed_on_request_kinds() -> frozenset[str]:
    """Certified kinds the registry marks delivered through typed_on_request."""
    return frozenset(
        kind
        for e in CAPABILITY_REGISTRY
        if e.delivery_path == "typed_on_request"
        for kind in e.typed_kinds
    )


def facade_entry_names() -> frozenset[str]:
    return frozenset(e.name for e in FACADE_ENTRIES)


def valid_delivery_path(path: str) -> bool:
    return path == "none" or any(
        path.startswith(prefix) for prefix in _DELIVERY_PATH_PREFIXES[1:]
    )
