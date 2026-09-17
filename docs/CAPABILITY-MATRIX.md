# GT capability matrix — the standing reference

The registry holds **21 identities**: 14 direct features + 7 alias caps.
This file is the one place that says, for each identity, *what must fire on
every task*, *what fires only when the task creates the precondition*, and
*what was ever dead and how it was repaired*. Anything that contradicts it is
a bug or a stale doc — not a third category.

Verdict vocabulary (as journaled in `feature_attribution`):

- `WITNESSED` — evidence reached a provider request on this task.
- `INELIGIBLE` — the trigger never held on this task. **Correct-or-quiet is
  the designed verdict** — the capability costs zero tokens when absent.
  INELIGIBLE is only a defect when the trigger actually held, or when the
  producer can never run (a dead gate).
- `no_trigger_observed` — the journaled reason for a clean INELIGIBLE.

## Class 1 — mandatory: fires on every task, a miss is a defect

| Identity | Fires at | Live evidence |
|---|---|---|
| `localization` | task start | 6 deliveries, `provider.request` exposure, 35178222629 |
| `obligations` | task start | 1 delivery, 35178222629 |
| `persistent_plan` | task start (bootstrap or deterministic floor) | `capability_applied`, 35178222629 |
| `plan_gate` | every submit attempt | `capability_applied`; 5 enforced refusals + clean accept, 35170780678 |
| `select_catalog` | delivery selection | 2 deliveries, 35178222629 |
| `submit_refusal` | enforcement: the suppression mechanism itself | 5× `action_suppressed reason=submit_refused`, 35170780678 |

`submit_refusal` is class 1 as a *mechanism* (it must work whenever a dirty
submit is attempted) and class 2 as a *delivery* (a task where the model only
submits clean never shows it — correct).

## Class 2 — trigger-dependent: correctly INELIGIBLE when the task never creates the precondition

| Identity | Trigger | Live status |
|---|---|---|
| `caller_contract` | boundary where caller facts exist for the touched symbol | INELIGIBLE on 35178222629 — no trigger observed |
| `cochange_prior` | co-change rows for touched files | WITNESSED — 6 deliveries |
| `covering_red` | an observed failing test attributed to the model's own edit | INELIGIBLE this run; starvation defect fixed in `0479fb14` |
| `def_partition` | a search classified AMBIGUOUS_HIT/FLOOD (symbol defined in ≥2 files) | never invoked: 33 search-boundary events on the gate-one journal, zero ambiguous/flood — the trigger genuinely never held |
| `newfile_precedent` | a file-creation edit | WITNESSED — 3 deliveries |
| `recovery` | an identical failure fingerprint recurring after an edit | WITNESSED once on gate runs; INELIGIBLE on clean-run tasks |
| `signature_delta` | an edit changing a callable signature that has callers | invoked on all 58 edit boundaries, correctly quiet — fix tasks rarely change signatures |
| `syntax_result` | a syntactically invalid/incomplete edit | dead gate until `0479fb14`; now reactive — delivers on every edit that breaks syntax, in every mode |

## Class 3 — aliases: no independent producer; verdict inherits the parent

| Alias | Resolves to | Note |
|---|---|---|
| `GT_CERT_DELIVERY` | `submit_refusal` | the cert machinery's env flag is nano-bridge-only; on Mini-SWE the `plan_gate_decision` record is the equivalent evidence. Structurally unreachable as a *distinct* path — flagged, not a benchmark blocker |
| `GT_CHANGE_SURFACE` | `newfile_precedent` | |
| `GT_EDIT_CHECK` | `syntax_result` | shared the dead gate; fixed in `0479fb14` |
| `GT_HYPOTHESIS` | `recovery` | |
| `GT_LOC_RESLOT` | `localization` | |
| `GT_PATCH_DELTA` | `signature_delta` | |
| `GT_SS_SUBMIT_RED` | `submit_refusal` | |

## Dead-gate register — everything that was structurally dead, and its fix

| Defect | Why it could never fire | Fixed in |
|---|---|---|
| `syntax_result` (+`GT_EDIT_CHECK`) | only producer was `run_syntax_probe`, gated on ASSISTIVE mode + `GT_ALLOW_LIVE_PROBES=1`; benchmarks run advisory → unreachable on every task | `0479fb14` — reactive lane delivers the already-computed `compile_transaction_artifacts` syntax verdict on every broken edit |
| `covering_red` starvation | `elif` ordering: with probes enabled, `run_covering_lane` returning None skipped `attribute_test_failure` — the model's own failing test was discarded (`no_covering_result_threaded`) | `0479fb14` — attribution runs whenever the probe produced nothing and the test failed |
| dense receipt conflation | a `graph_snapshot_not_current` *precondition refusal* was journaled as `dense_index_ready query_ready=false`; last-row-wins at the product receipt read it as `treatment_dense_index_not_ready` even while the measured index served fine (task solved, attestation FAIL — 35178222629) | this commit — the lexical re-rank does not need the graph; it now runs during stale windows and journals a real execution receipt |
| graph freshness starvation | `batch_amend_floor` (~1.7 GB on a ~93k-node parent) sat permanently above the cgroup limit (~1.4 GB): 58 refusals, zero recovery — the defer window waited for pressure that never drained | this commit — a headroom-refused batch amend now falls back to the per-file `incremental_amend_in_place` lane (memory scales with the dirty set, not the parent); uncoverable dirty sets keep the memory-typed refusal |
| no-plan submit-gate bypass | plan bootstrap transport timeout → `plan_submit_gate` early-returned before `unmapped_red_predicates` → a submit over 3 live RED predicates sailed (35168421439) | `e84d646b` — `decide()` refuses on self-describing evidence without a plan; bootstrap failure installs the floor plan |
| post-terminal verdict gap | a runtime-assembled submit marker executed before detection and journaled no gate verdict — "no gate row" was indistinguishable from "gate saw clean" | `b02e06ca` — read-only `plan_gate_decision enforcement=post_terminal` on the advisory path |
| `plan_gate_budget` session-kill | attribute read of `agent.config` raised into the fail-open path and silently disabled all post-execution observation | `e84d646b` — defensive `getattr` |

## Residual risks a benchmark run can still hit (honest, not blockers)

- **Large dirty set + small cgroup**: a dirty set the per-file lane cannot
  cover (`dirty_paths_exceed_limit`, `config_input_changed`, all-non-parser
  files) keeps the memory refusal — the graph can still go stale for a
  stretch on a big repo. Journaled as memory-typed deferral, not silent.
- **Trigger-absent cohorts**: a 20-task cohort where no task creates files
  or changes signatures will show `newfile_precedent`/`signature_delta`/
  `def_partition` INELIGIBLE on every task. That is the correct verdict —
  the fleet gate fails only on `STARVED`/unexplained `NO_DELIVERY`, not on
  honest trigger absence.
- **Graph-stale windows** still abstain the *semantic* (graph-ranked)
  localization — by design; the lexical + dense re-rank path keeps serving.

## How to read a cohort journal

1. Per identity: `WITNESSED` = delivered to a provider request;
   `INELIGIBLE` = trigger never held (correct); anything else = audit it.
2. The fleet gate accepts only `DELIVERED` / `DECLINED_CORRECTLY` /
   `NOT_REACHED` / named-unknown. `STARVED` or unexplained `NO_DELIVERY`
   keeps the benchmark claim closed.
3. Provider-free matrix `WITNESSED` ≠ live delivery — it means the CI
   positive+negative test pair passed. Live proof requires
   `exposure_source=provider.request` in `feature_attribution`.
