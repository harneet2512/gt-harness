# GT Harness current-session handoff

## Current status, 2026-09-05

Read [Benchmark readiness evidence and approval](BENCHMARK_READINESS_STATUS.md)
for the current exact artifacts, green CI run, capability census, removed release
defects, remaining proof, and the user's one-smoke plus conditional-19 approval.
Canonical acceptance passed at `4df7ab9c042b8cc1dd6708ec46b431646f3b7d1d`, run
`33990917733`. The stale installation and feature-matrix ordering blockers are
resolved. The unpaid full-flow rehearsal and remaining runtime gates are open.

The material below records the earlier 2026-09-04 architecture and failure
analysis. Its old CI blocker and approval state are historical, not the next
action. The new status reference supersedes those statements, while preserving
the unresolved architecture findings and historical evidence.

## Historical architecture and failure analysis

Read this first when continuing HAR-83, the Mini-SWE integration, benchmark
hardening, or GT performance work. This document records the current operating
state at commit `1b81dca204f52f86190491707930cd4d2f56ee20` on
`har81/canonical-task-identity` (2026-09-04 ET).

The detailed independent review is
[`astra_whole_system_review.md`](astra_whole_system_review.md). Treat that
report as the evidence and performance reference; use this file as the
execution map. `final_hardening_handoff.md`, `GT_MINISWE_HANDOFF.md`, and
`GT_MINISWE_CHANGELOG.md` describe earlier milestones and contain stale heads,
proof states, and next steps. They are historical inputs, not current status.

## Mission and non-negotiable architecture

This is **Mini-SWE powered by Groundtruth**, not a sidecar that occasionally
prints hints and not repeated full-graph construction around an otherwise stock
agent.

- Mini-SWE is the reasoner and actor. It chooses hypotheses, commands, edits,
  and the final answer.
- GT is the deterministic decision engine. It continuously supplies current
  repository facts, task obligations, change consequences, verification state,
  and bounded recovery context at real decision boundaries.
- GT computation/context, permission to run additional checks, and authority
  to block an action are three separate controls. Automatic useful context must
  not depend on indiscriminate enforcement.
- The official verifier owns benchmark success. GT predicates and graph claims
  cannot manufacture reward or redefine a solve.
- Every claim of feature operation must trace primary evidence through
  eligibility, rendered bytes, the immediate provider request/response, and the
  resulting disposition. Module existence, populated tables, or unit tests are
  not live delivery proof.

Target architecture:

```text
task + repository + immutable product identity
                    |
                    v
         canonical GT engine state
  task contract / source rev / graph rev / overlay rev
                    |
       +------------+-------------+
       |                          |
       v                          v
certified immutable base     typed transaction overlay
graph + cached derived       edits/deletes/new files,
layers                       invalidations, test evidence
       |                          |
       +------------+-------------+
                    |
                    v
      one current, bounded decision packet
 task / open-view / edit / failure / verify / submit
                    |
                    v
          Mini-SWE provider request
                    |
                    v
      Mini-SWE reasoning -> action(s) -> real result
                    |
                    +----> normalized GT events and next overlay
```

The base graph is rebuilt only when a query genuinely requires missing current
graph facts or when invalidation is global/unknown. Builds use a frozen
snapshot, coalesce later edits, and publish only if their revision is still
current. Source/transaction facts remain available immediately through the
overlay while a build is pending. This is the core B1/R15/R25 design; it is not
fully implemented yet.

## Shipping path and current code ownership

The supported product path is:

```text
GitHub Actions workflow
  -> pinned DeepSWE task image
  -> Pier 0.3.1 / Harbor 0.20.0
  -> eval/miniswe_agent.py
  -> scripts/miniswe_gt_run.py
  -> Mini-SWE-Agent 2.4.6
  -> GTSession + MiniSweAdapter + installed Groundtruth gateway
  -> task workspace
  -> exported model.patch
  -> official verifier
  -> native receipts/audit/live gate
```

