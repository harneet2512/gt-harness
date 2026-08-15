# GT Central Runtime Implementation

**Status:** provider-free implementation complete; paid GT-on experiments not yet run  
**Date:** 2026-08-03  
**Historical comparator:** frozen GT-off, 66/89 solved

## Decision

The inline installed engine is no longer the active paid-evaluation path. GT is
implemented as a host-owned Harbor `BaseAgent`: it owns the model loop and every
action transition, while its code, state, configuration, provider credentials,
and receipts stay outside the task container.

This is an isolation change, not a reduction in integration. Every model action
still crosses the central runtime before and after `BaseEnvironment.exec`.

## Latest smoke comparison and coverage correction

The archived ten-task smoke `30976148466` (GT-on shadow, commit `951e136`) is
compared with the frozen GT-off baseline in
`GT_SMOKE_30976148466_BASELINE_COMPARISON.md`. It was 4/10 solved versus 9/10
baseline, with 694 versus 420 calls and 698 versus 483 actions. Its reported
4.71M tokens versus 28.68M baseline tokens is censored/descriptive because six
GT-on tasks reached the step limit and five baseline solves were lost; it is
not an efficiency win.

Do not confuse the 17-path census with natural paid coverage. The smoke fired
15/17 feature IDs; `recovery` and `signature_delta` had no receipts. It still
applied 361 effects; only 36 were model-visible provider payloads. The effect
trace and summary now distinguish deterministic `engine_internal_state` work
from actual provider delivery and from genuinely unread private state.

### Feature applicability and repository runtime (2026-08-06)

Natural firing count is no longer the coverage metric. Every feature is now
classified per task as fired when eligible, correctly abstained, trigger
absent, ambiguous, substrate unavailable, or an implementation miss. This was
required after corrected smoke `31136099371`: all 38 repository refreshes were
`index_unavailable`, so the missing `caller_contract` and `def_partition` were
not legitimate task-trigger absences. Only `recovery` and `signature_delta`
lacked their exact lifecycle events.

The workflow now installs the vendored GroundTruth runtime, exports the pinned
index binary, and proves a real binary-to-SQLite graph before provider spend.
Task-start structural evidence separates graph definitions, graph references,
and certified directed callers. Raw grep text supplies localization anchors
only; it can never certify definition/reference/caller semantics. Ambiguous or
empty evidence abstains without an empty effect.

Provider evidence is first-window-only. Distinct compatible same-action claims
are coalesced once; a claim that cannot fit remains controller state and cannot
appear in a later call. The provider-free gate now rejects eligible trigger
misses, false fires, empty localization, unverified callers, duplicate frame
evidence, or an unavailable repository substrate.

### Benchmark language resolution and graph completeness (2026-08-08)

The repository substrate now resolves a source identity from path plus bounded
content before parsing. This closes the critical `.v` collision: Coq and
Verilog use the same suffix, so declaration signatures select the dialect and
conflict or insufficient evidence abstains as `AMBIGUOUS`. The same resolver
handles Nginx `.conf` files, exact build-file basenames, and extensionless
shebangs. Python coverage and the native Go walker use matching rules.

The native indexer adds conservative structural adapters for Coq, Stan,
SPARQL, Turtle, LaTeX, Vim, Nginx, and G-code and for Make, Dockerfile, CMake,
Meson, and Autotools control files. These parsers expose only fixture-proven
declarations, imports, references, and calls. Unknown syntax produces no
speculative relationship. Parser failures are persisted in graph metadata and
make `coverage_complete` false.

The parser-to-index boundary is now explicitly zero-based. A strengthened
runtime fixture checks per-language directed `CALLS` edges, catching the prior
structured-adapter off-by-one that produced in-memory calls but no certified
graph edges. COBOL receives a grammar-backed paragraph/`PERFORM` ownership pass
because those nodes are siblings rather than a conventional function body.

Benchmark completeness is checked independently of registry parity. The
checked-in Terminal-Bench 2 contract pins the official repository revision,
requires exactly 89 tasks, proves named language witnesses and declared
source-like suffix families, and rejects any registry-recognized structural
suffix observed but unclassified. The provider-free
runtime fixture must execute the FTS-enabled binary, validate SQLite, observe
each structural language in `file_hashes`, produce concrete graph nodes, and
report zero parser failures.

