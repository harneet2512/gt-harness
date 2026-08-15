# GT Central Engine: Complete Diagnosis, Repair Plan, and Proof Protocol

Date: 2026-08-04  
Branch: `inline-engine`  
Investigated commit: `be9ce1c`  
Latest treatment smoke: GitHub Actions run `30887276162`  
89-task run: blocked

## 1. Executive verdict

The current claim that "all 17 GT features work" is false under any operational definition of work.

What is proven today:

- all 17 feature IDs are configured;
- a synthetic fixture can make all 17 IDs emit valid receipts;
- the engine observes the host-owned model/action loop;
- five payloads reached model requests in the latest ten-task smoke;
- those five payloads were inserted before the next model call.

What is not proven:

- that all 17 features have a consumer;
- that all 17 features can change controller state or model behavior;
- that every delivered payload is semantically correct;
- that delivery happens before every already-planned action that it should prevent;
- that GT reduces tokens, calls, actions, validation delay, or failures;
- that the latest treatment is better than GT-off.

The primary root cause is a definition and test-oracle failure introduced by commit `27c2652`. The code changed `all17` from end-to-end feature delivery into producer receipt coverage plus a hardcoded visibility whitelist. The census then encoded that restriction as the expected successful result. The test now passes when 17 producers emit receipts even though only four feature types reach a synthetic delivery window and one eligible feature, `signature_delta`, is silently discarded.

The second root cause is architectural. Most feature IDs have no operational consumer. They produce booleans or generic messages, but do not route context, schedule validation, constrain edits, update an impact graph, interrupt a bad action batch, or gate submission. An observer cannot improve efficiency merely by observing more events.

The third root cause is evidence corruption. The runtime uses a whole-workspace revision for source freshness. Build artifacts, output files, directories, benchmark logs, generated binaries, and background-process writes alter that revision. Validation evidence becomes stale even when source code did not change, and validation debt can fire because a log or data file changed.

The fourth root cause is a split validation classifier. The feature runtime uses explicit task checks, while the ledger and deep-metrics path use the generic `is_check_command()` classifier. In the latest smoke, 14 validation actions recognized by the runtime were invisible to the ledger and metrics. All eight submission certificates reported zero checks and `unverified` readiness.

The fifth root cause is incomplete timing. The agent executes every tool action in an assistant response before calling `model_feedback()`. A control discovered after the first action can arrive only after the remaining actions in that same model response have already executed. The current `not_predictive` field proves only that the payload appears on the next model call. It does not prove that zero pre-decided actions ran after the evidence. The provider-free census uses one action per boundary and therefore cannot expose this late-delivery case.

Confidence in this diagnosis: **high, 0.97**. It is directly supported by the current source, the regression diff, the passing tests, the provider-free census output, and all ten downloaded treatment trajectories and receipts.

## 2. The correct mental model

GT is a host-owned engine. It is not a model-invoked sidecar and it does not require the model to request a GT tool.

The engine owns or wraps this sequence:

1. the model receives the current context;
2. the model selects one or more shell actions;
3. the host executes those actions;
4. GT observes the command, result, and workspace transition;
5. GT may update private state, execute a deterministic control, hold a submission, or add a bounded payload to the next model request.

There are three different meanings of "delivery" that the current implementation mixes together:

1. **Produced receipt:** a feature recognized an event and wrote a payload.
2. **Controller consumption:** an internal engine component used the payload to change state or select an action.
3. **Model delivery:** a payload was inserted into a provider request.

Only controller consumption or model delivery can change a trajectory. A receipt alone cannot.

The system must use this explicit state machine for every feature:

`enabled -> produced -> eligible -> selected -> consumed -> effect_applied -> outcome_observed`

For model-facing effects, insert two more states:

`selected -> prepared -> delivered -> behaviorally_aligned`

`behaviorally_aligned` means the next observable action matches the payload's declared effect predicate. It is not causal proof. Causal proof requires matched repeated treatment and control trials.

## 3. Source-level evidence

### 3.1 The runtime hardcodes five model-actionable FACTs

`gt_engine/central_runtime.py:29` defines:

```python
{"covering_red", "recovery", "signature_delta", "submit_refusal", "syntax_result"}
```

`CentralFeatureRuntime._is_model_actionable()` at line 915 rejects every other feature except the special `GT_EDIT_CHECK` validation-debt payload.

This means 11 feature IDs can never become model-visible under normal receipt production. They may still be useful if an internal controller consumes them, but no such consumer exists for most of them.

### 3.2 The feedback path discards unselected evidence

`CentralFeatureRuntime.model_feedback()` at line 1379 reads all receipts since the last cursor, selects one priority item, advances the cursor, and suppresses the rest. Suppressed actionable evidence is not queued for a later safe decision.

