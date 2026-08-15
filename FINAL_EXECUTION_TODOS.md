# GroundTruth Final Execution TODOs

This is the authoritative execution ledger for the retrieval-plus-reasoning
proof. Only one item may be `in_progress`. Paid provider work is blocked until
the relevant gate and explicit authorization are recorded.

## Control rules

- ARB proves retrieval quality only; it does not prove model reasoning or task success.
- Model utility is measured at paired decision points with exact control/treatment requests.
- No markers, acknowledgements, chain-of-thought inspection, or benchmark-task heuristics.
- No GT runtime changes after `FINAL_GT_MANIFEST.md` is frozen.
- Every 15-minute heartbeat records SHA, worktree, completed work, active TODO,
  verification, remaining work, blocker/permission, and deviation check.

## Ledger

| ID | Status | Acceptance condition | Evidence | Next action |
| --- | --- | --- | --- | --- |
| GT-FINAL-001 | complete | Four-layer proof contract documented | `FINAL_EXECUTION_PLAN.md` | continue baseline |
| GT-FINAL-002 | complete | ARB retrieval-only claim and paired reasoning contract documented | `RETRIEVAL_BENCH_CONTRACT.md`, `DECISION_POINT_EVAL_CONTRACT.md` | verify current defect evidence |
| GT-FINAL-003 | complete | Current SHA/config/environment captured | `artifacts/final_execution/baseline.md` | continue contract work |
| GT-FINAL-004 | complete | Verified P0/P1 defects reproduced and provider-free gates recorded | live/ARB profile RED tests; GitHub run `31526751148` | continue runtime proof |
| GT-FINAL-005 | complete | Runtime delivery/abstention/failure proof complete | real Snowflake local/GitHub witnesses; typed contribution compiler; `FINAL_RUNTIME_PROOF.md` | paired decision-point evaluation |
| GT-FINAL-006 | complete | Gold-isolated ARB adapter exercises production retrieval | `scripts/arb_adapter.py`, `tests/test_arb_adapter.py` | prepare official data |
| GT-FINAL-007 | complete | Complete 427-row ARB run is pinned, gold-isolated, evaluated, and retained | run `31517629497`; `RETRIEVAL_BENCH_RESULTS.md`; `D:\gt_runs\arb-31517629497` | connect the frozen profile to live Mini-SWE |
| GT-FINAL-008 | complete | One generalized hybrid retrieval repair completed and frozen | commits `55553a3` through `433c330`; ARB final metrics | no further retrieval tuning |
| GT-FINAL-009 | complete | Bounded paired decision-point utility evaluation and report complete | `DECISION_POINT_UTILITY_RESULTS.md`; captures `31530343093`, `31531620414`, `31532480146`; controls `31534502404`, `31534732127`, `31534734333` | outcome smoke gate |
| GT-FINAL-010 | complete | Repaired GT integration passes exact provider-free GitHub certification | commit `338b391`; provider-free run `31544885372` | request authorization for corrected matched smoke |
| GT-FINAL-010A | pending | Corrected ten-task matched smoke preserves runtime integrity and reports outcome/resource deltas | new GT-on artifacts plus existing matched control where scientifically usable | paid run requires separate authorization |
| GT-FINAL-010B | complete | Conservative decision-sufficiency compiler and actual Mini-SWE action boundary are provider-free proven | `gt_engine/decision_sufficiency.py`; action-loop SHADOW/ASSISTIVE_SAFE tests; biting visibility perturbation | certify exact tree on GitHub |
| GT-FINAL-010C | complete | DeepSWE substrate failures have generalized repairs and release-gate coverage | derived-tree manifest pruning; unhealthy-snapshot full rehash; refresh-race removal; archived artifact replay blocks exactly 3/10 defective substrates | certify current Go indexer on GitHub |
| GT-FINAL-010D | in_progress | Current exact tree passes the full provider-free GitHub workflow with current-source indexer and pinned Snowflake asset | pending workflow run | commit/push required before dispatch |
| GT-FINAL-010E | pending | Ten-task SHADOW qualification reports all decision receipts and zero runtime-release failures | paid GitHub smoke artifacts | requires 010D and authorization |
| GT-FINAL-011 | pending | Same-wrapper SWE-Live contract and run complete | final A/B artifacts | requires authorization |
| GT-FINAL-012 | pending | Existing DeepSWE-off artifact passes the exact schema/identity/outcome gate, or a new same-workflow control is produced before GT-on comparison | `DEEPSWE_FINAL_RESULTS.md` | never use an invalid or censored control |
| GT-FINAL-013 | pending | Terminal-Bench 2.0 evaluated through Mini-SWE after DeepSWE | `TERMINAL_BENCH_20_RESULTS.md` | conditional |
| GT-FINAL-014 | pending | Final causal report and verdict complete | `GROUNDTRUTH_FINAL_REPORT.md` | close project |