| Concern | Current owner / entry point | Current truth |
|---|---|---|
| Workflow admission, task plan, budgets | `.github/workflows/deepswe_gt_harness_product_p0731.yaml`, `scripts/resolve_harbor_budget.py`, `scripts/smoke_stage.py` | Provider-free gate precedes payment. Gate-one cap is 1,800 seconds. Pier multiplier is derived from the resolved budget. |
| Container agent installation | `eval/miniswe_agent.py` | Installs exact Mini-SWE, harness wheel, vendored Groundtruth wheel/producer, ONNX runtime, and tokenizers; exports `/logs/artifacts/model.patch`. |
| Runner and terminal conservation | `scripts/miniswe_gt_run.py` | Builds the agent/session, enforces inner wall time, bounds visible output/history, retains raw output, classifies terminal state, and exports the complete workspace delta. |
| GT lifecycle state | `gt_engine/gt_session.py`, `gt_engine/miniswe_controller.py`, `gt_engine/miniswe_integration.py` | Task contract/predicates, phases, delivery admission, graph freshness, provider receipts, submit policy, and append-only external state. This is not yet the one unified architecture described above. |
| Runtime interception | `gt_engine/miniswe_runtime.py`, `gt_engine/runtime_observation.py`, `gt_engine/miniswe_evidence.py` | Wraps provider preparation/query/action execution; captures Git-aware before/after snapshots, transactions, semantic events, evidence, and verification observations. |
| Graph build/certification | `gt_engine/indexer.py` plus vendored `gt-index` | Full certified graph build/reuse works narrowly. Stale graphs are no longer read. Efficient current overlay/incremental publication is missing. |
| Model-visible evidence | installed Groundtruth gateway plus `MiniSweAdapter.admit_model_visible_delivery` | At most one gateway winner per action plus task deltas. Exact admitted bytes are content-addressed and must be found at the immediate provider boundary. Priority and packet composition remain inadequate. |
| Attribution and audit | `gt_engine/event_journal.py`, `scripts/gt_audit.py`, `scripts/gt_live_gate.py` | Native trajectory/journal discovery, chain/blob validation, exact request/response joins, real usage, feature census, and fail-closed terminal/schema handling. |
| Release identity | `config/deepswe_product_bundle_v1.json`, vendored wheel/producer pins, review-inbox evidence | Source/tree/binary/build/review lineage is checked before execution. |

Do not treat the older `GTBridge`, `CentralRuntimeBinding`, `GraphLease`,
`context_packet`, retrieval, and catalog modules as shipping merely because
they exist. Astra found that the canonical runner bypasses several of them.
The repair is one production engine with adapters for historical surfaces, not
four synchronized state machines.

## What landed in `1b81dca2`

The commit is pushed to `origin/har81/canonical-task-identity`. Its 28 files
contain the following source repairs:

1. **Run and deliverable conservation**
   - Gate-one paid stage capped at 30 minutes, Pier timeout multiplier aligned
     to the resolved plan, and supervisor reserve increased to 300 seconds.
   - `wall_time_limit_seconds` reaches Mini-SWE; `TimeExceeded` is classified.
   - `model.patch` is exported after normal agent completion against the
     runner-start commit, including staged, tracked working-tree, delete,
     rename, binary, and intent-to-add-visible untracked changes.
   - Setup failures leave typed receipts rather than escaping silently.

2. **Provider/context containment**
   - Muse contributor requests explicitly use structured `xhigh` reasoning.
   - Model-visible command output is capped while exact raw output survives in
     audit metadata and remains available to GT.
   - Old tool results are compacted without deleting assistant reasoning,
     tool arguments, or the latest action batch. Accounting is incremental
     rather than quadratic.
   - Final provider admission is the hard boundary; the 120k history target is
     still a soft mitigation, not a complete context policy.

3. **Graph lifecycle safety and reduced useless work**
   - Initial/refresh indexing uses a typed receipt.
   - Ordinary provider continuation does not rebuild the graph.
   - Literal search, syntax, and status typed actions do not force a full graph
     rebuild; only defined graph-dependent actions can request freshness.
   - An edit invalidates and drops the cached gateway. Stale graph reads and
     stale co-change reads abstain.
   - Advisory submit does not perform a whole graph build whose result cannot
     affect the decision.
   - Workspace snapshots use Git inventory and exclude ignored build output.
   - LSP reports `promotion_not_scheduled` and does not call the producer's
     global unreceipted launcher. LSP is honestly unavailable, not repaired.

