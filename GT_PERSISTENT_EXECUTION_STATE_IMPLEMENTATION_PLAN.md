# Implement graph-first persistent execution state

This is the living implementation plan for the mechanism specified in
`GT_PERSISTENT_EXECUTION_STATE_RESEARCH.md`.

## Purpose / Big Picture

GroundTruth will retain a compact, typed representation of the repository-semantic
repair state across the full Mini-SWE trajectory. It will be created only after the
graph is complete, use exactly one bounded model call to select certified catalog
items, update deterministically after that call, and expose a small current slice in
every normal executor request. All extra calls and context must be visible in metrics.

## Progress

- [x] Audited the active graph, provider, preflight, execution, postflight, and refresh boundaries.
- [x] Added RED tests proving the persistent-state module did not previously exist.
- [x] Implemented the typed catalog, bootstrap selection, persistent state, transition engine, and bounded context frames.
- [x] Wired one isolated bootstrap call after graph construction and before executor call one.
- [x] Wired repeated provider, preflight, postflight, and graph-rebase consumption.
- [x] Added exact provider delivery receipts, deep metrics, total/executor/bootstrap call accounting, and baseline shields.
- [x] Made graph-derived relationships advisory and explicit task requirements blocking.
- [x] Prevented old graph labels from surviving a graph revision as current facts.
- [x] Enabled the mechanism explicitly in certified Terminal-Bench and DeepSWE treatment workflows.
- [x] Added provider-free and release-gate coverage.
- [x] Reused the accepted five-channel `HybridRetriever` to seed the bootstrap catalog after graph construction.
- [x] Seeded the first live task-start retrieval cache from that identical result so local dense/ranking work is not repeated.
- [x] Normalized certified production GraphDB relation names at the state boundary.
- [x] Reused the shared validator's canonical declared-check ID; no state-side command reparsing.
- [x] Proved reads cannot satisfy creation obligations and unattributed exits cannot manufacture validation failure.
- [x] Passed focused lint, bytecode, and local runtime tests.
- [x] Passed the authoritative Linux source-built provider-free workflow at runtime commit `e0c63ae15be6eeff9eae67ffe873f3b44e2da31f` (run `31647174958`).
- [x] Verified `READY`, `SMOKE_APPROVED`, every required all-17 census line, current-source graph coverage, the real pinned ONNX asset, and the uploaded `provider_calls: 0` receipt.
- [x] Re-ran the source-built provider-free workflow after the final runtime repairs; exact runtime SHA `e0c63ae15be6eeff9eae67ffe873f3b44e2da31f` is certified by run `31647174958`.
- [ ] Inspect a paid live receipt for one bootstrap, zero bootstrap actions, every-boundary state use, and every-call exact delivery.
- [ ] Obtain separate approval for one paid matched diagnostic.
- [ ] Freeze or kill the mechanism based on causal outcome and efficiency evidence.
- [x] Diagnosed the apparent `4/10 -> 1/10` change as a provider-confounded,
  pre-persistent comparison and repaired the independent task-start delivery
  flood it exposed.
- [x] Made bootstrap selection own task-start delivery, attached one exact
  selected source span, and stopped repeating that span after an observed read.
- [x] Added a single request-wide GT contribution budget and release gate.
- [x] Added actual response-model/provider/fingerprint receipts and DeepSWE
  merge enforcement.
- [x] Passed authoritative source-built Linux provider-free workflow
  `31655082336` at repaired runtime commit `9be71ad`.
- [ ] Clear the external provider preflight and obtain an outcome-authorized
  GT-on comparison; no paid persistent-state task has executed yet.

## Surprises & Discoveries

- The local checked-in Windows `gt-index.exe` is stale and lacks Objective-C support. It cannot certify readiness; the source-built Linux workflow is authoritative.
- Treating every certified graph neighbor as mandatory would create false submission blockers. The graph certifies a relationship, not that every repair must modify or validate that neighbor.
- The prior metrics path counted bootstrap tokens but initially left the bootstrap out of the top-level API-call count. The implementation now reports total, executor, and bootstrap calls separately.
- An immutable initial catalog can become line-stale after an edit. After graph rebase, the state retains task requirements and observed paths but does not present old graph labels as current.
- The first draft reused only legacy task-conditioned `RepositoryEvidence`; on sparse queries this could create an empty catalog despite a complete repository graph. The final path uses the accepted hybrid retriever after graph construction. Hybrid rank is explicitly non-certified relevance.
- Initial graph retrieval duplicated the first provider-boundary task-start retrieval. The final path uses the exact live task-start limits and seeds the live cache with the initial result.
- Production GraphDB relation names are uppercase. A lowercase-only allowlist silently removed valid `CALLS` and `ASSERTED_BY` relations; the state now normalizes a bounded certified alias set.
- Passing only raw Bash into state validation stranded a declared-check obligation when the same canonical check was wrapped by `timeout` or redirection. The final path consumes `ValidationClassification.declared_check_id` from the one shared classifier.