The current provider-free census proves this defect:

| Feature | Produced | Model-visible receipt | Delivered window |
|---|---:|---:|---:|
| caller_contract | 1 | 0 | 0 |
| covering_red | 2 | 2 | 1 |
| def_partition | 1 | 0 | 0 |
| localization | 1 | 0 | 0 |
| newfile_precedent | 2 | 0 | 0 |
| obligations | 1 | 0 | 0 |
| recovery | 1 | 1 | 1 |
| signature_delta | 1 | 1 | 0 |
| submit_refusal | 1 | 1 | 1 |
| syntax_result | 1 | 1 | 1 |
| GT_CERT_DELIVERY | 1 | 0 | 0 |
| GT_CHANGE_SURFACE | 1 | 0 | 0 |
| GT_EDIT_CHECK | 1 | 0 | 0 |
| GT_HYPOTHESIS | 2 | 0 | 0 |
| GT_LOC_RESLOT | 1 | 0 | 0 |
| GT_PATCH_DELTA | 1 | 0 | 0 |
| GT_SS_SUBMIT_RED | 1 | 0 | 0 |

The census prints `ALL_17_DELIVERABLE` even though only four feature types enter a delivery window and `signature_delta` is eligible but never delivered.

### 3.3 The census contains the false success oracle

`scripts/central_feature_census.py:64` explicitly asserts that visibility is valid only for the five whitelisted FACTs. Lines 182 through 203 then call the result `all_17_deliverable`.

The test at `tests/test_gt_central_runtime.py:453` asserts that this misleading result is true. The focused suite currently passes 35 tests, so the tests preserve the defect instead of detecting it.

### 3.4 The producer payloads are mostly not actionable evidence

Examples from `CentralFeatureRuntime.observe_action()`:

- `localization` emits `candidate_locations=True`, but not the paths, line ranges, symbols, or rank that should guide the next step;
- `def_partition` emits `definitions=True` and `references=True`, but not the actual definition and reference anchors;
- `caller_contract` emits `callers_verified=True`, but not verified caller names or locations;
- `obligations` emits `requirements_present=True`, but no parsed obligation ledger;
- `newfile_precedent` is triggered by broad words such as `existing` or `pattern`, not a concrete precedent artifact;
- `signature_delta` recognizes only a narrow `sed -i` before/after substitution, missing ordinary file rewrites, patch application, and scripted edits;
- several CAP rows copy their owner's generic message and behave as aliases rather than separate actuators.

These satisfy a minimum schema. They do not supply the evidence needed to save a search, prevent a wrong edit, select a check, or reject an invalid submission.

### 3.5 Validation classification is inconsistent

`observe_action()` classifies validation with the task's explicit checks. `eval/gt_central_agent.py:583` records ledger evidence only when the generic `is_check_command(command)` returns true. `gt_engine/deep_metrics.py:72` repeats the generic classification.

Latest smoke mismatch:

| Task | Runtime explicit-aware checks | Ledger/deep generic checks | Lost |
|---|---:|---:|---:|
| break-filter-js-from-html | 13 | 11 | 2 |
| cobol-modernization | 0 | 0 | 0 |
| fix-code-vulnerability | 3 | 3 | 0 |
| gpt2-codegolf | 0 | 0 | 0 |
| headless-terminal | 7 | 6 | 1 |
| llm-inference-batching-scheduler | 0 | 0 | 0 |
| modernize-scientific-stack | 0 | 0 | 0 |
| portfolio-optimization | 4 | 0 | 4 |
| schemelike-metacircular-eval | 7 | 0 | 7 |
| write-compressor | 0 | 0 | 0 |

Fourteen real task-declared validation actions disappeared at the ledger boundary.

### 3.6 Submission readiness uses the wrong revision

`EvidenceLedger.submit_decision()` and `readiness_evidence()` require an exact whole-workspace revision match. Any artifact change makes prior check evidence stale.

All eight tasks that reached submission emitted:

- `check_count=0`;
- `passing_checks=0`;
- `failing_checks=0`;
- `readiness=unverified`.

This happened even on tasks where the runtime observed 3, 4, 7, or 13 validation actions. `GT_CERT_DELIVERY` fired as a receipt, but did not certify anything.

### 3.7 Change-surface evidence is polluted

The latest smoke produced 118 `GT_CHANGE_SURFACE` receipts and 118 `GT_PATCH_DELTA` receipts. Many changes were derived artifacts rather than authored source changes:

- `portfolio-optimization`: `benchmark_out.txt`, `.so`, `.o`, and `build/` artifacts;
- `schemelike-metacircular-eval`: `callback-test.txt`;
- `gpt2-codegolf`: `a.out`;
- `write-compressor`: `data.comp`, generated test outputs, and compiled binaries;
- `cobol-modernization`: repeatedly modified data files and directories.