4. **Exact native audit truth**
   - The auditor discovers `miniswe_trajectory.json` and exactly one native
     `gt-state/*/events.jsonl` journal in nested Harbor results.
   - Journal chains, blobs, model-visible request hashes, request/response IDs,
     usage, tool/edit/refresh metrics, and all 19 feature identities are read
     from native artifacts.
   - Each admitted GT payload is stored as content-addressed exact UTF-8 bytes.
     WITNESSED requires those bytes in the first immediate provider request,
     strictly sequential request iterations, and its immediate matching
     response.
   - Fact delivery no longer invents execution of its owning capability.
   - Malformed nested trajectory/request/response/event schemas, invalid token
     counts, missing usage, and unknown/failure terminals fail closed.
   - The live gate recursively locates nested Harbor `result.json` files.

5. **Installation/attestation**
   - Wheelhouse and installed-agent paths include pinned ONNX/tokenizer
     dependencies.
   - Attestation installs hash-locked dependencies and verifies the vendored
     Groundtruth wheel before importing its gateway.
   - Declared native capabilities reflect the actual host seam; the invalid
     `engine` CLI mode was removed.

## Verification and independent review

- Local changed-surface suite: **278 passed, one skipped** across 279 tests.
  The skip is the Linux procfs boundary.
- Ruff passed, the workflow YAML parsed, and `git diff --check` was clean apart
  from line-ending warnings.
- Astra's final focused audit/LSP/integration suite: **106 passed**, plus
  independent adversarial valid-chain/blob witnesses.
- Astra found no remaining blocker in the six narrow repairs: exact immediate
  delivery, capability non-invention, malformed artifacts, terminal health,
  usage accounting, and truthful LSP scheduling status.
- The final independent report is committed at
  `astra_whole_system_review.md`, SHA-256
  `732b2a8202af25ccc9f629f223ca3c2cab23b327477bd568ec4bd7b0d4ab3cea`,
  and attached to HAR-83 as “Astra whole-system review — final at 1b81dca2”.

### New CI state: first action for the next session