## Decision Log

- Decision: use exactly one catalog-bounded model bootstrap rather than an LLM-authored free-form plan.
  Rationale: it permits task-specific prioritization without granting authority to invent repository facts.
- Decision: no repeated planner or advisor calls.
  Rationale: the experiment is persistent repository state, not a second agent or LivePlan clone.
- Decision: inject an external current-state slice in every executor request.
  Rationale: Mini-SWE's durable history does not otherwise contain the current external state after compaction or graph refresh.
- Decision: state context is additive and does not rewrite, suppress, or execute commands.
  Rationale: preserve Mini-SWE agency and isolate the causal mechanism.
- Decision: graph-derived obligations are advisory; only exact task requirements and current attributable failures are blocking.
  Rationale: prevent mechanically unjustified regressions.

## Context and Orientation

`eval/gt_central_agent.py` owns the live loop. `gt_engine/persistent_execution_state.py`
owns the state kernel. `gt_engine/provider_evidence.py` and
`gt_engine/delivery_audit.py` prove visibility. `gt_engine/deep_metrics.py` exposes
resource costs. `scripts/central_readiness_audit.py`,
`scripts/central_pre_smoke_gate.py`, and `scripts/central_release_gate.py` fail closed
when the mechanism is absent or bootstrap-only. The workflows under
`.github/workflows` freeze the actual benchmark arguments.

## Plan of Work

The code path must remain graph-first. Initial transfer and GraphDB certification
precede catalog construction. The bootstrap may reference only catalog IDs and its Bash
tool envelope is never executed or appended to executor history. The state compiler
runs before every normal query. Preflight reads state before host execution. Postflight
commits actual results. Source changes make the graph stale; delivery abstains until a
successful rebase. Every boundary writes a receipt.

The final engineering work is verification, not more architecture. The exact local
workflow test inventory passes except checks that execute the known stale Windows
index binary; focused persistent-state/integration/delivery/release tests, workflow
Ruff, byte-compilation, and diff checks pass. Run the complete
provider-free workflow using a freshly built indexer. If it passes, inspect the emitted
receipt and freeze the exact SHA. Do not tune against benchmark outcomes.

## Concrete Steps

From the repository root, run focused local verification:

    python -m pytest tests/test_persistent_execution_state.py tests/test_gt_central_agent.py tests/test_gt_delivery_audit.py tests/test_central_release_gate.py -q
    python -m ruff check gt_engine/persistent_execution_state.py eval/gt_central_agent.py scripts/central_release_gate.py

The Windows readiness audit is expected to fail if it resolves the stale checked-in
indexer. The authoritative command is the GitHub provider-free workflow, which builds
`vendor/gt-index-src` before running:

    python scripts/central_readiness_audit.py
    python scripts/central_pre_smoke_gate.py

Expected authoritative output includes `READY` and `SMOKE_APPROVED`.

## Validation and Acceptance

Acceptance requires all of the following in a graph-applicable task receipt:

1. initialization status `initialized`;
2. exactly one bootstrap provider call and zero bootstrap action executions;
3. bootstrap status `selected`, not fallback;
4. context compilation for every prepared executor request;
5. preflight projection for every proposed action before environment execution;
6. postflight commit for every executed model action;
7. graph-current state at exit and a rebase after every source-changing refresh;
8. one hash-valid, first-eligible persistent-state delivery per executed provider call;
9. total calls equal executor calls plus the single bootstrap;
10. OFF and AUDIT arms have zero persistent-state behavior.

These conditions prove implementation integrity only. Outcome acceptance separately
requires no attributable solve regression and a favorable solve/efficiency result with
the bootstrap overhead included.

## Idempotence and Recovery

The state is task-scoped and reconstructed per run. It does not write into the task
repository. Parser, graph, catalog, bootstrap, or refresh failure preserves normal
Mini-SWE execution but invalidates the intended treatment receipt. Disable the entire
mechanism with `enable_persistent_execution_state=false` or `integration_mode=off`.

## Outcomes & Retrospective

The local implementation and all non-stale-binary workflow tests are green. Local
`central_readiness_audit.py` and `central_pre_smoke_gate.py` correctly fail closed
because the checked-in Windows `gt-index.exe` lacks Objective-C; no gate was weakened.
The source-built Linux gate passed at runtime commit `e0c63ae15be6eeff9eae67ffe873f3b44e2da31f`
in workflow `31647174958`: the current indexer, pinned Snowflake ONNX asset, central
tests, `READY`, `SMOKE_APPROVED`, and the zero-provider-call receipt all passed. A
live receipt and any paid causal evaluation remain undone. Therefore the correct
current status is `IMPLEMENTED AND PROVIDER-FREE CERTIFIED, NOT BENCHMARK-PROVEN`.