This closes the language-substrate gap for the workspace plus the explicitly
allowlisted external service paths. `/etc/nginx/**` and
`/var/log/nginx/**` are captured only when named by the task, revision-tracked,
and authored Nginx configuration is mirrored under `__external__/` for graph
health accounting. Extensionless files are bounded shebang candidates and are
source-backed only after content proves a supported interpreter. No broad
external scan is permitted. This still does not prove retrieval relevance,
provider delivery, solve preservation, or efficiency.

## Deterministic context-compiler repair (2026-08-05)

The regression diagnosis found that the host was central in execution but not
yet rigorous enough in context selection. It reconstructed only one primary
operation from compound Bash, populated recent reads mainly from search output,
labelled active facts as "represented by full history" without proving where,
and used a dedupe fingerprint that could collapse turns with different
Mini-SWE reasoning. Lossy compaction also started far below the provider's
actual context limit in the failed smoke, removing old reasoning without an
outcome-preservation proof.

The repaired request path is:

```text
durable Mini-SWE history + typed controller state
  -> exact-turn dedupe (reasoning/content/commands/results all included)
  -> ContextFact inventory with source/workspace revision
  -> exact representation proof at provider message indices
  -> bounded selection only for current material facts absent from history
  -> final request hash
  -> model.query
  -> next-action anchor-alignment measurement
```

Every candidate fact is accounted as `represented_message`,
`selected_state_frame`, `controller_only`, `stale_source_revision`, or
`state_frame_budget`. The compiler cannot silently call a fact represented and
cannot truncate through the middle of a fact. Controller revisions and private
feature state affect deterministic selection without becoming prompt text.
Requirements remain revision-persistent; validation, failure, read, change,
and structural source evidence are revision-bound.

Every compound shell segment is typed independently and the same
`ProposedAction` reaches preflight and postflight. Read observations record
canonical path, requested line range, revisions, return code, and output hash.
Validation is attached only to the segment that actually runs the check.
Ambiguous executable behavior remains `OTHER` and therefore defaults to PASS.
The archived ten-task replay classified 641/698 primary actions (91.8%) and
deliberately abstained on 57. It accounted all 324 replayed effects: 321 as
controller-state context and 3 as provider payloads, with zero unaccounted
effects.

The paid workflow now enables bounded deterministic context compaction. Exact
semantic duplicate turns are removed first (transport-local tool-call IDs are
ignored, but tool status and action metadata are retained); only older turns
are compacted above the 70%-of-400,000-character envelope trigger, with a 50%
target and the latest two turns preserved. The typed current-state frame is
bounded, no LLM summarizes, unique reasoning is not silently removed, and the
immutable audit history is unchanged. A matched outcome-preservation smoke is
still required before claiming efficiency.

## Why Round 11 was inefficient

- The selected ten tasks stayed at 9/10 while calls rose 420 to 650, actions
  rose 483 to 669, and tokens rose 29.22M to 74.00M.
- Submission suppression could preserve a failed check indefinitely because a
  later pass did not resolve the blocker and engine edit detection depended on
  Git plus `.py` paths.
- The engine repeatedly counted an already-dirty Python file as a new edit and
  missed non-Git and non-Python edits.
- The Round 11 workflow removed six variables, but the installed runner
  recreated a much larger `GT_*` environment inside the model process.
- The `groundtruth` tool, task-obligation echoes, rejection messages, readable
  package, and state artifacts encouraged the model to debug the evaluator.
- The readiness audit exercised a curated Git/Python fixture, not the model's
  actual process, package, tool, and filesystem surface.
- Feature firing and later anchor overlap were treated as efficacy even though
  neither is a randomized counterfactual.

## Implemented architecture

### Host-owned agent

`eval.gt_central_agent:MiniSweCentralAgent` is a Harbor `BaseAgent`, not a
`BaseInstalledAgent`. `setup()` intentionally installs and uploads nothing.
Provider calls execute on the host. Model-selected commands execute in the task
through Harbor with an empty environment mapping.

The model-facing interface is stock Mini-SWE:

- stock system and instance templates;
- stock `LitellmModel`;
- one Bash tool;
- no typed GT tool or model-acknowledgement marker;
- only bounded source-backed facts absent from retained history;
- no task-container GT source, state, package, binary, or provider key.

