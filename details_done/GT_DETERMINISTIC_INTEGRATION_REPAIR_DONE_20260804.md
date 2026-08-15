# GT deterministic Mini-SWE integration repair — implementation record

Date: 2026-08-04  
Branch: `inline-engine`  
Implementation state: provider-free integration proof complete; causal efficiency proof pending one fresh paid GT-on smoke  
89-task run: blocked

## 1. Final conclusion

The old implementation did not establish that GT was helping Mini-SWE. It
mostly established that feature producers emitted receipts. Those are not the
same thing. The historical ten-task GT-on run solved 7/10 versus the frozen
GT-off baseline's 9/10, lost two baseline solves, increased uncached input by
271,602 tokens, increased sent context by 13,362,294 characters, and failed the
strict per-task resource gate on four mutually solved tasks.

The repaired implementation now proves, without a provider call, that:

1. each of the 17 feature identities has a real lifecycle trigger;
2. each trigger emits a typed, nonempty payload at its evidence boundary;
3. every payload reaches a registered consumer and mutates operational
   controller state;
4. only novel, grounded, decision-relevant evidence can become model-visible;
5. related facts are coalesced into one bounded observation enrichment;
6. evidence from action N is present in the first provider request after N,
   before `model.query()` starts;
7. no payload is predictive and none is delivered at N+2;
8. already-selected actions in a multi-action response continue unchanged;
9. GT never holds submission, cancels a batch, rejects a command, or replaces
   Mini-SWE's choice; and
10. all ten archived GT-on trajectories replay through the repaired policy.

This is an integration-correctness result, not an efficiency result. Replay
cannot change past model decisions. A fresh paid GT-on smoke is the only valid
next causal test. The existing GT-off baseline must remain frozen.

Confidence:

- integration semantics: high;
- trigger/payload/consumer coverage: high;
- first-request timing and non-prediction: high in deterministic tests;
- live model usefulness: unknown until the fresh smoke;
- negative per-task deltas: unknown until the fresh smoke.

## 2. Correct architecture

GT is a deterministic host-owned engine inside
`eval.gt_central_agent:MiniSweCentralAgent`. It is not a tool that the model
calls, not a sidecar, not a prompt marker protocol, and not a container package.
The model does not request GT.

The lifecycle is:

```text
Mini-SWE chooses action(s)
  -> host executes action N
  -> workspace/validation sensors produce evidence for N
  -> GT routes typed receipts to consumers
  -> consumers update controller state immediately
  -> any novel grounded model-relevant facts are coalesced
  -> the final request for provider call N+1 is enriched
  -> request hash/position/timestamp are recorded
  -> model.query() begins
```

If the model selected multiple actions in one response, the engine executes
them all. There is no new reasoning decision between those actions, so GT does
not cancel them merely to expose new evidence. The evidence enters the next
actual provider decision. This is timely and non-blocking.

## 3. Why the old GT-on arm regressed

### 3.1 Receipts were treated as proof of behavior

All-17 enablement and receipt counts only proved producer reachability. Several
effects had no authoritative state application. The repair adds a typed
`CentralControllerState` and an effect-application ledger with before/after
state hashes, changed sections, evidence action, source/workspace revisions,
and `applied_before_call`.

### 3.2 The intervention changed Mini-SWE control flow

The old control semantics could hold a submit or interrupt the remaining
actions in a model-selected batch. That can add calls, repeat reasoning, lose a
valid solve, and confound attribution. The repair replaces those controls with
non-blocking `SYNTAX_STATE_UPDATE` and `SUBMIT_RISK_UPDATE` effects. Submit and
all preselected actions always execute.

### 3.3 Delivery quality was not grounded tightly enough

Two historical `covering_red` deliveries had empty diagnostics yet were marked
model-visible. Generic advice cannot help and still consumes context. The
repair requires feature-specific anchors before visibility: paths, symbols,
before/after signatures, callers, exact validator commands, diagnostics,
declared checks, or blockers. Empty-diagnostic validation failures remain
private.

### 3.4 Trigger approximations confused words with events

Keyword matches such as “caller,” “pattern,” or “existing” could stand in for
real lifecycle evidence. The repaired triggers require structural witnesses:
definition plus reference anchors for caller contracts, an actual created file
plus a concrete sibling precedent, and source-derived before/after signatures.

### 3.5 Timing was inferred instead of proved at the request boundary

