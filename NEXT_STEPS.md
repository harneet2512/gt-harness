# GroundTruth shortest path to final evidence

## Current execution control

The authoritative TODO ledger is [FINAL_EXECUTION_TODOS.md](FINAL_EXECUTION_TODOS.md).
ARB is retrieval-only. Before any end-to-end claim, the paired decision-point
evaluation must measure whether the model's next action changes when it receives
the exact production evidence. During active implementation, the execution
heartbeat is reported every 15 minutes with SHA, active TODO, verification,
remaining work, next action, blockers, and deviation status.

## Decision principle

Do not add another GT architecture or tune against individual benchmark task IDs. Freeze the current central runtime, test its retrieval mechanism independently, repair only a failed measured mechanism, and then run contemporaneous same-wrapper A/B evaluations. `certified_full` is not the primary treatment because it bundles repository context with completion, progress, compaction, timeout, and controller behavior. The primary treatment is the smallest repository-context arm, `certified_context`; controller/full results are secondary ablations.

## STEP 1 — Freeze the auditable implementation

**Goal:** Create one reproducible candidate manifest for current HEAD without changing runtime behavior.

**Why:** Historical comparisons mixed commits, harnesses, dataset versions, model aliases, and controller bundles. A final result must point to one immutable implementation and dependency set.

**Files:** workflow manifests, dependency lock/configuration, a new experiment manifest under an ignored artifact directory; no GT runtime files.

**Exact change:** Record git SHA, clean/dirty status, Python, Mini-SWE, Harbor, dataset commit/version, graph-index binary hash, model provider/model fingerprint policy, arm configuration, timeouts, parallelism, and evaluator version. Separate repository-context features from controller features in the manifest.

**How verified:** Re-run the existing 1,183-test suite, census, readiness audit, graph-runtime verifier, pre-smoke gate, and static checks. Compare hashes with this audit.

**Time/runtime implication:** 1–2 engineering hours; no paid provider calls.

**Stop condition:** Any dependency, dataset, model identity, or arm cannot be pinned. Do not benchmark until it can.

## STEP 2 — Build the Agent Retrieval Bench V2 adapter

**Goal:** Determine whether current GT retrieval selects useful repository evidence and abstains without involving a coding-model trajectory.

**Why:** Retrieval quality is the largest unmeasured mechanism. Running another agent benchmark before measuring it would confound retrieval defects with model sampling and controller effects.

**Files:** a benchmark-only adapter and tests; no runtime algorithm changes. The adapter must call the same current query construction, graph index, ranking, filtering, and budgets used by `MiniSweCentralAgent`.

**Exact change:** Pin the official 427-sample V2 dataset and corpus revisions. Map `trace2code`, `edit2ripple`, `code2test`, and `comment2context` input fields into non-gold GT query state. Map ranked GT locations to official repository-relative file predictions. For the 82 no-gold cases, allow an empty result. Prevent gold labels, patches, expected files, and evaluator metadata from entering query construction. Emit per-row query fields, candidates, scores, filters, final ranking, abstention reason, latency, and graph status.

**How verified:** Fixture tests prove no gold leakage, exact commit checkout, deterministic repeated output, path canonicalization, empty-result support, and official evaluator compatibility. A second run must produce byte-identical predictions.

**Time/runtime implication:** 4–8 focused engineering hours plus approximately 2–8 CPU hours for download/index/evaluation; no model API cost. The runtime estimate is low-confidence until corpus cache/index throughput is measured.

**Stop condition:** The adapter cannot exercise the same production retrieval path, any sample uses a wrong repository revision, graph construction is incomplete, or repeated predictions differ.

## GATE 1 — Runtime integrity

Current status: **PASS provider-free, not live-confirmed after the latest repairs.**

Required evidence:

1. All 1,183 current tests pass with only the three expected skips.
2. All 17 producer/consumer paths, effect accounting, concrete payloads, first-eligible timing, and no blocked actions pass the census.
3. Repository graph verification proves executable indexer, valid SQLite schema, definitions, and certified directed calls.
4. Exact provider request hashes cover every invoked model call in any later smoke.
5. No runtime code changes after the frozen manifest without restarting this gate.

Failure response: repair the exact integrity defect; do not run a paid benchmark.

## GATE 2 — Retrieval quality

Current status: **UNKNOWN.**

Pre-register these V2 thresholds before seeing current GT scores:

- valid official output for all 427 cases;
- deterministic, byte-identical predictions on rerun;
- positive-set MRR at least 0.2158, Recall@20 at least 0.6333, and BCY@8k at least 0.3788—the published RepoMap baseline values;
- no-gold abstention precision at least 0.90;
- official selective-success score no worse than the always-return GT arm computed from the same predictions;
- every subset reported independently; no aggregate may hide a catastrophic subset failure;
- graph-invalid samples are failures, not abstentions.

These are release floors, not a claim of state of the art. The official V2 page is the metric authority: [Agent Retrieval Bench](https://agent-retrieval-bench.github.io/), [official repository](https://github.com/eyuansu62/agent-retrieval-bench).

Failure response: make one narrow query/ranker/filter repair supported by per-example error analysis, rerun provider-free regression gates and V2, and stop if the repair does not improve the failed metric without harming the other subsets. Do not add a feature family.

## STEP 3 — Conditional retrieval repair

**Goal:** Repair only a Gate 2 failure.

**Why:** Current retrieval ignores several workflow signals, but changing it before measurement would be speculative.

**Files:** only the active retrieval path identified in `CURRENT_GT_RETRIEVAL_AUDIT.md`, plus focused tests.

**Exact change:** Select the smallest error-class repair—query construction, candidate generation, ranking, or abstention. A repair must be generalized by evidence type and repository state, never by benchmark ID, repository name, expected answer, or language-specific witness unless it fixes a real parser contract.

**How verified:** Failing examples become passing for the stated reason; unaffected V2 subsets and the complete provider-free suite do not regress; output remains deterministic.

**Time/runtime implication:** 2–8 hours for one bounded repair. More than one failed repair means the two-day finalization target is no longer credible.

**Stop condition:** Gate 2 passes, or one repair cycle fails. In the latter case report retrieval as not ready rather than continuing an open-ended research loop.

## STEP 4 — Create the scientifically valid A/B harness

**Goal:** Make the control and treatment differ only in repository-context behavior.

**Why:** The frozen 66/89 stock baseline cannot estimate the current central runtime effect, and `certified_full` confounds multiple controllers.

**Files:** benchmark workflow/configuration and reporting code only unless an adapter exposes a runtime defect.

**Exact change:** Run both arms through `MiniSweCentralAgent` with the same task, prompt, model endpoint/checkpoint policy, temperature, tool schema, timeout, completion behavior, context compaction, and evaluator. Arm A uses `integration_mode=off`; Arm B enables `certified_context` only. Preserve `certified_controllers` and `certified_full` as secondary factorial ablations. Record model fingerprint per call and fail the matched comparison if identities drift. Report official reward, uncensored resolved, exceptions, tokens by cache class, calls, model actions, effective actions, wall time, GT characters, graph applicability, and graph failures.

**How verified:** A provider-free dry run validates configuration equivalence; the report rejects any pair with dataset/model/harness mismatch. One synthetic task verifies that OFF adds zero GT bytes and context mode delivers one grounded first-eligible payload.

**Time/runtime implication:** 3–6 engineering hours; no paid calls until approval.

**Stop condition:** Any non-GT configuration differs between arms or model identity cannot be audited.

## STEP 5 — Run a bounded current-HEAD matched smoke

**Goal:** Confirm live transport and outcome preservation after `dd2884e`/current HEAD before a large suite.

**Why:** The latest 20-task live run predates the fixes and is rejected evidence. Provider-free proof cannot establish real provider behavior or sampled outcomes.

**Files:** frozen experiment manifest and ignored run artifacts only.

**Exact change:** With separate paid-run approval, run a mixed bounded set containing known historical gains, losses, both-fail tasks, source-applicable tasks, and legitimate graph-not-applicable tasks. Use the same-wrapper OFF and context-only arms. Do not use `certified_full` as the primary comparison.

**How verified:** Audit every task for graph applicability/readiness, exact request hash coverage, zero late/predictive/duplicate delivery, grounded content, no false progress, no context overflow, outer exceptions, and full resource accounting. Classify losses by first divergence rather than labeling them automatically as GT-caused.

**Time/runtime implication:** Approximately 2–4 hours wall time depending on set size and queue; paid model usage.

**Stop condition:** Any P0 integrity failure, graph failure on a source-applicable task, wrong/late evidence, model identity mismatch, or new GT-attributable outcome loss. Temperature-1 score variation alone is reported, not silently repaired against task IDs.

## GATE 3 — End-to-end smoke

Current status: **FAIL/UNRUN on current HEAD.**

Pass requires runtime integrity plus no GT-attributable regression. Aggregate resource metrics are secondary because a bounded stochastic smoke is descriptive; it must nevertheless report common-solved and outcome-first denominators honestly. A score decrease does not prove GT caused the loss, but it blocks a positive release claim until trajectory evidence excludes a GT mechanism defect.

## STEP 6 — SWE-bench-Live Lite controlled A/B

**Goal:** Establish the primary contemporary issue-resolution result.

**Why:** This is the most direct end-to-end test of repository context on current GitHub issues.

**Files:** a current central-agent SWE-bench-Live adapter, pinned 300-task manifest, evaluator bridge, and experiment workflow.

**Exact change:** Wire the frozen Lite tasks to the same-wrapper OFF/context arms. Keep gold patch/test fields outside the agent environment. Verify images and official evaluation before full dispatch. Run a small infrastructure-only smoke, then 300 tasks per arm if approved.

**How verified:** Same task/base commit/container/model/harness/evaluator across arms; complete predictions; official resolved rate; paired outcome table; source-applicable subgroup; GT integrity and efficiency metrics.

**Time/runtime implication:** 8–16 engineering hours before the smoke; up to a 12.5-hour task-timeout envelope per arm at parallel 20, plus image setup and evaluation; paid cost unknown until provider rates are supplied.

**Stop condition:** Evaluator/container mismatch, gold leakage, incomplete arm, model drift, or a material outcome regression with a GT-attributable mechanism defect.

## GATE 4 — SWE-bench-Live decision

Pre-register the primary endpoint as official resolved count/rate. Secondary endpoints are source-applicable resolved rate and outcome-first resource use. Do not require every task to use fewer tokens at temperature 1. Proceed only if the treatment is operationally valid and does not show a material negative primary result requiring investigation. A single 300-task arm is evidence for that fixed rollout, not a universal causal guarantee.

## STEP 7 — DeepSWE generalization

**Goal:** Test long-horizon multilingual repository work after the primary result.

**Why:** DeepSWE stresses 113 tasks across 91 repositories and five languages under separate verifier environments.

**Files:** Pier/Harbor adapter, artifact bridge, central-agent configuration, and frozen manifest.

**Exact change:** Port the same-wrapper arms without changing GT semantics. Prove repository handoff, cwd, source revision, graph coverage, task output handling, and verifier isolation on a small multilingual smoke before full dispatch.

**How verified:** Official Pier verifier, all-task and per-language results, graph-applicable subgroup, integrity receipts, and paired resource metrics.

**Time/runtime implication:** 12–24 engineering hours; estimated 6–12+ hours per arm after images are ready; paid cost unknown.

**Stop condition:** Any language/substrate cannot meet the graph contract, or the adapter changes agent semantics between arms.

## GATE 5 — DeepSWE generalization

Pass requires a valid complete run, no systematic graph-invalid language subgroup, no GT-attributable safety regression, and transparent all-task/per-language/applicable-subgroup outcomes. No arbitrary solve-rate threshold is invented before measuring the selected model's same-wrapper baseline.

## STEP 8 — Terminal-Bench 2.0 product integration-safety evaluation

**Goal:** Test broader harness safety on the already integrated, frozen 2.0
product cohort.

**Why:** The current product contract and language-coverage gate target 2.0.
Version 2.1 changed 28 of 89 tasks, so this result remains explicitly TB2.0
diagnostic evidence and cannot be submitted or described as TB2.1.

**Files:** TB workflow dataset pin, static task contract, merge/evaluation report.

**Exact change:** Retain the already pinned TB2.0 89-task contract, verify its
language/applicability closure, retain parallelism 20, and run same-wrapper
OFF/ON arms. Report both all-task and source-applicable-subgroup results.
Correct no-source abstentions are excluded from the repository-intelligence
denominator; graph failures are not.

**How verified:** Harbor official rewards and exception states, complete 89-task artifacts per arm, identical versions/model/harness, graph applicability audit, exact request hashes, and paired metrics.

**Time/runtime implication:** 4–8 engineering hours and an estimated 5–7 hours per arm; historical token scale suggests roughly 0.4–0.5 billion total token accounting across both arms. Dollar cost is unknown without provider pricing.

**Stop condition:** TB2.0 task-contract mismatch, incomplete arm,
source-applicable graph failure, or any P0 experimental defect.

## GATE 6 — Final evidence

Final claims must be separated:

1. **Mechanism:** Agent Retrieval Bench retrieval/ranking/abstention metrics.
2. **Primary task outcome:** SWE-bench-Live Lite same-wrapper A/B.
3. **Long-horizon generalization:** DeepSWE.
4. **Harness safety/general applicability:** Terminal-Bench 2.0 product
   diagnostic, all-task and source-applicable subgroup; no TB2.1 claim.
5. **Efficiency:** calls, model/effective actions, cache-class tokens, and wall time conditioned first on outcome; never inferred from failure-shortened trajectories.

No single score proves the whole architecture, and TB2.0 product evidence is
never substituted for a TB2.1 leaderboard result.

## Two-day reality check

### Can finish in approximately two working days

- Freeze the current candidate and reproduce all provider-free gates: 1–2 hours.
- Build and validate the Agent Retrieval Bench adapter: 4–8 hours.
- Index/evaluate V2 and perform one bounded error analysis: 2–8 CPU hours, partly unattended.
- If V2 passes without a repair, create and dry-run the same-wrapper A/B manifest: 3–6 hours.
- Prepare, but not necessarily complete, one bounded paid smoke after explicit approval.

Optimistic total focused engineering is 8–16 hours plus unattended indexing/smoke runtime. A single narrow retrieval repair adds 2–8 hours and consumes the available slack.

### Cannot honestly finish in two working days

- Implement, smoke, run, and analyze both 300-task SWE-bench-Live arms.
- Integrate Pier, run both 113-task DeepSWE arms, and analyze multilingual graph coverage.
- Run and analyze both 89-task Terminal-Bench 2.0 product-diagnostic arms.
- Complete all three end-to-end suites after Agent Retrieval Bench.
- Establish universal or statistical guarantees that a temperature-1 model never regresses.

### Can start but not fully establish in two days

- SWE-bench-Live adapter/container preparation.
- Terminal-Bench 2.0 exact-pin and static-contract re-verification.
- One end-to-end arm if infrastructure is warm, but not a defensible complete A/B plus analysis.
- Cost forecasting after provider input/cache/output rates are supplied. Current artifact `$0` values are not real price evidence.

## Immediate approval target

The first executable task after approval is **Agent Retrieval Bench V2 adapter and deterministic evaluation**. It is provider-free, directly tests the unproven retrieval mechanism, and determines whether any runtime retrieval repair is justified before spending money on coding-agent trajectories.