The runtime counts directory changes and any path outside a short cache/VCS exclusion list as material. That is not a source-change model.

### 3.8 Validation debt fired on the wrong paths

The four model-visible `GT_EDIT_CHECK` receipts were triggered at:

| Task | Trigger path | Verdict |
|---|---|---|
| fix-code-vulnerability | `report.jsonl` | required deliverable, but not source code |
| portfolio-optimization | `benchmark_out.txt` | background benchmark output, incorrect trigger |
| schemelike-metacircular-eval | `eval.scm` | source edit, valid trigger class |
| schemelike-metacircular-eval | `callback-test.txt` | generated/test data, incorrect trigger |

Three of four trigger paths violate the documented rule that validation debt follows material source revisions.

The portfolio payload also selected the first parsed check, `python3 setup.py build_ext --inplace`, even though the build had already passed. It arrived while the model was waiting for a running benchmark and the next action was another `sleep` plus `cat`. This payload was timely by call number but stale and irrelevant by lifecycle state.

### 3.9 Batched actions can make guidance late

`eval/gt_central_agent.py:478` executes every action in `actions`. Only after the loop, at line 624, does the agent call `model_feedback(deferred=True)`.

If a model response contains actions A, B, and C, and A produces a syntax failure, B and C are already selected and will execute before GT can speak. Calling the eventual payload `next_model_call_only` does not make it timely. The correct late-action metric is:

`model_decided_actions_executed_after_evidence_before_effect`

For an immediate safety or correctness control, that value must be zero.

## 4. Latest ten-task smoke evidence

Run `30887276162` returned ten verifier rows. Nine had reward 1, `gpt2-codegolf` had reward 0, and `schemelike-metacircular-eval` was censored by the wall-time limit despite a reward artifact. Outcome-first promotion therefore fails.

Aggregate feature evidence:

| Feature | Receipts | Model-visible receipts |
|---|---:|---:|
| covering_red | 1 | 1 |
| def_partition | 10 | 0 |
| GT_CERT_DELIVERY | 8 | 0 |
| GT_CHANGE_SURFACE | 118 | 0 |
| GT_EDIT_CHECK | 21 | 4 |
| GT_HYPOTHESIS | 1 | 0 |
| GT_LOC_RESLOT | 44 | 0 |
| GT_PATCH_DELTA | 118 | 0 |
| localization | 44 | 0 |
| obligations | 10 | 0 |
| syntax_result | 17 | 0 |
| **Total** | **392** | **5** |

Six IDs never fired in the live panel:

- `caller_contract`;
- `newfile_precedent`;
- `recovery`;
- `signature_delta`;
- `submit_refusal`;
- `GT_SS_SUBMIT_RED`.

Their absent events do not prove broken producers. They do prove that this smoke cannot validate those feature paths.

### 4.1 Per-task GT-off to treatment resource deltas

The frozen GT-off token file is `D:\gt_runs\miniswe_tb2_gtoff_20260731\per_task_tokens.json`. Positive resource delta is bad.

| Task | Token delta | Token delta % | Call delta | Guidance | Outcome note |
|---|---:|---:|---:|---:|---|
| break-filter-js-from-html | +65,658 | +39.4% | +4 | 0 | solved |
| cobol-modernization | +1,758,539 | +122.3% | +28 | 0 | solved |
| fix-code-vulnerability | -327,920 | -72.6% | -17 | 1 | solved |
| gpt2-codegolf | +778,944 | +8.9% | -9 | 0 | failed |
| headless-terminal | -3,002,596 | -61.0% | -31 | 0 | solved |
| llm-inference-batching-scheduler | -978,292 | -32.6% | -3 | 0 | solved |
| modernize-scientific-stack | +69,263 | +172.1% | +4 | 0 | solved |
| portfolio-optimization | +54,056 | +12.4% | +6 | 2 | solved |
| schemelike-metacircular-eval | -5,547,008 | -65.3% | -53 | 2 | censored, invalid efficiency win |
| write-compressor | +480,719 | +50.4% | +11 | 0 | solved |

Six tasks have positive token deltas. Five solved tasks have positive token deltas. Seven tasks received no guidance at all, so their deltas cannot be attributed to visible GT payloads. They are stochastic trajectory differences plus any host-loop or measurement differences. They also cannot be credited to GT because GT supplied no trajectory-changing control.

The only cleanly aligned validation-debt event was on `fix-code-vulnerability`: the payload arrived after action 14 and the next model action ran the declared pytest check. That is one useful trace, not proof of causal savings.

`portfolio-optimization` received one real validation-failure advisory and one invalid artifact-driven validation-debt advisory. `schemelike-metacircular-eval` received two validation-debt advisories, but one was artifact-driven and the task still hit the wall-time limit.