Counting guidance receipts or searching for GT markers does not prove that the
model saw a payload. The repaired agent records the normalized SHA-256 of the
exact final request, enriched message index, evidence action, first eligible
call, delivered call, query-start timestamp, and explicit late and predictive
flags. The request is confirmed before `model.query()` begins.

### 3.6 Context was treated as free

The historical arm reduced aggregate actions but increased aggregate context
characters by 13.36M and uncached input by 271.6K. This is why aggregate tokens
alone gave a misleading picture. The repair adds GT-specific context and
delivery metrics and restricts visible output to bounded, coalesced evidence.
Most feature effects remain private and cost zero prompt tokens.

### 3.7 Workspace noise could invalidate real evidence

Whole-workspace revisions treated caches, binaries, logs, benchmarks, build
products, and background writes like authored source. The repair maintains
separate workspace and source revisions. Only validation-relevant authored
source changes stale checks.

## 4. All 17 features: trigger, payload, consumer, timing

“Visible” below means eligible for a bounded next-request fact when its full
grounding contract is satisfied. “Private” means it still changes controller
state but adds no model tokens.

| Feature | Real trigger | Required concrete payload/evidence | Applied controller consequence | Visibility and time |
|---|---|---|---|---|
| `obligations` | task start | parsed obligations, IDs, declared checks | initializes contract ledger | private at action 0 |
| `localization` | real search result | ranked file/line/symbol anchors | updates localization state | private after search |
| `def_partition` | search result containing both definitions and references | separate definition and reference anchors | updates impact set | private after search |
| `caller_contract` | structural definition plus non-definition references | verified caller paths/symbols/signatures | updates impact/caller state and may contribute to a signature fact | private alone; coalesced after edit when relevant |
| `newfile_precedent` | an actual file creation | created path plus concrete repository precedent path | records placement/registration precedent | visible in first call after edit when grounded |
| `covering_red` | recognized or declared validation fails on current source | exact command, nonempty diagnostic, attribution, failure kind | creates current failure state | visible in first call after failing check |
| `recovery` | identical grounded failure repeats without source change | repeat count plus one discriminating alternate action and paths | advances deterministic recovery state | visible in first call after repeat |
| `signature_delta` | source-content AST/signature changes | symbol, before signature, after signature, changed paths, callers if known | schedules caller/targeted validation | visible in first call after edit |
| `submit_refusal` | current source acquires grounded failing submission evidence | exact blockers and source-bound risk | records submission risk; never refuses | visible in first call after failure, not delayed until submit |
| `syntax_result` | host syntax check after authored edit | path, exact command, return code, diagnostic on failure | updates validation result state | failures visible in first call after edit; passes private |
| `GT_CERT_DELIVERY` | current validation state changes or submit occurs | sensor health, source-bound check counts, readiness | updates certificate state | private; submit remains allowed |
| `GT_CHANGE_SURFACE` | actual workspace transition after an edit | created/modified/deleted paths classified by origin | updates authored/derived/deliverable/unknown surface | private after edit |
| `GT_EDIT_CHECK` | authored source edit | changed paths and deterministic check schedule | updates validation plan; tracks real validation debt | private initially; visible only after three unvalidated authored edits |
| `GT_HYPOTHESIS` | grounded validation failure | attempted command, failure fingerprint, deterministic next predicate | updates failure hypothesis state | private, may contribute to recovery state |
| `GT_LOC_RESLOT` | search result where ranking discards noisy anchors | selected anchors plus discarded count | replaces bounded localization slot | visible only when reslot materially removes noise |
| `GT_PATCH_DELTA` | authored source edit | changed symbols/paths and impacted checks | updates patch-delta validation plan | private; may contribute to signature fact |
| `GT_SS_SUBMIT_RED` | grounded current-source failing check | concrete blocker list and source revision | latches red submission-risk state | private; never blocks submit |

The six features previously described as “absent” did not need fabricated
events. They needed correct event contracts:

- `caller_contract`: now structural, not keyword-based;
- `recovery`: fires only on the same failure fingerprint at the same source
  revision;
- `submit_refusal`: retains the historical ID but now records non-blocking risk
  at the failure event;
- `GT_CHANGE_SURFACE`: fires on a real transition;
- `GT_HYPOTHESIS`: fires on a grounded failing validation;
- `GT_SS_SUBMIT_RED`: latches the same real source-bound risk privately.

## 5. Payload construction and anti-spam rules

The model-visible text is rendered from typed fields, not a generic `message`:

