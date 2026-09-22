# MAIN -> canonical port ledger (HAR-90, workstream W4)

Bases: MAIN `bb6f8925` (origin/main), merge-base `4f393f1e`, UNION `11ae7790` (the base of canonical/gt-har90).

## How it was computed

1. Candidate files: `git -C D:/gt-control-main diff --name-only 4f393f1e bb6f8925 -- gt_engine scripts eval gt_harness src tests pyproject.toml conftest.py`, intersected with `git diff --name-only 11ae7790 bb6f8925 -- <same pathspec>`. Result: **112 files**.
2. For each file, take MAIN's own hunks with `git diff -U0 4f393f1e bb6f8925 -- <file>`. A hunk is **residual** when it adds at least one non-blank line whose stripped text is absent from UNION's version of the file (`git show 11ae7790:<file>`). 84 files have residual hunks (193 hunks in all); 28 have none.
3. Every residual line was attributed with `git blame bb6f8925` to the MAIN commit that introduced it, then read against UNION's current code in `D:\gt-canonical`.
4. Limit of the method: it compares sets of lines, so it finds MAIN additions only. MAIN deletions are not residual (for example, the 4 dependency lines MAIN dropped from pyproject.toml); UNION keeps its own.

**Main finding.** UNION's `gt_engine/`, `gt_harness/` and `scripts/` are a **later revision of the same code** that MAIN froze at candidate-24 (`6fe08fd7`, GT 66791e0b) and then patched through 2026-09-07. Nearly every residual line is MAIN's older text for code that UNION later rewrote. Two things are genuinely MAIN-only: the HAR-90 typed surface (`b04ef0fa`) and one orphaned test (`tests/test_capability_matrix.py`).

Classes: (a) HAR-90 typed surface: port. (b) Superseded by UNION's newer design: leave. (c) Independent fix or feature absent from UNION: port if correct. (d) Dead or legacy: leave.

| class | rows | residual hunks |
|---|---|---|
| a | 11 | 28 |
| b | 77 | 156 |
| c | 1 | 1 |
| d | 8 | 8 |
| total | 97 | 193 |

Reasons that recur across rows are abbreviated in the table:

- **COORD**: UNION deleted the async GraphBuildCoordinator (graph_coordinator.py now holds value types only): builds are synchronous amends at the transaction boundary (miniswe_integration.py:_sync_amend_graph/_amend_graph_inline/_recovery_build_inline, adopted via EngineState.publish_graph) with recovery suspension; zero-edge enrichments are refused there as no_edge_mutations.
- **PTR**: UNION removed model-facing fetch pointers: context.py:_bound_observation elides inline (no sha, no retrieval command) and _MODEL_FACING_POINTER_PREFIXES is a tripwire, because pointer dereferences starved run 34656860834.
- **FP**: UNION carries a later revision of the same code; MAIN's lines are the older snapshot.

## Ledger