## 5. Why GT is not efficient today

GT is currently an expensive measurement layer with a small, inconsistent control layer.

The engine observes many transitions, hashes changed files, lints edits, produces receipts, and records metrics. Most of those observations never alter the trajectory. That can prove instrumentation health, but it cannot reduce model work.

The historical all-visible implementation sent 94 generic, passing, or repeated advisories. That added context and changed stochastic trajectories without enough new evidence. Commit `27c2652` correctly removed that flood, but overcorrected by turning most of the engine into passive telemetry. The new validation-debt exception restored a small control path, but it was built on a corrupted source-revision signal.

This creates two bad modes:

1. **Everything speaks:** token and context overhead, generic repetition, stochastic divergence.
2. **Almost nothing acts:** no mechanism to outperform baseline, despite hundreds of receipts.

The correct architecture is a third mode: all 17 identities feed deterministic internal consumers, while only rare, novel, grounded evidence becomes model-visible.

## 6. Research constraints on the repair

The repair should follow four findings from primary research:

1. SWE-agent shows that agent-computer interface design changes behavior and performance. GT therefore must improve the action/observation interface, not merely add prose. Source: [SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering](https://arxiv.org/abs/2405.15793).
2. Agentless demonstrates that a simple localization, repair, and patch-validation lifecycle can outperform more complex agents at lower cost. GT should make those phases deterministic and explicit, without adding an ideation phase. Source: [Agentless: Demystifying LLM-based Software Engineering Agents](https://arxiv.org/abs/2407.01489).
3. Agentic trajectories diverge early and single-run results vary even at temperature zero. A single temperature-1 comparison cannot prove small efficiency changes. Source: [On Randomness in Agentic Evals](https://arxiv.org/abs/2602.07150).
4. Explainable evaluation requires Thought-Action-Result trajectories and interaction data. Receipts must connect evidence to a specific controller effect and later action, not just state that a feature fired. Source: [Reproducible, Explainable, and Effective Evaluations of Agentic AI for Software Engineering](https://arxiv.org/abs/2604.01437).

The lifecycle used here is deterministic:

`contract -> localization -> change -> static validation -> behavioral validation -> recovery if needed -> certification -> submit`

No ideation phase is added.

## 7. Target operational role for all 17 IDs

The 17 IDs are ten FACT producers and seven CAP actuators. They are not 17 independent prompt messages.

| ID | Current behavior | Required operational role | Model-visible policy |
|---|---|---|---|
| obligations | Boolean receipt at task start | Parse a contract ledger of required outputs, constraints, and declared checks; feed validation and submit certification | Never repeat the task prompt; show only a concrete missing obligation at submit |
| localization | Boolean after non-empty search | Store ranked file, line, and symbol anchors; update the context router | Only when the next edit targets an unanchored location or search has drifted |
| def_partition | Two booleans | Separate actual definition anchors from reference anchors; determine what must be read before edit | Usually private; one compact anchor set if it prevents a wrong edit |
| caller_contract | Boolean on caller words | Store verified callers and call signatures; feed signature-impact validation | Show unresolved caller paths only after a real signature delta |
| newfile_precedent | Broad keyword trigger | Store a concrete sibling/registry precedent and its path; validate new-file placement/registration | Only before an unsupported new-file edit |
| covering_red | Visible validation failure | Create a grounded failure state with command, source revision, diagnostic fingerprint, and attribution | One novel failure payload, then private until evidence changes |
| recovery | Visible after exact repeat | Select a deterministic discriminating next action and block the identical failed action once | One concrete alternate action, never generic "change hypothesis" prose |
| signature_delta | Narrow `sed` detector | Compute symbol/signature changes from before/after source content; schedule caller and targeted check selection | Only unresolved caller impact, with paths/symbols |
| submit_refusal | One-time hold on fresh failing check | Block submit once on a current grounded failure or missing required validation | Direct tool observation from the hold, not an extra advisory |
| syntax_result | Auto-lint receipt; only failures visible | Run bounded host-side syntax checks after attributable source edits | Immediate failure observation; passing result stays private |
| GT_LOC_RESLOT | Alias of localization | Actuator that changes the bounded context slot to the ranked anchors | Internal zero-token effect |
| GT_CHANGE_SURFACE | Receipt for almost every workspace change | Maintain authored-source, task-deliverable, derived-artifact, and unknown change sets | Internal zero-token effect |
| GT_PATCH_DELTA | Duplicates change surface | Actuator that computes changed symbols/paths and selects impacted checks | Internal zero-token effect |
| GT_EDIT_CHECK | Syntax alias plus validation-debt exception | Validation scheduler bound to source revision; auto-run a cheap declared check or interrupt before another edit/submit | Only if the engine cannot safely execute the required check itself |
| GT_HYPOTHESIS | Failure fingerprint receipt | Internal failure-state machine with attempted action, result, and next discriminating predicate | Internal unless recovery needs one concrete alternate action |
| GT_SS_SUBMIT_RED | Alias of submit refusal | Actuator that transitions submit state to blocked, schedules the required check, and records one hold | Direct hold effect, zero extra prompt message |
| GT_CERT_DELIVERY | Always emits unverified receipt | Certify contract obligations and fresh behavioral checks against source revision; allow or block submit | Private certificate on pass; concrete blocker on fail |

Every ID must have both a producer contract and a consumer contract. A private consumer effect counts as operational delivery and costs zero model tokens.

## 8. Detailed implementation plan

### Phase 0: Make the current false proof fail

Files:

- `scripts/central_feature_census.py`
- `tests/test_gt_central_runtime.py`
- `gt_engine/deep_metrics.py`

Changes:

1. Rename `delivered_counts` to `produced_counts`.
2. Rename deep metric `feature_deliveries` to `feature_receipts`.
3. Replace `all_17_deliverable` with separate gates:
   - `all_17_producers_proven`;
   - `all_17_consumers_proven`;
   - `all_effects_timing_valid`;
   - `all_payloads_semantically_grounded`.
4. Add an aggregate `ALL_17_CONSUMER_PATHS_PROVEN` terminal gate.
5. Add a regression test showing that the current implementation fails consumer proof.
6. Add a regression test showing that `signature_delta` is currently eligible but discarded by the one-message selection path.

Acceptance:

- the existing implementation must fail the new consumer gate before feature repair;
- no code may call a producer receipt a delivery;
- no timing gate may pass vacuously on an empty set.

### Phase 1: Split workspace revision from source revision

Files:

- `gt_engine/central_runtime.py`
- `eval/gt_central_agent.py`
- `tests/test_gt_central_runtime.py`
- `tests/test_gt_central_agent.py`

New data structures:

```python
class ChangeOrigin(StrEnum):
    MODEL_AUTHORED = "model_authored"
    VALIDATOR_DERIVED = "validator_derived"
    BACKGROUND_DERIVED = "background_derived"
    TASK_DELIVERABLE = "task_deliverable"
    UNKNOWN = "unknown"

@dataclass(frozen=True)
class ClassifiedChange:
    path: str
    kind: str
    origin: ChangeOrigin
    validation_relevant: bool

@dataclass(frozen=True)
class RevisionState:
    workspace_revision: str
    source_revision: str
    source_epoch: int
```

Rules:

1. Keep the raw workspace revision for auditing.
2. Advance source revision only for regular files attributable to a model-authored change and relevant to the solution source/configuration.
3. Do not advance source revision for directories, caches, compiled objects, binaries, build products, logs, benchmark output, or background-process writes.
4. Track task-required output files separately as deliverables. A required `report.jsonl` can satisfy an obligation without pretending it is a source edit.
5. Validation commands may create artifacts, but those artifacts cannot stale the validation result produced by that same command.
6. `GT_CHANGE_SURFACE` reports all classified change categories. Validation debt and readiness use only validation-relevant source changes.

Regression fixtures from the live run:

- `benchmark_out.txt` does not advance source revision;
- `callback-test.txt` does not advance source revision unless the task contract explicitly identifies it as authored source;
- `a.out`, `.so`, `.o`, `build/`, and directories do not advance source revision;
- `eval.scm`, `bottle.py`, `headless_terminal.py`, and actual source rewrites do advance source revision;
- `report.jsonl` is a task deliverable, not validation-relevant source;
- a validator-created cache or result file cannot invalidate its own pass.

### Phase 2: Use one validation classifier everywhere

Files:

- `gt_engine/central_runtime.py`
- `eval/gt_central_agent.py`
- `gt_engine/deep_metrics.py`
- `tests/test_gt_central_runtime.py`
- `tests/test_gt_central_agent.py`
- `tests/test_gt_deep_metrics.py`

Changes:

1. Classify every executed action once in the agent.
2. Pass the immutable `ValidationClassification` into the feature runtime, evidence ledger, receipt writer, and metrics extractor.
3. Remove independent command reparsing from the ledger and deep metrics.
4. Store these fields:
   - normalized command;
   - command class;
   - declared-check ID;
   - grounded status;
   - failure kind;
   - source revision;
   - workspace revision;
   - result code;
   - diagnostic fingerprint.
5. Bind check freshness to source revision.
6. Keep a validation plan with check roles: build, syntax, focused behavioral, regression, task verifier.
7. Select the highest-priority unsatisfied relevant check. Never blindly select `explicit_checks[0]`.
8. Record passing and failing checks in the certificate ledger.

Acceptance:

- portfolio's four declared validation actions and schemelike's seven declared validation actions enter the ledger;
- all 14 currently lost validation actions are represented in receipt-v3;
- a task with fresh passing checks cannot emit `check_count=0` at submit;
- all current eight zero-check certificates become either truthfully verified or truthfully blocked/unverified for a concrete reason;
- environment failures cannot become product failures.

### Phase 3: Add explicit consumers and effects

Files:

- `gt_engine/central_runtime.py`
- optionally a small new `gt_engine/central_controls.py` if `central_runtime.py` would otherwise exceed the project's readability limit;
- `eval/gt_central_agent.py`

Introduce a consumer registry:

```python
@dataclass(frozen=True)
class FeatureEffect:
    feature_id: str
    receipt_id: str
    effect_kind: str
    effect_action: dict[str, object]
    required_before_action: int | None
    model_visible: bool
```

Effect kinds:

- `CONTRACT_STATE_UPDATE`;
- `CONTEXT_RESLOT`;
- `IMPACT_SET_UPDATE`;
- `VALIDATION_SCHEDULE`;
- `AUTO_VALIDATION`;
- `FAILURE_STATE_TRANSITION`;
- `BATCH_INTERRUPT`;
- `SUBMIT_HOLD`;
- `CERTIFY_PASS`;
- `NO_OP_WITH_REASON`.

Every produced receipt is routed immediately to its registered consumer. Most effects remain internal and cost zero prompt tokens.

Do not send 17 messages. That recreates the 94-advisory regression.

### Phase 4: Fix delivery timing and multi-action batches

Files:

- `eval/gt_central_agent.py`
- `gt_engine/central_runtime.py`
- `tests/test_gt_central_agent.py`

Policy:

1. Execute actions sequentially, as today.
2. After each action, consume all feature effects before executing the next action from the same assistant response.
3. If an effect is marked `must_precede_next_action`, stop executing the remaining pre-decided actions.
4. Return the action result plus the compact control observation to the model.
5. Record every skipped action as `batch_interrupted_before_execution`; do not silently drop it.
6. Passive internal effects do not interrupt the batch.
7. Submit gating remains before marker execution.

Receipt-v3 timing fields:

- `evidence_call`;
- `evidence_action`;
- `effect_selected_at_action`;
- `effect_applied_before_action`;
- `delivered_before_call`;
- `predecided_actions_executed_after_evidence`;
- `predecided_actions_cancelled`;
- `late`;
- `predictive`;
- `expiry_call`.

Timing acceptance:

- immediate controls require `predecided_actions_executed_after_evidence=0`;
- a syntax failure after action A prevents already-planned mutating action B;
- a repeated failure prevents an identical already-planned retry;
- a submit blocker acts before the marker command;
- no payload is delivered before its evidence;
- transient model payloads appear on one call only.

### Phase 5: Replace generic payloads with grounded payloads

Producer requirements:

- localization: actual file, line, symbol, rank, and query;
- def partition: definition anchors and reference anchors;
- caller contract: caller symbols, paths, and verified relationship;
- precedent: concrete precedent path and relation;
- signature delta: symbol, before signature, after signature, changed source revision;
- covering red: exact validator ID, result code, bounded diagnostic, attribution state;
- recovery: prior failed action, repeated fingerprint, selected alternate action and why it discriminates;
- obligations: obligation IDs, required deliverables, declared checks, and satisfaction state;
- certification: obligation status plus source-revision-bound checks.

Generic payloads such as "keep requirements in scope" and "inspect relevant locations" are forbidden. If the engine cannot name the anchor or action, the payload stays private and records `NO_OP_WITH_REASON=insufficient_grounding`.

### Phase 6: Prefer zero-token engine actions

Efficiency comes from replacing model decisions, not adding reminders.

1. Run bounded syntax checks host-side after authored source changes.
2. Auto-run cheap declared focused checks when validation debt matures.
3. For expensive checks, schedule one concrete next action or hold submit, rather than adding recurring prose.
4. Reuse localization and change-surface facts to select targeted checks.
5. Reserve model-visible payloads for information the model does not already have in the tool observation.
6. Keep one external payload per decision, but consume all internal effects.
7. Reserve delivery capacity for high-priority failures so an early low-priority message cannot exhaust the global budget.

Track model actions and engine actions separately. An auto-check may reduce calls and model actions while increasing engine actions; total wall time and compute still remain part of the efficiency gate.

### Phase 7: Build a real all-17 proof suite

Replace the single omnibus fixture with 17 producer/consumer scenarios plus adversarial negatives.

Each scenario must assert:

1. exact triggering event;
2. exact non-triggering near misses;
3. grounded payload fields;
4. source and workspace revision;
5. consumer identity;
6. controller state change or explicit justified no-op;
7. timing boundary;
8. model visibility decision;
9. deduplication/expiry;
10. expected next-action predicate.

Mandatory cross-feature scenarios:

- localization plus `GT_LOC_RESLOT` changes routed context without a model message;
- signature delta plus caller contract plus patch delta schedules caller validation;
- syntax failure interrupts a multi-action batch;
- validation debt ignores background artifacts and auto-runs the relevant check;
- covering red plus hypothesis plus recovery chooses a different discriminating action after an exact repeat;
- submit refusal plus submit-red plus certification performs a one-time hold and then either certifies or fails open according to the bounded policy;
- two actionable facts from one action are both consumed even when only one compact message is rendered;
- absent events remain correct-quiet and are not fabricated.

The final provider-free terminal output must be:

```text
ALL_17_PRODUCERS_PROVEN
ALL_17_CONSUMERS_PROVEN
ALL_EFFECTS_TIMING_VALID
ALL_PAYLOADS_GROUNDED
ALL_17_CONSUMER_PATHS_PROVEN
```

### Phase 8: Repair deep metrics and causal language

Files:

- `gt_engine/deep_metrics.py`
- `scripts/central_deep_metrics.py`
- workflow summary generation
- `tests/test_gt_deep_metrics.py`

Required metrics per task:

**Outcome**

- reward;
- solved;
- censored and exact reason;
- submit certified/blocked/unverified.

**Model resources**

- total, uncached input, cached input, and output tokens;
- normalized cost;
- API calls;
- assistant steps;
- model-selected actions;
- context characters by system/task, assistant, tool, and GT payload.

**Engine resources**

- sensor scans and elapsed time;
- hashes and bytes hashed;
- lint/auto-validation actions;
- engine tool time;
- receipts, effects, external deliveries, and payload characters.

**Lifecycle efficiency**

- actions/calls to first anchored location;
- actions/calls to first relevant edit;
- actions/calls to first focused validation;
- source revisions before first validation;
- failed, repeated, no-op, and reverted actions;
- source churn and derived-artifact churn;
- time from failure to discriminating action;
- time from last source edit to fresh certification;
- pre-decided actions prevented by batch interruption.

**Feature funnel**

- enabled;
- produced;
- eligible;
- consumed;
- effect applied;
- externally delivered;
- behaviorally aligned;
- suppressed with a precise reason.

Do not infer L1 delivery from `model_visible` receipts. Use `guidance_deliveries`. Do not call a later command containing the same file path causal use. Label it `behaviorally_aligned` and reserve causal claims for matched experiments.

### Phase 9: Provider-free replay before another paid smoke

Replay all ten archived trajectories through the repaired policy.

For each task, report:

- old and new source revision changes;
- old and new validation classifications;
- old and new certificates;
- old and new feature funnel;
- old and new external payloads;
- old and new late-action count;
- exact controller action that would change the trajectory.

Required replay outcomes:

- portfolio no longer fires debt from `benchmark_out.txt`;
- schemelike no longer fires debt from `callback-test.txt`;
- fix vulnerability schedules or recognizes the correct pytest validation without relying on `report.jsonl` as source;
- all checks currently lost at the ledger boundary are recovered;
- no historical generic obligation/localization stream returns;
- external payload bytes do not increase without a new grounded control effect.

No paid run is allowed until the replay is reviewed action by action.

### Phase 10: Live proof sequence

The 89-task run remains blocked.

The next paid smoke is allowed only after Phases 0 through 9 pass.

1. Run the ten-task GT-on treatment through GitHub Actions, not local Docker.
2. Require all ten task artifacts and no censoring.
3. Audit every produced, consumed, and delivered feature transition.
4. Reject the run if any payload is false, stale, late, or based on a derived artifact.
5. Compare with the frozen GT-off baseline descriptively.
6. For causal proof, run matched shadow and treatment repetitions with the same model, prompt, limits, images, and concurrency.
7. Use at least three independent repetitions per arm and task-level medians. A single stochastic run cannot prove superiority.

Promotion gates:

- no GT-off solve is lost;
- no treatment task is censored;
- every task that submits has a truthful certification state;
- zero false or late external payloads;
- zero validation-classifier disagreement across runtime, ledger, and metrics;
- zero artifact-driven source revisions;
- external GT context remains below the declared budget;
- for every comparable solved task, treatment median has no positive delta in total tokens, uncached input, calls, model actions, assistant steps, or normalized cost;
- at least one metric is strictly improved per comparable solved task;
- repeated-trial uncertainty does not support a material regression;
- every external delivery has an evidence-linked behavioral-alignment record.

The demand that every individual temperature-1 sample must have a negative resource delta is not scientifically defensible. Early stochastic divergence can make a single trial larger even when the policy is better. The strict usable gate is no positive task-level median across repeated matched trials, no solve loss, no censoring, and uncertainty bounds against material regression.

## 9. Exact remaining TODOs

### P0: Correctness blockers

- [ ] Replace the false `ALL_17_DELIVERABLE` oracle.
- [ ] Add producer-to-consumer state transitions and receipt-v3.
- [ ] Split workspace revision from source revision.
- [ ] Classify regular source, deliverable, derived artifact, directory, and background changes.
- [ ] Use one validation classification object in runtime, ledger, receipt, and metrics.
- [ ] Bind check freshness to source revision.
- [ ] Stop choosing the first declared check blindly.
- [ ] Make submission certificates report real current checks.
- [ ] Add post-action effect consumption before the next action in a model batch.
- [ ] Interrupt remaining pre-decided actions for immediate controls.
- [ ] Preserve all actionable internal effects even when one external message wins arbitration.
- [ ] Replace generic boolean payloads with concrete anchors.

### P1: Complete all 17 operational paths

- [ ] Implement contract-ledger consumption for obligations.
- [ ] Implement anchored context reslot for localization and `GT_LOC_RESLOT`.
- [ ] Implement definition/reference partition consumption.
- [ ] Implement caller-impact consumption.
- [ ] Implement concrete new-file precedent validation.
- [ ] Implement semantic signature delta beyond `sed -i`.
- [ ] Implement classified authored/derived change surface.
- [ ] Implement patch-delta-to-check selection.
- [ ] Implement source-bound validation scheduling and optional auto-check.
- [ ] Implement deterministic failure hypothesis state.
- [ ] Implement discriminating recovery action and repeat prevention.
- [ ] Implement submit-red state transition.
- [ ] Implement truthful certification and one-time submit hold.

### P2: Proof and metrics

- [ ] Add 17 positive producer/consumer scenarios.
- [ ] Add 17 adversarial negative scenarios.
- [ ] Add multi-action timing tests.
- [ ] Add current live artifact regression fixtures.
- [ ] Replace `feature_deliveries` with the full feature funnel.
- [ ] Separate model actions from engine actions.
- [ ] Replace L1/L2/L3 causal wording with delivered, referenced, aligned, and experimentally causal.
- [ ] Add source churn, validation delay, prevention, and engine overhead metrics.
- [ ] Replay all ten archived trajectories and inspect every effect.
- [ ] Update `AGENTS.md`, `CLAUDE.md`, and remediation documentation to the repaired behavioral truth.

### P3: Live validation

- [ ] Run one new ten-task GitHub treatment smoke only after provider-free proof.
- [ ] Audit all ten trajectories and receipt-v3 files manually and programmatically.
- [ ] Require 10/10 uncensored task completion artifacts.
- [ ] Run repeated matched shadow/treatment trials for causal efficiency.
- [ ] Apply task-level outcome-first median and uncertainty gates.
- [ ] Keep the 89-task run blocked until every prior gate passes.

## 10. Implementation order and stopping rules

Implement in this order:

1. false-oracle tests;
2. source revision and change attribution;
3. unified validation classification and certification;
4. consumer/effect registry;
5. batch timing control;
6. grounded payload producers;
7. all-17 proof suite;
8. deep metrics;
9. archived replay;
10. live smoke.

Stop immediately if:

- a proposed feature cannot name its consumer;
- a payload cannot name concrete evidence;
- a controller effect cannot state when it must occur;
- a provider-free test cannot fail without the fix;
- a source revision can be changed by a build log or background output;
- runtime, ledger, and metrics disagree about the same action;
- the repaired census can pass without exercising all consumer callbacks;
- a paid smoke is being used to discover a provider-free bug.

## 11. Definition of done

The repair is done only when all statements below are true:

1. All 17 IDs have a tested producer contract.
2. All 17 IDs have a tested consumer or actuator contract.
3. Every consumer produces an observable internal or external effect.
4. Passive facts improve a later deterministic control without adding prompt tokens.
5. Every external payload is grounded, novel, bounded, and on time.
6. No action that should be prevented executes after the evidence is available.
7. Validation evidence survives irrelevant artifact changes and becomes stale only after relevant source changes.
8. Submission certification reflects current obligations and checks.
9. The provider-free all-17 proof cannot pass on producer receipts alone.
10. Archived trajectories show the intended controls at the intended lifecycle points.
11. The ten-task treatment is uncensored and preserves outcomes.
12. Repeated matched trials show no positive task-level median resource delta on comparable solves and at least one strict efficiency improvement per task.
13. The 89-task run remains blocked until items 1 through 12 are proven.

That is the complete repair target. Anything less is instrumentation theater.