The runtime emits both a Mini-SWE-compatible trajectory and ATIF-v1.7 on the
host. Private candidate and decision receipts are written separately and are
not included in model context.

### Workspace transitions

The central sensor is non-Git and language-independent. It records path, type,
size, mtime, ctime, symlink target, and hashes for files whose metadata changed.
Ctime detects same-size rewrites even when mtime is restored. Each real action
produces at most one revision transition.

The sensor fails open. More than 50,000 entries, more than 100 changed files,
a scan over two seconds, a malformed manifest, or a hashing failure disables
state-dependent hard decisions for that task.

### Evidence and submission

Failed checks are identified by normalized command and revision. A passing
rerun removes the matching failure immediately, including at the same revision.
An edit makes prior evidence stale. Only a check explicitly present in the task
instruction or a deterministic changed-file syntax failure is grounded enough
to affect submission.

A grounded fresh failure may hold one submit attempt. The next submit at that
same state passes unconditionally. Unrelated failures, stale failures, and
degraded sensing never block.

### Complete 17-feature runtime

`gt_engine.central_runtime.CentralFeatureRuntime` now owns the complete direct
inventory: ten FACT identities (`caller_contract`, `covering_red`,
`def_partition`, `localization`, `newfile_precedent`, `obligations`,
`recovery`, `signature_delta`, `submit_refusal`, and `syntax_result`) plus the
seven CAP_OWNER identities. Each delivery is accepted only when its trigger
boundary, current workspace revision, non-empty feature-specific payload, and
freshness marker pass the payload contract. Treatment receipts are model
visible through bounded grounded decision facts; shadow receipts remain private.

`scripts/central_feature_census.py` forces every trigger independently and
requires all 17 producer/consumer paths to be non-opaque, fresh, correctly
timed, concrete, applied, and context-accounted. It proves provider-free
deliverability; it does not claim every real task will trigger every feature or
that every private engine effect becomes model text. Changed-file syntax
feedback and bounded submission readiness remain the only hard interventions.

## GT-on evaluation implementation

The paid workflow now selects:

- `MiniSweCentralShadowAgent` for GT-on core/shadow;
- `MiniSweCentralAgent` for GT-on treatment;
- `lint`, `submit_readiness`, `all17`, or `integrated` feature mode. `all17`
  is now the default and enables the complete central feature runtime.

The workflow pins Mini-SWE 2.2.8 on the host. It does not install the agent or
GT into the task image.

`gt_engine.experiment` implements deterministic arm assignment, deterministic
eligible-panel selection, five-repeat task-cluster bootstrap analysis, and the
predeclared Pareto gate:

- mean solve count at least 72/89;
- one-sided 95% lower solve-delta bound above zero;
- mean tokens no more than 206,159,394;
- mean actions no more than 3,734.9;
- one-sided 95% upper token/action ratios no more than 0.85;
- no run with more than four errored tasks;
- zero runtime failures and permanently blocked submissions.

## Verification

The focused provider-free battery covers the host boundary, workspace
transitions, lint feedback, pass-clears-failure semantics, bounded submission,
shadow/treatment workflow, ATIF output, deterministic assignment, release gate,
and all17 trigger/payload contracts. The dispatch-only `central_provider_free.yml` repeats those checks with
zero provider calls.

Local receipts:

- 61/61 targeted central/engine/experiment tests passed under Mini-SWE 2.3.0;
- the complete repository suite was attempted after installing the missing
  local `hypothesis` dependency but exceeded the two-minute local command
  budget in unrelated shell-tool tests; targeted regression suites remained
  green;
- structural readiness reported `READY` and changed-file Ruff checks passed;
- the central feature census reported all 17 feature payloads deliverable at
  their declared lifecycle boundaries;
- Harbor 0.20 custom-agent dispatch uses its required `--agent-import-path`
  option, protected by both a workflow assertion and the readiness audit;
- direct agent construction without a runner-injected session ID and a Windows
  CP1252 audit console are covered by fail-safe portability handling;
- a deliberate `>=` to `>` hold-budget mutation made the bounded-submit test
  fail, and restoring the condition returned it to green.

The older inline-engine workflow and tests remain as an explicit
legacy/forensic path; they are not the active paid-evaluation agent.