- syntax: changed path + host check command + return code/diagnostic;
- red validation: validator command + bounded diagnostic + attribution;
- repeat recovery: repeat count + changed source path + discriminator;
- signature: symbol + before/after signatures + affected callers;
- precedent: created file + actual sibling precedent;
- reslot: highest-ranked source anchors only;
- submission risk: exact failing required check;
- validation debt: changed authored paths + declared check.

Related features contribute provenance to one payload rather than emitting
duplicate text. At most three concrete facts are coalesced into the observation
enrichment, with a 320-character default budget. Passing checks, generic task
obligations, certificates, search echoes, unchanged state, empty diagnostics,
and ungrounded CAP receipts stay private.

The payload is injected into the last ordinary tool observation in a copied
request message list. It is not appended permanently to the durable trajectory,
so subsequent calls do not repeatedly pay for the same GT block.

## 6. New deep metrics and gates

The receipt/extractor now records more than tokens:

- stock context characters and GT-added context characters;
- effects produced, effects applied, and state mutations;
- payload deliveries and timely delivery count;
- late and predictive deliveries;
- first-eligible delivery rate;
- actions already selected after evidence;
- submission risks versus submission holds;
- batch interruptions and interrupted actions;
- input/output/cache/uncached tokens;
- calls, assistant steps, actions, failures, repeats, no-action steps;
- search/read/edit/check/submit milestones;
- wasted-action proxy, context characters, model-output characters;
- solve/censor status and normalized frozen-price cost.

`compare_arms` keeps solve preservation, censoring, and strict per-task Pareto
as hard gates. Its Markdown now includes a second per-task table for uncached
input, context, failed/wasted actions, submit timing, GT context, and
timely/late/predictive deliveries. `central_deep_metrics.py compare` accepts the
frozen baseline and treatment directly; a shadow arm is optional. Archive
extraction now finds nested Harbor trajectories, adjacent receipts, and reward
files without rerunning the baseline.

## 7. Frozen historical per-task delta

Delta is historical GT-on run `30928910763` minus the frozen local GT-off
baseline. Positive resource delta is bad. This table diagnoses the old run; it
does not estimate the repaired run.

| Task | Solve | Total tokens | Calls | Actions | Steps | Uncached input | Context chars | Strict Pareto |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| break-filter-js-from-html | 1→1 | +106,048 | +7 | +3 | +7 | -1,656 | +42,261 | fail |
| cobol-modernization | 1→1 | -539,139 | -7 | -25 | -7 | -9,248 | -619,206 | pass |
| fix-code-vulnerability | 1→1 | -125,603 | -8 | -8 | -8 | +1,217 | -214,211 | pass |
| gpt2-codegolf | 0→0 | +8,312,612 | -15 | -16 | -16 | +336,528 | +24,584,885 | not outcome-comparable |
| headless-terminal | 1→0 | -3,671,614 | -46 | -46 | -46 | -26,004 | -5,573,339 | solve regression |
| llm-inference-batching-scheduler | 1→1 | -292,877 | +5 | +6 | +5 | -19,630 | -1,010,583 | fail |
| modernize-scientific-stack | 1→1 | +30,716 | +3 | +2 | +3 | +873 | +66,750 | fail |
| portfolio-optimization | 1→1 | +79,856 | -3 | -7 | -3 | +872 | -7,210 | fail |
| schemelike-metacircular-eval | 1→1 | -5,642,939 | -55 | -80 | -55 | -4,754 | -3,658,161 | pass, but treatment censored |
| write-compressor | 1→0 | -1,038,951 | -14 | -16 | -15 | -6,596 | -248,892 | solve regression/timeout |

Aggregate historical delta:

| Metric | GT-off | old GT-on | Delta |
|---|---:|---:|---:|
| solved | 9 | 7 | -2 |
| total tokens | 29,223,016 | 26,441,125 | -2,781,891 |
| uncached input | 354,433 | 626,035 | +271,602 |
| API calls | 420 | 287 | -133 |
| assistant steps | 420 | 285 | -135 |
| actions | 483 | 296 | -187 |
| failed actions | 38 | 23 | -15 |
| wasted-action proxy | 41 | 24 | -17 |
| context characters sent | 30,874,834 | 44,237,128 | +13,362,294 |
| model-output characters | 1,412,486 | 973,556 | -438,930 |
| normalized cost | $0.280391 | $0.265428 | -$0.014963 |