| file | hunk summary | class | action | reason | hunks |
|---|---|---|---|---|---|
| `eval/pier_filtered_docker.py` | PierFilteredDockerEnvironment.__init__ forces allow_internet=False for every task (c47e917d, 706d1089) | b | leave | UNION passes the task's declared network through (Pier 0.3.1 already resolves network_mode). The forced rewrite put TB2 verifiers behind the egress proxy and graded every task 0 (runs 35545356695, 35557511581; fixed 466c10c9). UNION also adds the proxy host-gateway compose override | 1 |
| `gt_engine/context.py` | _bound_kept_blocks/compact_provider_view spool elided tool results to the artifact store and emit [GT_HISTORY_EVIDENCE]/[GT_HISTORY_ARCHIVE] markers (6fe08fd7) | b | leave | PTR | 11 |
| `gt_engine/contract_embeddings.py` | embedding_inputs raises ambiguous_stable_identity on any duplicate stable_id (711febc1, 1cb75b2e) | b | leave | UNION embedding_inputs collapses exact duplicates and quarantines only the conflicting group, instead of failing the whole projection | 1 |
| `gt_engine/delivery_budget.py` | truncate_utf8 byte-prefix helper, exported but unused (d39a5a7b) | d | leave | No caller in MAIN. UNION drops whole co-change lines at the ceiling instead of cutting one (06b255d0). The typed path has its own _truncate_utf8 | 1 |
| `gt_engine/engine_state.py` | docstring line naming GraphBuildCoordinator (a4ad9c94) | d | leave | Refers to a class UNION deleted | 1 |
| `gt_engine/generated_typed_capabilities.py` | CERTIFIED_TYPED_KINDS widened to 16 kinds; CERTIFICATION_SHA256 for the 480-pair CSV (b04ef0fa) | a | port (W4 parent) | HAR-90 typed surface. Regenerate from the canonical certification (the MAIN sha is for MAIN's CSV, which labels taint/slice/route_map sound_overapprox without per-language proof) | 2 |
| `gt_engine/graph_coordinator.py` | the entire nonblocking GraphBuildCoordinator: coalescing latest-request queue, parent-graph carry (bb551a48), zero-edge enrichment refusal (477bd880), refused-candidate deletion (64a04c4e), enrichment scheduling (1cb75b2e, 6fe08fd7, 8ac9f813, 2c100e9b) | b | leave | COORD | 1 |
| `gt_engine/gt_session.py` | decision-batch candidate identity + [GT_CONTEXT_UNIT_REFERENCE] rendering in provider_request_admitted; localization stored as a referenced context unit (6fe08fd7) | b | leave | PTR | 5 |
| `gt_engine/gt_session.py` | task-start localization compression with GT_LOCALIZATION_OVERSIZED diagnostic (9dc63e99, 1cb75b2e) | b | leave | UNION journals localization_compressed from miniswe_integration.py and keeps DiagnosticCode.GT_LOCALIZATION_OVERSIZED; task-start shipping is gated differently there | 2 |
| `gt_engine/gt_session.py` | capability-status rows: lsp_promotion read from GraphBuildCoordinator.consider_enrichment terminals, _promotion_yield counting verified+corrected only, newest-rank selection (68854295, 9442bd4b, 36667aed, 35baed70, 948fee59, c6de1dd2) | b | leave | UNION gt_session.py:_promotion_yield (~2058) reports from the path that actually promotes in UNION (_maybe_schedule_lsp_promotion -> _schedule_lsp_candidate); the MAIN tap names the deleted coordinator | 1 |
| `gt_engine/gt_session.py` | close() calls engine.close_graph_coordinator (1cb75b2e) | b | leave | COORD | 1 |
| `gt_engine/indexer.py` | _INDEX_TIMEOUT_SECONDS = 600 hard wall-clock bound and matching publication-lock deadlines (e4c9c165) | b | leave | UNION removed the artificial cap on purpose (boa died on 600s with zero coverage); GT_INDEX_TIMEOUT_SECONDS is an opt-in operator bound, the RSS guard and task budget are the real bounds | 2 |
| `gt_engine/indexer.py` | older snapshot of: _source_paths plain os.walk, amend-capability cache keyed without capability, cgroup snapshot/headroom, _referenced_revisions/_prune_superseded_revisions, refresh_index_files amend fallback, BenchmarkGraphRequired abort, LSP derivation certification (6fe08fd7, 2c100e9b, 5ba2bfe9, 56a5b131, 5e88fd70, 516f4c1b, 1920a127, 6500c169, 68853236) | b | leave | FP: git-visible source domain (run 34760986248), BATCH_AMEND_CAPABILITY, cgroup v1/v2 limit_state, pinned-revision protection, lsp/lsp_scoped_merge derivation phases, and BenchmarkGraphRequired no longer aborts the trial (indexer.py ~2616). W4 parent separately adds -source-revision passing (not a MAIN hunk) | 9 |
| `gt_engine/miniswe_controller.py` | receipt dependency_footprint field and dependency_footprint_affected invalidation (6fe08fd7) | b | leave | FP (miniswe_controller.py:194 has the same invalidation with normalize_dependency_path) | 4 |
| `gt_engine/miniswe_evidence.py` | run_evidence_pipeline replaces an over-budget candidate with a [GT_EVIDENCE_REFERENCE] artifact pointer (6fe08fd7) | b | leave | PTR | 1 |
| `gt_engine/miniswe_integration.py` | GraphBuildCoordinator wiring: refresh_graph schedules/polls async builds, _build_frozen_graph (amend via refresh_index_files, build_mode/reason and parent-path journal rows, rebuild embedding budget), frozen-input symlink capture, lsp-* orphan sweep, close_graph_coordinator (1cb75b2e, 6fe08fd7, 2c100e9b, bb551a48, 5ba2bfe9, e001ff36, 516f4c1b, a4ad9c94, 947eb2c1, ad58b7d9, 8ac9f813, 2229d33d) | b | leave | COORD; symlinks via _symlink_alias_target, orphan sweep at ~4334 | 8 |
| `gt_engine/miniswe_integration.py` | _reverify_after_edit re-runs recorded proof commands synchronously inside the edit (GT_VERIFY_EXECUTE=1, 30s pass budget), journals obligation_reverified (f4e72deb, 2c100e9b) | b | leave | UNION _reverify_after_edit (~1096) queues obligation_reverify_queued and drains at the verification boundary, so the edit path runs no commands; the old event name claimed executions that never happened | 1 |
| `gt_engine/miniswe_integration.py` | prepare_verification_candidate builds the verification plan from the PRE-edit graph (1cb75b2e) | b | leave | UNION prepare_verification_candidate (~2752) uses the post-edit graph, which exists synchronously now | 1 |
| `gt_engine/miniswe_integration.py` | older snapshot of: ExternalStateStore verify, localization cache key, _semantic_task_start_localization(no query), render_obligation_delta, _record_graph_refresh_failure, latest delivery ids (1cb75b2e, 6fe08fd7, 45ac7694, 9dc63e99, 1b81dca2, a48cfb03) | b | leave | FP (read_verified_events, query-parameterized _semantic_task_start_localization ~6206, render_obligation_transitions, provider_wait scheduler) | 10 |
| `gt_engine/miniswe_runtime.py` | older _viewed_files (no cd tracking), _classify_test via classify_execution_outcome, cochange priority-10 candidate, diagnostics import (1b81dca2, 1cb75b2e, f7a2c9d3, d6742327) | b | leave | FP (_viewed_files tracks cd against repo_root) | 4 |
| `gt_engine/miniswe_runtime.py` | _run_evidence stores decision evidence via store_history_evidence as referenced units; install_runtime_hooks bootstrap/compaction/provider-admission wiring incl. select-catalog call counting (6fe08fd7, 3da86564, 1cb75b2e) | b | leave | PTR; provider admission and bootstrap counting are UNION's later versions | 6 |
| `gt_engine/miniswe_runtime.py` | typed branch (_GRAPH_DEPENDENT_TYPED_KINDS, refresh-before-query, graph_db gating) | a | port (W4 parent) | No MAIN-only lines: both lineages carry the same 5-kind set. W4 task 3 widens it to every graph-reading kind | 0 |
| `gt_engine/miniswe_typed_actions.py` | GROUNDTRUTH_TOOL description for 16 kinds, _KIND_ALIASES + alias normalization in build_action_request/execute_typed_action, canonical kind->ActionRequest map (b04ef0fa) | a | port (W4 parent) | HAR-90 typed surface. Description must be rewritten so slice advertises interprocedural/max_hops/max_depth and no removed kind is mentioned | 5 |
| `gt_engine/miniswe_typed_actions.py` | _graph_revision reads project_meta.git_commit (b04ef0fa) | a | replace (W4 parent) | Defect: git_commit is the producer BUILD commit, so graph_revision_mismatch can never fire (audit F sec. 0). CANON contract: RevisionVector.graph = EngineState graph source revision, passed to gt-index as -source-revision | 1 |
| `gt_engine/miniswe_typed_actions.py` | oversize result: pop rows while direct_answer is a list, then AUGMENT + QUERY_RESULT_TRUNCATED + honesty completeness=truncated (b04ef0fa) | a | port (W4 parent), extend | Correct for lists, but dict answers (every kind in features 14-19) are never reduced; W4 task 5 bounds dicts too | 1 |
| `gt_engine/output_evidence.py` | EvidenceStore preview appends [GT_OUTPUT_ARTIFACT] + 'gt-evidence read' retrieval command (6fe08fd7) | b | leave | PTR | 1 |
| `gt_engine/persistent_execution_state.py` | select-catalog task text cut to 2,000 chars without recording the cut (6fe08fd7) | b | leave | UNION records task_truncated_chars | 1 |
| `gt_engine/pytest_evidence.py` | pytest.main(sys.argv[2:]) with no pinned rootdir/ini (8ac9f813) | b | leave | UNION pins -c <empty ini> --rootdir cwd so an ancestor pytest.ini cannot rewrite nodeids (the witness reported not_run) | 1 |
| `gt_engine/request_history.py` | history spool in the shared temp dir (6fe08fd7) | b | leave | UNION spools inside store.root (run 35016130850 lost a leg to /tmp cleanup) | 1 |
| `gt_engine/retrieval.py` | score-string localization rendering; lexical_rank with no NULL/non-finite bm25 guard; no derived community/process context (6fe08fd7, 1122c213) | b | leave | FP (payload-line localization, bm25 NULL degrade-with-reason, derived_context) | 1 |
| `gt_engine/run_diagnostics.py` | unanchored _SECRET_VALUE regex; unhealthy = any event (ef3ee1f9) | b | leave | UNION anchors the key regex (no false fire on 'aiomonitor-task-snapshots'), adds assignment redaction, and treats only ERROR severity as unhealthy | 1 |
| `gt_engine/runtime_observation.py` | capture_workspace git-path discovery via ls-files; compile_execution_evidence older outcome guard (6fe08fd7, 1b81dca2) | b | leave | FP (repository_identity.git_visible_paths, submodule-aware ls-files -s, _imperative per-test guidance) | 7 |
| `gt_engine/verification_contract.py` | literal_sha256 regex requiring result=pass; expected_relation check (d6742327, ef3ee1f9) | b | leave | FP (regex captures result; expected_relation checked for executable predicates at ~643) | 3 |
| `gt_harness/product.py` | _SAFE_GT_ENV allowlist (f99d68d9) | b | leave | FP | 1 |
| `gt_harness/recorded_content.py` | localization anchors parsed from ' score=' lines (cafd206a) | b | leave | Tracks MAIN's score-string localization format, which UNION replaced | 1 |
| `gt_harness/runtime_receipts.py` | provider_calls/bootstrap attribution, refusal chronology, LSP-report docstring naming the coordinator (68854295, 6fe08fd7, 6d6f4809, 35766673, c6197b1e, 3f53b55c, 074645b2, e4e8c580, 5ea5be77, 45ac7694) | b | leave | FP | 1 |
| `scripts/attest_deepswe.py` | DeepSWE cohort attestation constants/gates for MAIN's paid smoke (a50b89dd, ae3dccc0, 45ac7694, 544a873d, b56d488b, ec1ac8e8, 3a5c44b1, d82a73f0) | d | leave | MAIN-only paid-run attestation; no paid runs in this phase, and UNION carries its own attestation | 1 |
| `scripts/benchmark_progress.py` | progress reporter without official_harbor_reward artifact fallback (9b2d2ac0) | b | leave | FP | 1 |
| `scripts/diagnose_benchmark_run.py` | capability-failure section, no gh annotation escaping (d6742327, 53fa03a6, ef3ee1f9) | b | leave | FP (annotation escaping, path-launch sys.path fix) | 1 |
| `scripts/feature_accounting.py` | boundary witness map and delivery counting (fb213e43, 4fd4418e, ec7a1f55, 507a1a7d, fd8e22da) | b | leave | FP (DECIDABLE_ABSENCE rule, channel_for_evidence) | 1 |
| `scripts/finalstand_offline.py` | certification matrix must contain 480 pairs (b04ef0fa) | a | port (W4 parent) | Follows the 16-kind x 30-language CSV | 1 |
| `scripts/generate_gt_finalstand.py` | OPERATIONS += 13 kinds, per-language certification rules for the new kinds, typed-capability module emission (b04ef0fa) | a | port (W4 parent) | HAR-90 typed surface; certification rows must be narrowed to what is proven (taint is symbol-level reachability, slice only CFG languages, route_map only manifest frameworks) | 6 |
| `scripts/gt_audit.py` | _native_feature_projection over 19 identities, delivery material checks (1b81dca2, 9113531d, ea70b347) | b | leave | FP (plan_projection-aware projection at ~1014) | 1 |
| `scripts/gt_engine_acceptance.py` | acceptance list includes tests/test_graph_coordinator.py (1cb75b2e) | b | leave | COORD | 1 |
| `scripts/gt_installed_rehearsal.py` | rehearsal request/bootstrap counting and GT_OUTPUT_ARTIFACT scenario (6d6f4809, 6fe08fd7, 5426f94a, b0d04aef, f23fa9f2) | b | leave | FP | 1 |
| `scripts/issue_producer_artifact.py` | hard-coded graph_schema_version v15.2-trust-tier + capability list (0fd60892, ff578719) | b | leave | UNION reads schema/capabilities from the binary's build-info sidecar (hard-coding stamps the previous producer's identity) | 1 |
| `scripts/miniswe_gt_run.py` | GT_HISTORY_REF provider-visible history markers (1920a127, 62b1254c) | b | leave | PTR | 1 |
| `scripts/miniswe_gt_run.py` | openai/deepseek/deepseek-v4-flash-0731 route pinned to provider 'relace' only (1cb75b2e) | d | leave | MAIN provider-routing pin; provider routing is out of scope and UNION enforces its own route check | 1 |
| `scripts/miniswe_gt_run.py` | older build_agent/main: contained-command worker, BenchmarkGraphRequired handling, contract-embedding budget comments, patch export (6fe08fd7, 1b81dca2, 29ca364e, 4c744b0d, e454965b, b2216e69, dc702d3c, 5ba2bfe9) | b | leave | FP (embedding_budget_seconds=0.35*wall, contract_store_path from layout) | 6 |
| `scripts/miniswe_supervisor.py` | supervise() without abort_probe; no submission_patch_state (6fe08fd7, 8ac9f813) | b | leave | FP (churn-governor abort_probe) | 1 |
| `scripts/provider_preflight.py` | provider preflight route/model pins and check fields (99e22822, da4e3988, 8c19e52b, 1cb75b2e) | d | leave | MAIN paid-route preflight pins (deepseek-v4-flash-0731 via relace); no provider calls in this phase | 1 |
| `scripts/resolve_harbor_budget.py` | supervisor grace constants (1b81dca2) | b | leave | FP (measured 240s grace, GT setup credit) | 1 |
| `scripts/smoke_stage.py` | stage parsing without single:/subset: stages (a50b89dd, 544a873d) | b | leave | FP | 1 |
| `scripts/standardize_benchmark_result.py` | result reader without reward_stats/list-manifest handling (ac7c4d82) | b | leave | FP (run 34790375793 read as missing_verifier) | 1 |
| `scripts/validate_failure_ids.py` | rglob over the whole tree (41fe8267) | b | leave | UNION scans git-tracked files only (untracked .tmp-* run trees made a clean checkout fail) | 1 |
| `scripts/validate_gt_finalstand.py` | 480 pairs, 16 operations, semantics counts exact 35 / execution_specific 30 / sound_overapprox 75 / removed 340 (b04ef0fa) | a | port (W4 parent) | Counts must be regenerated from the narrowed canonical certification, not copied (MAIN's 75 sound_overapprox rows overstate taint/route_map) | 4 |
| `tests/test_admission_transactions.py` | asserts [GT_CONTEXT_UNIT_REFERENCE] rendering (1cb75b2e, 6fe08fd7) | b | leave | PTR | 1 |
| `tests/test_agent_timeout_is_graded.py` | exit-code map without 7: churn_abort (41ad8853) | b | leave | UNION added churn_abort=7 | 1 |
| `tests/test_attest_deepswe.py` | bootstrap admission fixtures for MAIN attestation (6d6f4809) | d | leave | Tests the MAIN-only attestation script | 1 |
| `tests/test_benchmark_graph_required.py` | asserts a benchmark run without a graph aborts (6500c169) | b | leave | UNION deliberately no longer aborts the trial (indexer.py ~2616); it grades every task the baseline graded | 1 |
| `tests/test_benchmark_progress.py` | status vocabulary fixtures (9b2d2ac0) | b | leave | FP | 1 |
| `tests/test_bounded_context.py` | compaction pairs tool results with retrieval markers (6fe08fd7) | b | leave | PTR | 1 |
| `tests/test_capability_matrix.py` | HAR-41 source-backed capability matrix test: build_capability_matrix/verify_capability_matrix against a pinned GitNexus checkout (03217272, f65ba823, 340f602a, 92056de1) | c | defer | UNION still ships gt_engine/graph_context.py:build_capability_matrix but reused this filename for an unrelated failure-mode ledger test, so the builder has no test in UNION. The MAIN test skips unless GT_GITNEXUS_ROOT points at GitNexus 7e993ab8, and it pins GT_REVISION e56c7ef1; it cannot be shown to fail-then-pass here without that checkout. Port as tests/test_capability_matrix_source_backed.py when one is available | 1 |
| `tests/test_cochange_evidence.py` | source-text assertions on MAIN's _cochange_prior call shape (e3783447, 1cb75b2e) | b | leave | FP | 1 |
| `tests/test_contract_embeddings.py` | ambiguous stable identity must raise (1cb75b2e) | b | leave | UNION quarantines conflicting groups instead (see the contract_embeddings.py row) | 1 |
| `tests/test_delivery_budget.py` | prompt-kind derivation test without plan_request_rejected (4149cc14) | b | leave | FP | 1 |
| `tests/test_dependency_verification.py` | fixture text (6fe08fd7) | b | leave | FP | 1 |
| `tests/test_failfast_binds_to_real_paths.py` | create_bridge propagates BenchmarkGraphRequired (a761dbb6, 1cb75b2e) | b | leave | Abort-on-missing-graph semantics superseded in UNION | 1 |
| `tests/test_failure_ids.py` | hook receipt expects preserved-auto-push (0bc3bdc6) | b | leave | UNION hook contract is opt-in auto-push (GNX_AUTOPUSH=1) | 1 |
| `tests/test_frozen_history.py` | background producer retains co-change history via the coordinator (6fe08fd7) | b | leave | COORD | 1 |
| `tests/test_graph_coordinator.py` | whole file: coordinator coalescing/parent-carry/identical-input tests (1cb75b2e, bb551a48, 8ac9f813) | b | leave | COORD | 1 |
| `tests/test_graph_publication_layout.py` | background publications keep workspace identity (6fe08fd7) | b | leave | COORD | 1 |
| `tests/test_gt_attribution.py` | DIRECT_FEATURES == 19 (45ac7694) | b | leave | UNION has 21 direct features | 1 |
| `tests/test_gt_audit.py` | attribution projects all 19 features (40637f03) | b | leave | UNION feature census is 21 | 2 |
| `tests/test_gt_engine.py` | census feature count 19 / marker list (40637f03, 53490708) | b | leave | UNION asserts len(DIRECT_FEATURES) | 3 |
| `tests/test_gt_finalstand.py` | language_operation_pairs 480 (b04ef0fa) | a | port (W4 parent) | Follows the regenerated certification | 1 |
| `tests/test_gt_session.py` | task-start localization and CONTEXT_UNIT_REFERENCE expectations (1cb75b2e, 6fe08fd7, d39a5a7b) | b | leave | PTR | 5 |
| `tests/test_index_incremental.py` | amend tests over MAIN's older refresh_index_files (2c100e9b) | b | leave | FP | 1 |
| `tests/test_index_resource_guard.py` | memory-limit expectation for the older cgroup snapshot (56a5b131) | b | leave | FP | 1 |
| `tests/test_installed_rehearsal_interruption.py` | rehearsal fixtures (6fe08fd7) | b | leave | FP | 1 |
| `tests/test_lsp_graph_publication.py` | LSP publication through the coordinator; successful_receipt fixture with edges (6fe08fd7, 608e6654) | b | leave | COORD | 1 |
| `tests/test_miniswe_agent_parity.py` | step limit fixed at 300 (79aacada, 35766673) | b | leave | UNION parametrizes step_limit over 0 and 300 | 1 |
| `tests/test_miniswe_evidence.py` | decision evidence beyond preview becomes an artifact reference (6fe08fd7) | b | leave | PTR | 2 |
| `tests/test_miniswe_integration.py` | pre-edit-graph verification candidate; _build_frozen_graph amend path; LSP promotion via coordinator (1cb75b2e, 2c100e9b, 68854295) | b | leave | COORD | 5 |
| `tests/test_miniswe_repro.py` | GT_HISTORY_REF markers and relace-only routing (1cb75b2e, 6fe08fd7, 1b81dca2, 62b1254c, cf9e5d02) | b | leave | PTR; the routing pin is MAIN-only | 2 |
| `tests/test_miniswe_runtime.py` | coordinator lifecycle and cochange fixtures (6fe08fd7, 1cb75b2e) | b | leave | COORD | 3 |
| `tests/test_miniswe_typed_actions.py` | 16-kind schema enum, removed_kinds == [], uncertified kinds never dispatched, graph_unavailable omission, per-kind unavailable-producer cases (b04ef0fa) | a | port (W4 parent) | HAR-90 typed surface | 6 |
| `tests/test_obligation_reverify.py` | synchronous post-edit reverification pass (2c100e9b) | b | leave | UNION's file tests the queued drain (obligation_reverify_queued) | 1 |
| `tests/test_output_evidence.py` | preview + GT_OUTPUT_ARTIFACT retrieval (6fe08fd7) | b | leave | PTR | 1 |
| `tests/test_product_acceptance.py` | status VERIFIED_PROVIDER_FREE (83ffdb40) | b | leave | FP | 1 |
| `tests/test_product_workflow.py` | closed workflow set admits MAIN's image-mirror, SWE-Live paid smoke and producer_build workflows (419bcfa4, e4fe9661, d79b9acc, 355a7fd8) | d | leave | MAIN workflow inventory; UNION has its own closed set | 1 |
| `tests/test_provider_preflight.py` | paid route is deepseek relace-only (1cb75b2e) | d | leave | MAIN provider pin | 1 |
| `tests/test_resolve_harbor_budget.py` | grace constant rationale (1b81dca2) | b | leave | FP | 1 |
| `tests/test_runtime_receipts.py` | receipt fixtures for MAIN's attribution fields (35766673, 8c19e52b, d39a5a7b, 3cb16ad9) | b | leave | FP | 1 |
| `tests/test_semantic_delivery_receipts.py` | score-string localization fixture (6fe08fd7) | b | leave | FP | 1 |
| `tests/test_standardize_benchmark_result.py` | receipt fixtures (b593e260, ac7c4d82) | b | leave | FP | 1 |
| `tests/test_typed_graph_dispatch.py` | whole file: dispatch coverage for the certified kinds; execution tests only for literal/syntax/definition/references/callers/symbol_context/processes/patch_impact/verification_status, ActionRequest construction only for slice/route_map/api_impact/taint/rename/shape_check/tool_map (b04ef0fa) | a | port (W4 parent) | HAR-90 typed surface; W4 task 8 adds real execution tests against a gt-index-built graph for the seven construction-only kinds | 1 |

## Typed-surface artifacts outside the step-1 pathspec (class a, ported by the W4 parent)

`gt_finalstand/*` (FEATURE_MATRIX.md, feature_matrix.json, language_operation_certification.csv and the other language_operation_* files) and `tests/test_phase2_closeout.py` fall outside the step-1 pathspec, or show no residual lines. They carry the same 16-kind certification and are regenerated with `scripts/generate_gt_finalstand.py`, not copied.

## Files with no MAIN-only lines (28)

UNION contains every non-blank line MAIN added to these files. The remaining UNION-vs-MAIN differences are UNION additions. Spot checks: `gt_engine/attribution.py` (MAIN +17 lines, all in UNION, which adds 237 more), `gt_engine/graph_context.py` (MAIN +175; UNION adds 205 more), `scripts/check_change_blast_radius.py` (MAIN +206; UNION adds 13), `pyproject.toml` (UNION adds only the `gt-plan` entry point).

`gt_engine/attribution.py`, `gt_engine/dense_runtime.py`, `gt_engine/feature_matrix.py`, `gt_engine/graph_context.py`, `gt_engine/miniswe_covering.py`, `gt_engine/repository_identity.py`, `gt_harness/groundtruth_provenance.py`, `pyproject.toml`, `scripts/check_change_blast_radius.py`, `scripts/gt_live_gate.py`, `tests/conftest.py`, `tests/test_dense_runtime.py`, `tests/test_event_journal.py`, `tests/test_failure_id_validator.py`, `tests/test_feature_matrix_outcomes.py`, `tests/test_groundtruth_provenance.py`, `tests/test_gt_task_contract.py`, `tests/test_hybrid_retrieval.py`, `tests/test_miniswe_controller.py`, `tests/test_miniswe_smoke.py`, `tests/test_miniswe_supervisor.py`, `tests/test_paid_workflow_gates.py`, `tests/test_producer_binding.py`, `tests/test_red_evidence.py`, `tests/test_run_diagnostics.py`, `tests/test_runtime_observation.py`, `tests/test_smoke_stage.py`, `tests/test_terminal_outcomes.py`

## Port status (class a)

All class (a) rows were ported on `canonical/gt-har90` by W4. The details are in `docs/canonical/TYPED_SURFACE.md`. Three rows were deliberately ported with changes rather than copied:

- **Certification:** MAIN's `sound_overapprox` label for 75 pairs is narrowed to `partial` by a harness ceiling in `generate_gt_finalstand.py`, and the counts are exact 35 / execution_specific 30 / partial 75 / removed 340. A `partial` kind never REPLACEs. Languages are gated per kind at runtime.
- **`_graph_revision`:** this was replaced, not ported. It is the EngineState workspace revision (`graph_source_revision`), and gt-index receives it as `-source-revision` when the producer declares `source_revision_meta_v1`.
- **Oversize truncation:** this was extended to dict answers (`gt_engine/typed_output_bounds.py`). Honesty completeness becomes `incomplete`, not MAIN's `truncated`. `HonestyEnvelope` rejects `truncated` without a known true total, which MAIN never supplied.

Two files were left alone. MAIN's `gt_finalstand/FEATURE_MATRIX.md` and `feature_matrix.json` are an older witnessed run record (source revision 90101994, generated 2026-09-05), and UNION's is newer; neither describes the typed kinds.