The provider-free smoke gate was re-executed after implementation: 24/24
focused tests passed under the isolated Mini-SWE 2.2.8 environment, the
structural audit reported `READY`, and Ruff passed. A live canary remains
pending because the local Docker Linux daemon is stopped and no provider
credential is present locally; no provider request was attempted.

The first GitHub ten-task GT-on treatment smoke then completed as run
`30856353817`. All ten verifier results and all ten host receipt bundles were
returned; eight tasks solved and two timed out at Harbor's 900-second agent
limit (`gpt2-codegolf`, `write-compressor`). All ten sensors remained healthy,
no private GT terms appeared in model trajectories, and no repeated submit
hold was recorded. Aggregate usage was 448 model calls, 459 actions, and about
26.1M tokens. This is a runtime/wiring success but a smoke non-regression
failure against the frozen 9/10 reference. Shadow and 89-task runs remain
blocked pending timeout diagnosis.

### All17 smoke after payload proof

After the feature census and payload contract passed, all17 treatment smoke
run `30864114805` was dispatched from commit `b67d213` on the same ten-task
panel. The workflow was cancelled only after
`schemelike-metacircular-eval` remained inside Harbor for roughly 30 minutes;
its partial receipt was retained, but cancellation prevented a completed
trial/verifier result. The partial central receipt shows 90 model calls, 90
actions, `Submitted`, and 1,507 seconds elapsed, so it is censored rather than
counted as solved. The merge returned 9 trials, 8 solved, and one agent timeout
(`gpt2`). Against the frozen 9/10 baseline this is still 8/10 planned (minus
one task): eight unchanged solves, `gpt2` unchanged failed, and `schemelike`
is ungraded. `write-compressor` recovered to a solve in this run.

Every returned or partial task receipt reported `feature_count=17`,
`enabled=true`, a healthy sensor, and zero invalid payloads under
`feature_payload_valid`. The union of features that actually triggered was 11:
obligations (10 tasks), localization and `GT_LOC_RESLOT` (9),
newfile_precedent (8), covering_red (7), syntax_result and `GT_EDIT_CHECK` (6),
def_partition (4), signature_delta and `GT_PATCH_DELTA` (2), and
`GT_CERT_DELIVERY` (9). Six features did not trigger in this panel:
`caller_contract`, `recovery`, `submit_refusal`, `GT_CHANGE_SURFACE`,
`GT_HYPOTHESIS`, and `GT_SS_SUBMIT_RED`. That is correct trigger gating, not a
claim that the producers are broken; the forcing census is the proof for those
paths.

### Trajectory and token-accounting audit

I read every downloaded all17 trajectory and receipt. All ten task directories
had `feature_count=17`, `enabled=true`, healthy sensors, valid payloads, and no
private GT terms in model-visible trajectory text. The treatment did emit
bounded generic guidance; this was visible in every task and occurred 6--21
times per trajectory. That guidance volume is a possible efficiency cost and
must be measured, not assumed harmless.

The frozen `per_task_tokens.json` values sum to 28,682,113 tokens for the ten
tasks. Raw all17 trajectories sum to 19,812,623 tokens only because `gpt2` was
cut off and `schemelike` was cancelled before verifier completion. The eight
unchanged solved tasks alone are 11,407,692 baseline tokens versus 10,054,842
all17 tokens (minus 1,352,850, or 11.9%), but calls increased 261 to 276. This
selected-success subset is descriptive, not an efficiency proof. The earlier
29.22M baseline figure came from a different aggregate artifact, so the token
accounting source itself needs normalization before any release decision.

### Smoke delta against frozen GT-off

| metric | frozen GT-off | GT-on treatment | delta | interpretation |
|---|---:|---:|---:|---|
| solved | 9/10 | 8/10 | -1 task, -10 percentage points | regression |
| timeout/error tasks | 1 | 2 | +1 | regression |
| model calls | 420 | 448 | +28 (+6.7%) | not more efficient |
| actions | 483 | 459 | -24 (-5.0%) | fewer actions, but two tasks did not finish |
| reported tokens | 29.22M | 26.11M | -3.11M (-10.6%) | censored by timeouts; not evidence of savings |