## Current stop state

### 2026-08-12 implementation update

The current worktree implements the missing conservative decision boundary and
the three generalized DeepSWE substrate repairs. The per-action boundary does
not run dense inference; it uses one bounded target-and-structural-neighbor
slice and returns eligibility only for complete mechanically certified evidence
absent from the exact selecting provider request. Paid workflows remain
`SHADOW`, so this new accounting cannot alter commands or add model calls.

Local widened verification is 303 passed, 3 failed, 1 skipped. All three
failures share one environment cause: the untracked Windows `gt-index.exe` is
older than the current Objective-C language registry. Go is not installed on
this workstation and Docker Desktop is not running, so the required authority
is the existing GitHub provider-free job that builds the current Go source.
The real Snowflake test is the one skip because its pinned asset is provisioned
by that same workflow. Ruff, Python compilation, four workflow YAML parses,
diff checking, focused action-loop tests, decision tests, and release-gate tests
pass. Archived DeepSWE replay makes seven healthy receipts pass and blocks
exactly `arktype`, `boa`, and `csstree` for their recorded substrate failures.

No paid run is approved from the dirty/unpushed tree. The immediate remaining
work is GT-FINAL-010D: review, commit/push, and run the exact provider-free
workflow. Only a green exact-SHA result can advance to GT-FINAL-010E.

`GT-FINAL-010` is complete. ARB remains the retrieval-only authority and the
paired decision-point work remains a behavioral proxy, not model
acknowledgement. The repaired live integration is commit `338b391`. Exact
provider-free workflow `31544885372` built the current Go indexer, provisioned
and exercised the pinned Snowflake ONNX asset, proved the language contract,
passed the complete central runtime suite, printed `READY` and
`SMOKE_APPROVED`, passed static checks, and recorded `provider_calls: 0`.

The authorized matched outcome smoke is complete but rejected as repaired-system evidence: treatment
`certified_context` run `31535815764` versus central GT-off `31535955624`, both
at commit `9ca48b9` and the frozen ten-task repair mix. Treatment resolved 7/10
versus 8/10 baseline, gaining `regex-chess` but losing `qemu-alpine-ssh` and
`sanitize-git-repo`. Total tokens fell 1.22%, but common-solved tokens rose
9.43%, model calls/steps rose 5.62%, and effective task actions rose 14.59%.
Canonical reconstruction finds 60 deliveries/44,372 characters, not seven.
Timing/hash checks pass, but 44 old preemptive receipts lack persisted semantic
support; the matrix also lacked the live Snowflake backend and runner-kernel
identity perturbed the initial prompt. Model causality remains unidentifiable. See
`SMOKE_OUTCOME_CONTRACT_20260811.md` and `SMOKE_OUTCOME_RESULTS_20260811.md`.
The implementation/integration repair is verified. The next step is a new
authorized ten-task matched GT-on smoke using the repaired matrix. It must have
zero canonical delivery failures, explicit live dense receipts on every
applicable source task, source-less abstention, and complete baseline/control
request accounting before outcome/resource deltas are interpreted. The paid
smoke and the 89-task run remain blocked pending separate authorization.

## Work plan mapped to the GT objective

### Phase 0 — Freeze the question and controls

- [x] Define the four claims separately: retrieval, delivery, model decision,
  and task outcome.
- [x] Record that ARB cannot prove model reasoning or end-to-end benefit.
- [x] Pin the active branch/commit and preserve the historical baseline only as
  non-causal reference evidence.
- [x] Resolve the exact-pushed-tree publication gate without bypassing it.
- [x] Reproduce each current live-retrieval P0/P1 defect against executable code and mark it
  `must_fix`, `measurement_only`, or `not_reproduced`.

### Phase 1 — Prove the deterministic GT engine

- [x] Verify all-17 producer/consumer/timing/payload/context-accounting gates.
- [x] Verify graph substrate, parser coverage, and readiness provider-free.
- [x] Capture local runtime proof for grounded dense delivery and warm abstention with exact request hashes.
- [x] Capture GitHub runtime proof for grounded delivery, correct abstention, and
  graph failure with exact request hashes.
- [x] Prove locally no extra agent action, no late delivery, no predictive delivery,
  no duplicate fact, and no stale-revision evidence.
- [x] Produce `FINAL_RUNTIME_PROOF.md`.

### Phase 2 — Prove retrieval quality independently of model sampling

- [x] Pin official ARB source at `07014c986f3deadb1548c62b32c0ffbe6a81465d`.
- [x] Implement the gold-isolated adapter through GT’s production contract,
  graph projection, evidence need, and ranker.