The aggregate reductions do not rescue the run. Outcome preservation comes
first. Among baseline-solved tasks that treatment also solved, every primary
resource must be non-positive and at least one must be negative per task. Four
tasks failed that requirement, and two baseline solves were lost.

## 8. Verification evidence

### Provider-free pre-smoke gate

Command:

```powershell
.\.venv\Scripts\python.exe scripts\central_pre_smoke_gate.py
```

Terminal proof:

```text
ALL_17_PRODUCERS_PROVEN
ALL_17_CONSUMERS_PROVEN
ALL_EFFECTS_TIMING_VALID
ALL_PAYLOADS_GROUNDED
ALL_17_CONSUMER_PATHS_PROVEN
ALL_17_TRIGGERS_PROVEN
ALL_17_PAYLOADS_CONCRETE
ALL_17_CONSUMERS_APPLIED
ALL_VISIBLE_PAYLOADS_IN_FIRST_ELIGIBLE_REQUEST
NO_ACTIONS_BLOCKED
READY
SMOKE_APPROVED
```

### Focused and full tests

- 104 focused GT/runtime/replay/deep-metrics tests passed.
- Full repository suite: 868 collected, 865 passed, 3 platform/coverage skips.
- Ruff on every changed Python file: passed.
- `git diff --check`: passed (only Git's Windows line-ending notices).

The tests include exact request-hash equality, first-eligible-call equality,
pre-query delivery timestamp, non-prediction, non-blocking submit, no
multi-action cancellation, empty-diagnostic privacy, semantic signature
extraction, all-17 state mutations, and archive syntax-evidence reconstruction.

### Archived replay

All ten archived tasks pass the repaired deterministic replay. The replay
reconstructs both model-selected actions and GT's host-side syntax checks,
because the latter exist in central receipts but not Mini-SWE trajectories.
It reports zero submit holds, zero batch interruptions, zero interrupted
actions, zero artifact-driven validation-debt triggers, and grounded visible
payloads only.

The repaired policy would emit two grounded model payloads on the same archived
evidence instead of the old five deliveries. This is evidence that spam and
ungrounded deliveries were removed, not evidence of a future solve score.

## 9. Research basis and what was missing from the SWE lifecycle

The implementation deliberately uses a deterministic
contract→localize→edit→validate→recover→certify lifecycle; it does not add an
“ideate” phase.

- SWE-agent shows that the agent-computer interface itself changes software
  agent performance and emphasizes repository navigation, editing, and test
  execution. GT therefore belongs in the host interaction loop, not behind a
  model-invoked sidecar: <https://arxiv.org/abs/2405.15793>.
- Agentless demonstrates a simple localization, repair, and patch-validation
  pipeline and reports strong performance at low cost. This supports explicit
  lifecycle state and selective deterministic help instead of constant
  advisory generation: <https://arxiv.org/abs/2407.01489>.
- SWE-bench evaluates the final repository using fail-to-pass tests. Receipts,
  marker visibility, or plausible reasoning are not outcomes; solve
  preservation and fresh validation must remain the first gate:
  <https://www.swebench.com/original.html>.
- Lost in the Middle shows that more context can reduce effective use of
  relevant information and that position matters. This supports bounded
  coalescing at the most recent observation immediately before the next model
  decision: <https://arxiv.org/abs/2307.03172>.

The missing lifecycle elements were not more “features.” They were contracts
between stages: authoritative state application, source-bound validation,
failure fingerprinting, recovery after observed repetition, patch/caller
impact, non-blocking submission risk, certificate truth, and exact request
timing.

## 10. Remaining TODOs

### Required before the 89-task run

1. Commit and push only the intended tracked repair and this report; do not add
   unrelated untracked user artifacts.
2. Dispatch one fresh paid GT-on ten-task smoke at the exact approved commit.
   Do not rerun GT-off.
3. Audit every task receipt against these hard invariants:
   - 17 enabled and healthy;
   - triggered features have concrete payloads;
   - every effect is applied;
   - visible facts have an exact request hash and first-eligible call;
   - late=0 and predictive=0;
   - holds/interruptions/cancellations=0;
   - GT-added context is bounded and attributable;
   - no empty diagnostics or generic guidance.
4. Compare the new GT-on arm to
   `C:\Users\Lenovo\Downloads\deep_metrics_baseline.json` and the frozen reward
   file. Reject on any lost solve or censored treatment task.
5. Require strict per-task Pareto improvement on every mutually solved task for
   total tokens, calls, actions, assistant steps, and normalized cost. Review
   the deep diagnostic table even if that primary gate passes.
6. Repeat matched GT-on trials to distinguish a real effect from model/run
   variance. One smoke can approve mechanics and reject a regression; it cannot
   establish stable causal efficiency.
7. Keep the 89-task run blocked until outcome preservation and repeated matched
   efficiency evidence pass.

### Not required before the ten-task smoke

- More feature identities.
- Fabricating absent lifecycle events so all 17 fire on every task.
- GT markers in model messages.
- A model-callable GT tool.
- Rerunning the frozen baseline.
- Any GCP authentication or project change.

## 11. Reproduction commands

```powershell
# Strict provider-free approval
.\.venv\Scripts\python.exe scripts\central_pre_smoke_gate.py

# Repaired replay of the previous paid trajectories
.\.venv\Scripts\python.exe scripts\central_replay.py `
  D:\tmp\gt-smoke-30928910763-tasks `
  --json D:\tmp\gt-central-replay-repaired.json

# Extract historical treatment without rerunning either arm
.\.venv\Scripts\python.exe scripts\central_deep_metrics.py extract `
  --name historical-gt-on-30928910763 `
  --input D:\tmp\gt-smoke-30928910763-tasks `
  --output D:\tmp\deep_metrics_treatment_30928910763.json

# Compare directly to the frozen baseline (shadow is optional)
.\.venv\Scripts\python.exe scripts\central_deep_metrics.py compare `
  --baseline C:\Users\Lenovo\Downloads\deep_metrics_baseline.json `
  --treatment D:\tmp\deep_metrics_treatment_30928910763.json `
  --output-dir D:\tmp\deep_delta_30928910763

# Focused proof
.\.venv\Scripts\python.exe -m pytest `
  tests\test_gt_central_agent.py `
  tests\test_gt_central_runtime.py `
  tests\test_gt_central_consumer_proof.py `
  tests\test_gt_deep_metrics.py `
  tests\test_central_replay.py -q

# Full repository proof
.\.venv\Scripts\python.exe -m pytest -q
```

## 12. Stop state

`IMPLEMENTED_UNVERIFIED`

The code and provider-free integration contract are verified. The user-facing
claim “GT makes Mini-SWE more efficient while preserving outcomes” is not yet
verified and must not be made until a fresh paid GT-on run passes the frozen
baseline gates above.

## 13. Fresh paid smoke result — run 30942313482

The approved GitHub workflow ran on commit `cf89bf7` with `arm=treatment`,
`feature=all17`, and the ten-task smoke set. The workflow and merge jobs
completed successfully. The verifier solved 9/10 tasks, matching the frozen
GT-off solve count of 9/10; no baseline task was rerun.

The strict outcome/efficiency gate remains **FAIL**:

- censored treatment tasks: `llm-inference-batching-scheduler` and
  `schemelike-metacircular-eval` (`WallTimeExceeded`);
- solve regressions: none;
- strict per-task Pareto failures: `break-filter-js-from-html`,
  `cobol-modernization`, `modernize-scientific-stack`,
  `portfolio-optimization`, and `write-compressor`;
- aggregate total-token delta: `-14,793,375`;
- aggregate uncached-input delta: `-142,664`;
- aggregate context-character delta: `-20,579,668`;
- aggregate action delta: `-129`.

Those aggregate reductions do not pass the gate because five mutually solved
tasks still regress on at least one primary resource, and two tasks are
censored. The model-visible payload stream was small and grounded, but this
run does not prove that the payloads caused the resource reductions.

Live receipt audit:

- all ten tasks reported all 17 feature IDs enabled;
- triggered feature sets ranged from 4 to 12 per task, depending on the real
  lifecycle events present;
- every produced feature had a corresponding effect application and changed
  controller state;
- 0 ungrounded model-visible payloads;
- 0 late payloads;
- 0 predictive payloads;
- 0 submit holds;
- 0 batch interruptions;
- 0 interrupted actions;
- 0 artifact-driven validation-debt triggers in replay;
- archived replay of this new run: `REPLAY_OK` for all ten tasks.

This is the expected distinction: “all 17 work” means all 17 are implemented
and fire when their evidence exists, not that every task fabricates every event.
The live run confirms correct delivery for triggered features, but it rejects
the stronger claim that GT is already a reliable efficiency win.