Task-level change was eight unchanged solves, one unchanged `gpt2-codegolf`
failure, and one new `write-compressor` regression. Therefore this smoke does
not show that GT helps. It shows that the host boundary works in Harbor, while
the treatment still needs timeout diagnosis and repeated matched trials.

### Feature status and per-task map

The historical Round 11 table below is retained as a comparator for live
trigger frequency. It is not the implementation proof for the new runtime.
The central feature census now covers all ten FACT producers and seven
CAP_OWNER links. A feature is counted as live only when its trigger is actually
observed in a task. In the historical Round 11 trajectories, the live FACT
coverage was:

| feature | wired/forcing-tested | Round 11 tasks with live evidence |
|---|---|---|
| obligations | yes | all 10 |
| localization | yes | break-filter, headless, llm-inference, modernize-scientific, portfolio, schemelike, write-compressor |
| def_partition | yes | headless, llm-inference, portfolio |
| covering_red | yes | break-filter, headless, llm-inference, portfolio, schemelike, write-compressor |
| syntax_result | yes | none |
| recovery | yes | none |
| signature_delta | yes | none |
| newfile_precedent | yes | none |
| submit_refusal | yes | none |

The CAP_OWNER aliases follow their FACT: `GT_LOC_RESLOT` fired on the seven
localization tasks; `GT_EDIT_CHECK`, `GT_PATCH_DELTA`, `GT_SS_SUBMIT_RED`,
`GT_HYPOTHESIS`, and `GT_CHANGE_SURFACE` fired on none. `GT_CERT_DELIVERY` is
an infrastructure receipt emitted for all ten deliveries, not a task-triggered
fact. `caller_contract` was absent from the retired inline smoke; it is now a
conservative central trigger that requires caller language in a search result.
Thus the old smoke did not exercise all features, while the central producer
census does.

The first smoke was dispatched before the complete feature runtime was wired,
so its per-task map had only lint and submission-readiness candidates. The next
smoke must use `feature=all17`; no result from the earlier run is evidence for
or against the complete 17-feature treatment.

| task | old inline FACTs (Round 11) | central smoke candidates | frozen GT-off → central GT-on |
|---|---|---|---|
| fix-code-vulnerability | obligations | lint, submit readiness | Y → Y |
| portfolio-optimization | obligations, localization, def_partition, covering_red | lint, submit readiness | Y → Y |
| modernize-scientific-stack | obligations, localization | lint, submit readiness | Y → Y |
| headless-terminal | obligations, localization, def_partition, covering_red | lint, submit readiness | Y → Y |
| llm-inference-batching-scheduler | obligations, localization, def_partition, covering_red | lint, submit readiness | Y → Y |
| break-filter-js-from-html | obligations, localization, covering_red | lint, submit readiness | Y → Y |
| write-compressor | obligations, localization, covering_red | lint only; timed out before submit | Y → N |
| gpt2-codegolf | obligations | lint only; timed out before submit | N → N |
| schemelike-metacircular-eval | obligations, localization, covering_red | lint, submit readiness | Y → Y |
| cobol-modernization | obligations | lint, submit readiness | Y → Y |

## Remaining execution gates

1. Run the provider-free workflow at an immutable commit.
2. Run one live GT-on canary task to verify Harbor import, model loop, task
   command execution, receipts, and submission end to end.
3. Run the canonical ten-task GT-on smoke from
   `config/tb2_deepseek_smoke10.json`; do not start the 89-task matrix yet.
4. Require every smoke job to return a verifier result and host trajectory,
   central receipt, and ATIF receipt; require no import/setup/runtime error,
   no private GT surface in the model shell, no permanently blocked submit,
   and no repeated hold at one workspace revision.
5. Review smoke solve parity against the frozen 9/10 reference and inspect
   calls, actions, tokens, sensor health, and intervention receipts. Smoke
   parity is a wiring/non-regression gate, not a superiority claim.
6. Only after the smoke gate passes, replay historical trajectories and run
   GT-on shadow versus single-feature treatment experiments.
7. Integrate only candidates that improve solve, tokens, and actions.
8. Run five full 89-task GT-on repetitions and apply the frozen Pareto gate.

### Follow-up all17 smoke with normalized token and step telemetry