Provider-free acceptance run
[`33931439713`](https://github.com/harneet2512/gt-harness/actions/runs/33931439713)
for `1b81dca2` is **RED**. All steps through feature-matrix verification passed;
the full Python suite has one failure:

```text
tests/test_failfast_binds_to_real_paths.py::
  test_the_benchmark_runner_lets_the_refusal_through

Expected structural anchor: ensure_index(cwd
Current source call: ensure_index_with_receipt(cwd, ...)
```

The test's helper therefore found no guarded handlers and failed
`assert "BenchmarkGraphRequired" in handlers`. The production call remains
inside `except BenchmarkGraphRequired` handling; current evidence points to a
stale structural test anchor introduced when startup moved to the receipt API,
not to a reproduced fail-fast regression. The next session must inspect both
the AST/test intent and the real exception path, update the assertion only if
the invariant is still truly preserved, run the focused test, then the
provider-free workflow. Completion criterion: the test proves the receipt API's
actual call path and CI is green; a string-only patch without checking behavior
is insufficient.

## What the failed paid run actually proved

Retained run `33915825554` is an external timeout after 5,400 seconds, not a
successful smoke and not evidence that the final edited workspace failed the
official tests.

- 115 provider calls; 25,242,024 cumulative input tokens; 88,570 output;
  17,605,193 cached input.
- 39 graph invalidations and 38 refreshes. Invalidation-to-refresh intervals
  total 4,050.094 seconds; they include intervening work, but the synchronous
  rebuild chain is clearly the dominant avoidable region.
- One 430,894-character tool output was followed by a 117,223-token prompt
  increase.
- 19 GT admissions used 9,568 of the 9,600 lifetime bytes; 82 candidates were
  refused. Ten co-change hints consumed 4,622 bytes while task-start
  localization was refused at 6,309 bytes.
- Fourteen sealed capsules reappeared 1,288 times in later requests.
- The graph was 2.679 GB: 461,376 nodes and 1,030,693 edges. Only 3,832 nodes
  were ordinary source/file nodes; name match supplied 276,887 of 283,101
  resolution candidates.
- The agent made relevant edits, but the process timed out before a usable
  patch was collected. `model.patch` was zero bytes, so the verifier explicitly
  graded the pristine base and reported F2P 0/25.

The repaired auditor correctly keeps this run RED and recovers the real usage.
Its old journal lacks the new delivery-byte blobs, so historical admissions
cannot be upgraded retroactively to independently witnessed exposures.

## Feature status: do not say “all 19 work”

No feature is 100% live-proven against the intended whole-product contract.
The failed run establishes narrow operation only:

| Identities | Current evidence |
|---|---|
| `caller_contract` | Partial: two live delivered views; useful repair effect unproven. |
| `cochange_prior` | Partial: ten live deliveries; relevance/benefit unproven and budget dominance harmful. |
| `localization` / `GT_LOC_RESLOT` | Failed intended task-start placement: oversized localization was refused; two later trace frames are not equivalent. |
| `newfile_precedent` / `GT_CHANGE_SURFACE` | Candidate generation reachable; live candidates were largely refused and useful accepted effect is absent. |
| `obligations` | Partial: initial contract plus four deltas delivered; no completed obligation-verification result. |
| `syntax_result`, `signature_delta`, `GT_EDIT_CHECK`, `GT_PATCH_DELTA` | Partial source/transaction machinery; TypeScript semantic behavior and model-visible current delta are not live-proven. |
| `covering_red`, `def_partition`, `recovery`, `GT_HYPOTHESIS` | No appropriate live delivery witness. |
| `submit_refusal`, `GT_CERT_DELIVERY`, `GT_SS_SUBMIT_RED` | No live submit attempt; paid mode was advisory and non-enforcing. |
| `select_catalog` | Unreachable on the canonical shipping path; standalone catalog types are not runner integration. |

For per-identity producers, inputs, limits, and evidence, use the report's
“Feature-by-feature assessment” and “Production reachability” sections.

## Remaining work, dependency ordered

The wider release verdict is **NOT READY**. Do not start with a paid task.

1. **Restore provider-free CI.** Resolve run `33931439713` as described above
   and retain the green closeout artifact. This is the immediate blocker.
2. **Finish P0 finalization under interruption.** Patch export currently occurs
   after `agent.run()` returns. Prove or redesign patch/receipt conservation
   when the outer process is killed during provider/index/action work. Use one
   absolute deadline with cancellable in-flight work and a protected finalizer.
3. **Build the canonical base + overlay engine.** One service owns task/source/
   graph/overlay revisions, eligibility, current packet, and admission. Normalize
   OPEN/VIEW/EDIT/failure/test/submit from authoritative action/transaction facts;
   multiple applicable subevents may exist for one action. Coalesce full builds
   and prevent obsolete publication.
4. **Make context bounded and useful.** Enforce final request bytes/tokens,
   retain reconstructible raw evidence separately, content-address repeated
   messages/requests, supersede stale capsules, and reserve decision-local
   budget for localization, current failures, change consequences, and
   verification before weak priors.
5. **Repair producer scale without weakening truth.** Narrow candidates with
   sound import/scope/type evidence, intern repeated candidate/provenance sets,
   lazily materialize explanations, and retain explicit incompleteness. Compare
   candidate/selected-target semantics and shipping query answers before/after.
6. **Connect actual graph-native retrieval and verification.** Add independent
   semantic recall rather than dense reranking of lexical top 20; bind vectors
   to current primary entities and a versioned query/document recipe; select
   checks that close current uncertainty and invalidate on complete dependency
   keys.
7. **Implement real dense and LSP consumers only under receipts.** Cache the
   verified ONNX session/vectors after correcting the publisher recipe. LSP
   needs a graph/revision-bound task handle and terminal receipt before the
   harness may schedule it.
8. **Run G0-G2 provider-free gates.** Installed exact bundle, automatic engine
   fidelity, real files/subprocesses, adversarial edit/build/kill sequences,
   and component overhead budgets must pass.
9. **Request separate authorization for G3.** Only then run one GT-on smoke.
   It must conserve a meaningful patch, official verifier binding, native audit,
   and all terminal receipts. Keep the other 19 tasks halted.
10. **Judge outcomes only at G4.** Use the retained four-trial Muse baseline,
    join by task name, preserve model/reasoning/environment/budget differences,
    and report solve/F2P/P2P plus calls, input/cache/output, wall time, cost, RSS,
    graph/storage. Do not rerun GT-off or select favorable trials.

The detailed B1-B14 optimizations and R1-R29 acceptance criteria are in the
Astra report. Preserve their proof boundaries: provider-free improvement is a
release prerequisite, not proof of higher solve rate.

## Safe continuation procedure

1. Read `AGENTS.md`, this handoff, and the Astra report's verdict, architecture,
   current-fix review, R25-R29, performance ranking, and final addendum.
2. Confirm `git rev-parse HEAD` and the branch. Preserve unrelated worktree
   changes. `artifacts/product-closeout-local/` is untracked local evidence and
   was deliberately excluded from `1b81dca2`.
3. Read HAR-83's latest two comments and final Astra attachment. HAR-83 is the
   current status ledger; HAR-78 supplies the intended end-to-end pipeline.
4. Fix and verify one dependency-ordered unit at a time. Update HAR-83 with the
   observed cause, exact evidence, commit, and remaining blockers.
5. Use the exact vendored wheel before relevant local tests because the global
   Python environment may import stale `D:\gt-fh-producer` code:

   ```powershell
   $env:PYTHONPATH = 'D:\tmp\gt-har81-wheel-deab2a69e07c40128dedace0bb91a5a9;D:\gt-har81-canonical'
   $env:GIT_AUTHOR_NAME = 'GT Test'
   $env:GIT_AUTHOR_EMAIL = 'gt-test@example.invalid'
   $env:GIT_COMMITTER_NAME = 'GT Test'
   $env:GIT_COMMITTER_EMAIL = 'gt-test@example.invalid'
   ```

6. Run targeted tests, then the complete affected surface, Ruff, YAML parsing,
   `git diff --check`, and the provider-free acceptance workflow. A local pass
   cannot override red CI.
7. Before commit/push, scan the staged diff for secrets and protected cloud
   identifiers. The repository post-commit hook auto-pushes this branch.

## Hard constraints

- No paid/provider run without a new, explicit authorization for that exact
  dispatch. The 19-task remainder is halted.
- No GT-off rerun. The retained Muse baseline is read-only comparison evidence.
- No GCP authentication, account switching, credential mutation, project
  changes, or credential-directory cleanup. The user alone handles login.
- No secret, account identity, cloud project identity, or key material in Git,
  GitHub, Linear, receipts, logs, task shells, or model-visible context.
- Preserve candidate evidence semantics. Smaller/faster is not success if it
  silently drops ambiguity, raises trust, or disconnects GT from decisions.
- Preserve Mini-SWE's reasoning/action authority. GT supplies deterministic
  evidence and verified controls; it does not replace the model with a scripted
  planner.

## Completion definition

GT Harness is benchmark-ready only when all of the following are true:

- exact installed product identity and provider-free CI are green;
- one canonical engine automatically supplies current graph/overlay evidence at
  every eligible decision boundary with explicit dispositions;
- patch, receipts, request/response evidence, terminal state, and official
  verifier inputs survive every tested exit/kill path;
- graph, context, retrieval, verification, dense, and LSP work fit declared
  cold/warm resource budgets without weakening semantic conservation;
- one separately authorized GT-on smoke proves the real infrastructure; and
- the approved comparison against the retained baseline demonstrates a better
  solve/efficiency frontier with all planned tasks and failures conserved.

Anything less is useful progress, not a benchmark-ready product.