- [x] Reject recursive gold/fix/patch/evaluator leakage.
- [x] Separate index-build latency from post-index query latency.
- [x] Download and validate official V2 benchmark/corpus releases.
- [x] Prepare redacted input JSONL containing only query state and declared
  given files.
- [x] Move corpus/index/baseline execution to the pinned GitHub workflow;
  local memory-heavy evaluation is prohibited.
- [x] Evaluate the complete 427-row GitHub run and compare against official leaderboard baselines.
- [x] Dispatch GitHub lexical/BM25/RepoMap-compatible baselines with
  `all_files` and retain run artifacts.
- [x] Dispatch GitHub GT candidates and bounded delivered evidence; report
  both.
- [x] Classify misses as query, index, graph, ranking, redundancy, over-
  retrieval, failed abstention, or unrepresentable input.
- [x] Allow at most one generalized retrieval repair if the repeated-defect
  rule is satisfied.
- [x] Produce `RETRIEVAL_BENCH_RESULTS.md`.

### GitHub execution controls

- [x] Use immutable action SHAs and the pinned ARB source commit.
- [x] Use twenty balanced independent exact-base snapshot shards.
- [x] Keep gold/fix/patch/evaluator fields out of GT inputs.
- [x] Upload per-shard receipts and optional official baseline details.
- [x] Push the workflow and dispatch it from the intended harness SHA.
- [x] Verify uploaded artifacts and write the retrieval results report.

### Phase 3 — Prove whether the model’s next decision changes usefully

- [x] Locate replay-ready first-visible-intervention points; archived run
  `31421610097` has 0/1,051 because the legacy bundle omitted exact controls.
- [x] Implement opt-in exact control/treatment capture and reject pairs whose
  provider-visible difference is not exactly the compiled GT payload.
- [x] Make the paid GitHub capture bound explicit (`step_limit=1`) while
  preserving the normal workflow default of 100 calls.
- [x] Reject and cancel capture run `31529376771`: its first artifacts exposed
  that Mini-SWE's built-in Bash schema was not included in the pair receipt;
  no result from that run is evidence.
- [ ] If unavailable, obtain explicit authorization for bounded SHADOW capture;
  do not run a full paid benchmark merely to collect these points.
- [ ] Build exact control/treatment provider requests differing only by the
  production GT payload.
- [ ] Reject pairs with prior visible GT context, stale facts, duplicate facts,
  missing responses, or non-GT byte differences.
- [ ] Run one response per arm per distinct decision point with the same model,
  prompt, tools, sampling, and limits.
- [ ] Mechanically classify next actions as beneficial, harmful, equivalent, or
  indeterminate without markers or hidden-reasoning claims.
- [ ] Report paired sign-test results and all harmful cases.
- [ ] Produce `DECISION_POINT_EVAL_RESULTS.md`.

### Phase 4 — Freeze the final GT candidate

- [ ] Pass runtime, retrieval, and decision-point gates.
- [ ] Freeze GT SHA, Mini-SWE SHA, graph/index binary, model fingerprint,
  prompts, thresholds, evidence budget, containers, and evaluator.
- [ ] Produce `FINAL_GT_MANIFEST.md`.
- [ ] Make all later benchmark runs read-only with respect to GT code.

### Phase 5 — Establish end-to-end product evidence

- [ ] Run DeepSWE first after freeze, using a GitHub-hosted same-wrapper
  baseline/treatment adapter and its current official protocol.
- [ ] Analyze every DeepSWE gain/loss by first trajectory divergence; do not
  attribute zero-intervention differences to GT.
- [ ] Verify and run the checked-in GitHub DeepSWE workflow
  (`.github/workflows/deepswe_miniswe_central.yml`); it pins Pier 0.3.1,
  performs commit-only patch collection and separate-verifier grading, and
  launches Mini-SWE only (no OpenHands/OpenAgents path). Its one-rollout,
  300-call, 5,400-second contract is a matched product experiment, not the
  official multi-rollout leaderboard protocol.
- [ ] Run Terminal-Bench 2.0 next through the existing GitHub Mini-SWE/Harbor
  workflows, with all-task and source-applicable results.
- [ ] Run contemporaneous same-wrapper SWE-Live Lite only after the DeepSWE and
  Terminal-Bench gates, if still needed for the final product claim.
- [ ] Report resolution first, then outcome-conditioned calls, steps, actions,
  tokens, cost, wall time, GT context, and graph applicability.
- [ ] Produce the final causal report and stop the project.

## Heartbeat contract

Every 15-minute update must show the phase, active TODO ID, completed IDs,
verification evidence, remaining IDs, next command/action, blockers or required
authorization, current/local-vs-origin SHA, and whether scope has deviated. A
heartbeat is not permission to cross a paid-run or freeze gate.