Workflow run `30869649342` was dispatched through GitHub Actions at commit
`65d7ae7` with `feature=all17`, ten planned tasks, parallelism five, and the
same DeepSeek model configuration as the preceding smoke. The schemelike job
was cancelled after approximately 30 minutes because it remained in the known
long-tail loop; the workflow's `always()` merge completed and preserved its
partial artifact. No local Docker run was used.

The returned result was 8/9 solved (88.9% of graded; 8/10 planned). The nine
returned task receipts all report `feature_count=17`, `enabled=true`, and a
healthy workspace sensor. For every returned receipt,
`total_tokens == input_tokens + output_tokens`; this is now an enforced receipt
invariant rather than a value reconstructed from trajectory text. Aggregate
returned telemetry is 12,403,295 tokens, 278 API calls, 297 actions, 278
assistant steps, 601 trajectory messages, and 94 bounded guidance events. The
aggregate is not a ten-task efficiency claim because schemelike is absent and
gpt2 did not solve.

| task | result | GT-on tokens | frozen GT-off tokens | delta | calls | actions | assistant steps | trajectory messages | guidance events |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| break-filter-js-from-html | solved | 811,746 | 166,564 | +645,182 (+387.3%) | 32 | 34 | 32 | 69 | 6 |
| cobol-modernization | solved | 1,890,972 | 1,437,605 | +453,367 (+31.5%) | 53 | 56 | 53 | 112 | 9 |
| fix-code-vulnerability | solved | 352,480 | 451,819 | -99,339 (-22.0%) | 28 | 28 | 28 | 59 | 10 |
| gpt2-codegolf | failed (reward 0) | 2,922,575 | 8,784,582 | -5,862,007 (-66.7%); invalid efficiency comparison because outcome differs | 37 | 37 | 37 | 76 | 15 |
| headless-terminal | solved | 542,160 | 4,921,513 | -4,379,353 (-89.0%) | 28 | 28 | 28 | 59 | 10 |
| llm-inference-batching-scheduler | solved | 3,472,089 | 3,000,980 | +471,109 (+15.7%) | 46 | 47 | 46 | 96 | 22 |
| modernize-scientific-stack | solved | 62,745 | 40,243 | +22,502 (+55.9%) | 9 | 16 | 9 | 28 | 5 |
| portfolio-optimization | solved | 437,916 | 435,035 | +2,881 (+0.7%) | 24 | 28 | 24 | 55 | 6 |
| schemelike-metacircular-eval | censored; no verifier | — | 8,489,839 | — | — | — | — | — | — |
| write-compressor | solved | 1,910,612 | 953,933 | +956,679 (+100.3%) | 21 | 23 | 21 | 47 | 11 |

The frozen per-task values above are the ten-task `per_task_tokens.json`
reference used in the earlier audit (sum 28,682,113). They are the comparison
source, not a new baseline run. The eight unchanged solved tasks total
9,480,720 GT-on tokens versus 11,407,692 frozen GT-off tokens (-1,926,972,
-16.9%) and 241 calls versus 261 (-20, -7.7%). This is descriptive only: the
model is stochastic, the run has one failed task and one censored task, and the
task-level deltas range from -22.0% to +387.3%. It does not prove a release-level
efficiency gain.

#### Payload and timing audit

For each of the nine returned tasks, every receipt marked `DELIVERED` had a
non-empty boundary, `fresh=true`, `model_visible=true`, and a non-null payload;
there were zero invalid delivered receipts. The live trigger union was 11 of 17
IDs. The six IDs with zero task-triggered deliveries were
`caller_contract`, `recovery`, `submit_refusal`, `GT_CHANGE_SURFACE`,
`GT_HYPOTHESIS`, and `GT_SS_SUBMIT_RED`. They remain enabled and forcing-tested;
their absence is explained by missing exact task events, not by a disabled
runtime. `GT_CERT_DELIVERY` is the infrastructure submit receipt and fired on
all returned tasks. No model-visible trajectory contained a `GT_*` private
feature name. Occurrences of `site-packages` were ordinary task source content,
the known discoverable-harness-source limitation, not injected GT text.

#### Steps interpretation and next gates

The new fields distinguish API calls, assistant steps, tool actions, and total
trajectory messages. `assistant_steps == api_calls` for all nine returned
receipts; actions can exceed steps because one model turn can issue multiple
tool actions. Guidance was capped to at most one prioritized advisory per
action, and the observed 94 events were never greater than the 297 actions.

Next steps are:

1. Fix the schemelike long-tail/termination path and rerun the same ten-task
   all17 smoke until all ten have verifier results; do not use the censored
   receipt for efficiency claims.
2. Run matched repeated GT-off and GT-on trials with the same model, seed or
   trial policy, and timeout budget. Compare solved outcome first, then tokens,
   assistant steps, actions, and wall time per solved task.
3. Add event-fixture smoke cases for the six absent triggers so each feature's
   payload, boundary, freshness, and visibility are proven without fabricating
   unrelated live-task events.
4. Profile the high-cost regressions (`break-filter`, `write-compressor`,
   `cobol`, and `llm-inference`) and the 22-guidance llm task. Keep the bounded
   advisory policy only if matched trials show no solve regression and a stable
   token/step benefit.
5. Repeat the ten-task gate across multiple runs before any 89-task workflow;
   the current evidence is not a clean non-regression gate.

### Root cause of the current step/token regressions

The main GT-specific overhead is not the private receipt bookkeeping; it is
model-visible feedback. `_run_lint()` records a `syntax_result` even when lint
passes (`decision="PASS"`), and `model_feedback()` currently considers every
visible receipt regardless of decision. Consequently a successful lint emits
the repair advisory “Repair the syntax or compiler failure on the edited file.”
The follow-up smoke contained 23 PASS syntax receipts, and all 23 were exposed
as model guidance; two additional syntax guidance events came from actual
failures. This is an unconditional false intervention.

The same path exposes non-actionable or repetitive evidence: 20
`newfile_precedent`, 14 `covering_red`, 9 `def_partition`, 8 localization, 8
obligations, and 8 infrastructure `GT_CERT_DELIVERY` guidance events. In total
there were 94 guidance events (11,341 characters). The runtime bounds this to
one advisory per action, but that still appends guidance to the next model
context on every selected action. Since the full conversation is resent on
each API call, repeated guidance has a cumulative input-token cost and can
change the model's search/edit path. This explains why GT-on is more expensive
on break-filter, cobol, llm-inference, modernize, and write-compressor even
though private receipts themselves add no model tokens.

There is also trigger-noise risk: `_FAILURE` treats common words such as
`error`, `failed`, and `red` in command output as failure evidence, while
`_PRECEDENT` treats `existing`, `pattern`, and `registry` as precedent evidence.
Those broad predicates can create additional advisories that are technically
well-formed but not useful to the task. The current data therefore supports a
specific diagnosis: GT is over-intervening, not that the 17-feature wiring is
missing.

The corrective ablation is now clear: (1) keep all receipts host-private;
(2) make `model_feedback()` select only actionable `DELIVERED` failures or
explicit submit holds, never `PASS`, `GT_CERT_DELIVERY`, or repeated facts;
(3) coalesce each advisory by feature and workspace revision; and (4) rerun the
same ten tasks with telemetry. A valid improvement requires fewer assistant
steps/actions and fewer tokens per solved task, with no solve regression. Until
that ablation passes repeated matched trials, the current GT-on treatment should
not be called efficient.

No superiority or efficiency claim is made until those paid GT-on stages pass.

## Efficiency remediation implementation

The remediation is now implemented in the worktree and specified in
`gt_finalstand/GT_EFFICIENCY_REMEDIATION_PLAN.md`. Successful lint and private
lifecycle receipts cannot enter model context; repeated guidance is coalesced
by feature/revision; guidance is limited to four events and 640 characters per
task; and the duplicate direct-lint injection is removed. Receipt-v2 adds
cache-normalized token accounting, action/check/change/failure counters,
lifecycle positions, guidance candidate/suppression counts, and explicit
censoring. The current central paid workflow resolves Harbor's task-owned
`agent.timeout_sec` from exported `task.toml`, passes the exact budget into the
in-process loop, and reserves 15 seconds for a clean return before Harbor's
outer cancellation. It retains the 100-step cap and does not increase any
resource limit. Arm-neutral metrics still read outer exceptions from the trial
result because Harbor can terminate after the last central receipt.

Ordinary feature guidance is now a transient next-decision payload: it is
prepared after a verified tool observation, sent in exactly the next model
request, and not retained in durable conversation history. Receipt-v3 records
the evidence action and delivery call. The provider-free census therefore
checks event chronology, the model decision window, and the full consumer
funnel, emitting `ALL_17_PRODUCERS_PROVEN`, `ALL_17_CONSUMERS_PROVEN`,
`ALL_EFFECTS_TIMING_VALID`, `ALL_PAYLOADS_GROUNDED`, and the terminal
`ALL_17_CONSUMER_PATHS_PROVEN`.

The shared deep-metrics extractor and comparator now reject solve regressions,
censored treatment runs, and any positive primary-resource delta on a
comparable solved task. Provider-free tests and the forced all-17 producer
census pass. This is implementation proof, not efficiency proof; the next gate
is the three-task GitHub treatment canary, followed by repeated ten-task shadow
and treatment runs. The 89-task workflow remains blocked.

## Context-compiler smoke `31061665540`

The replacement context-compiler smoke ran at `a45601f0ba05`. It preserved
verifier outcome 9/10 and passed the request/effect/timing audit, but it failed
the strict efficiency gate because of a new treatment Harbor timeout, step
censoring, per-task Pareto failures, and higher normalized token cost. See
`GT_SMOKE_31061665540_CONTEXT_COMPILER_AUDIT.md`. The 89-task run is still
blocked.

Post-smoke code removes the noisy `edit_target_absent` preflight candidate and
joins outer Harbor censor/wall-time evidence into arm-neutral metrics. Compiler
context totals now include state-frame characters separately from active
guidance. These repairs are provider-free proven; no later paid smoke is
claimed.

## 2026-08-08 authoritative architecture correction

The current engine is conservative and two-sided:

```text
model.query
  -> typed ProposedAction
  -> deterministic preflight (paid arms remain SHADOW)
  -> literal environment execution
  -> postflight + source-bound graph/state update
  -> CertifiedOpportunity gate
  -> exact stock provider request OR one bounded certified delta
  -> next model.query
```

This section supersedes earlier design text that prescribed eager observation
bounding, soft character-trigger compaction, or a single frozen-baseline
treatment comparison.

`gt_engine/uplift_policy.py` is the common active-consequence authority. A
candidate is active only when its evidence is mechanical or certified
structural, current, concrete, absent from provider history, needed for the
present decision, and inside its one-call window. All uncertainty abstains.
Completion auto-submit uses the same certification boundary. Preflight rewrite
and suppression remain disabled; the paid policy is SHADOW.

`ProviderViewSession` preserves the provider-prepared Mini-SWE request exactly
before measured budget pressure. It does not bound a current large observation
or create a soft compaction epoch. During a genuine provider-budget epoch, it
may receipt older tool bodies but preserves all assistant content/reasoning and
the newest successful requested read/search result. Semantic facts are emitted
whole or omitted.

The repository index separates structure from relevance. Raw FTS rank never
certifies relevance. Typed action paths trigger a cached query of the current
graph without rebuilding it, allowing repository intelligence to advance at
the actual Mini-SWE decision boundary while remaining non-predictive. Delivery
requires exact path/symbol anchoring and independent certainty/relevance gates.

Provider receipts expose the complete component delta: stock/final chars and
hashes, guidance chars, graph chars, compaction removal/receipt chars, changed
indices, and reason. Behavioral anchor alignment is measured without markers
but is not causal proof. Causality is evaluated only through the staged OFF,
AUDIT, context-only, controller-only, and full repeated arms.

The release evaluator uses balanced fresh OFF/full crossover trials,
hierarchical task/repeat bootstrap, outcome lower confidence bounds, resource
upper confidence bounds, and failure-capped cost. The latest smoke
`31282615178` remains rejected (8/10; common-solved resources regressed), and
the 89-task run remains blocked.

This implementation is locally provider-free verified: 376 central-engine
tests, the exact 161-test pre-smoke lifecycle set, both census entrypoints, the
real 48-language graph-runtime fixture, readiness, direct/module archived
replay, complete ten-task run diff, Ruff, compilation, workflow YAML parsing,
and diff checks all pass. That does not authorize a smoke: the worktree must
first become the exact clean pushed commit accepted by
`central_pre_smoke_gate.py`. No paid run has been started for this change.
