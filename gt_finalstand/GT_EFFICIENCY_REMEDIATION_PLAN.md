# GT efficiency remediation and proof plan

Status: implemented locally through the provider-free gate; GitHub canary and
repeated live proof remain pending. Confidence in the code-level root cause is
high. Confidence that the treatment now beats GT-off is unknown until the
live paired gates pass.

## 1. Strongest conclusion

The previous all-17 treatment was not an efficiency mechanism. It was an
over-intervention mechanism. Run `30869649342` sent 94 advisories (11,341
characters) into model context. Twenty-three of those advisories told the model
to repair syntax immediately after syntax had passed. Non-actionable task
obligations, localization receipts, precedent receipts, and certification
receipts were also surfaced. Because every later API request resends the full
conversation, each unnecessary advisory creates cumulative input-token cost
and can redirect the search/edit trajectory. This is sufficient to explain the
large positive per-task deltas without blaming private bookkeeping.

The old aggregate comparison also understated failure. A task that stops early
or is cancelled can use fewer tokens while producing no solution. Resource
reduction is admissible only after outcome preservation; censored tasks are
automatic gate failures.

## 2. Deterministic SWE lifecycle (no ideation stage)

The implementation follows a deterministic evidence pipeline:

`TASK_STARTED -> CONTRACT_CAPTURED -> BEHAVIOR_OBSERVED -> LOCATION_ANCHORED
-> IMPACT_CAPTURED -> WORKSPACE_EDITED -> STATIC_VALIDATED ->
FOCUSED_CHECK_VALIDATED -> REGRESSION_VALIDATED -> CHANGE_SURFACE_CERTIFIED ->
SUBMIT_READY`

This choice matches the strongest relevant agent research:

- [Agentless](https://arxiv.org/abs/2407.01489) decomposes repair into
  hierarchical localization, patch generation, and validation without an
  autonomous planning/ideation layer.
- [AutoCodeRover](https://arxiv.org/abs/2404.05427) uses program structure and
  test evidence to make localization more precise.
- [SWE-agent](https://arxiv.org/abs/2405.15793) attributes material performance
  to a constrained agent-computer interface; its
  [official ACI documentation](https://github.com/SWE-agent/SWE-agent/blob/main/docs/background/aci.md)
  emphasizes concise search output and automatic lint feedback at edit time.
- [SWE-Edit](https://arxiv.org/abs/2604.26102) reports that editing performance
  and cost depend on how context is coupled to the model. That supports keeping
  lifecycle facts private unless they change the next action.

These sources do not prove this GT implementation is efficient. They justify
the lifecycle and context-minimization hypotheses that the experiment must
test.

## 3. Feature changes

| Feature | Revised trigger/payload | Visibility |
|---|---|---|
| obligations | non-empty task contract at task start | private |
| localization / GT_LOC_RESLOT | non-empty search evidence at the search boundary | private |
| def_partition | definition/reference evidence in search output | private |
| caller_contract | caller/reference evidence from search, not a generic edit | private |
| newfile_precedent | a new file only after precedent evidence was observed | private |
| GT_CHANGE_SURFACE | every real workspace revision change with created/modified/deleted paths | private |
| GT_PATCH_DELTA | every non-empty changed-path surface | private |
| signature_delta | signature-shaped edit plus a real changed path | visible only when actionable |
| syntax_result / GT_EDIT_CHECK | pass and fail both receipted; only a concrete failure is visible | failed FACT only |
| covering_red | non-zero recognized check, labelled reproduction or post-edit | visible, deduplicated |
| recovery | same normalized failure fingerprint repeats | visible, deduplicated |
| GT_HYPOTHESIS | deterministic failure-state transition ID; never model ideation | private |
| submit_refusal / GT_SS_SUBMIT_RED | current-revision grounded failure at submit; one hold maximum | submit message only |
| GT_CERT_DELIVERY | current check counts and `validated`, `blocked`, or `unverified`; never implies success without evidence | private |

The provider-facing allowlist is now only failed `syntax_result`,
`covering_red`, deterministic `recovery`, anchored `signature_delta`, and
`submit_refusal`. A successful lint, a CAP receipt, or a non-actionable FACT
cannot become guidance.

Deduplication is by feature and workspace revision. Treatment is capped at four
guidance events and 640 guidance characters per task, with at most one advisory
per action. Ordinary guidance says `Runtime evidence:` and does not tell the
model to resubmit. Only a real submit hold carries resubmit language. The
separate lint injection that previously duplicated syntax guidance has been
removed.

Ordinary guidance is transient. It is prepared only after the triggering tool
observation, included in exactly the next model request, and then omitted from
durable conversation history. Receipt-v2 records the evidence action, the call
after which it was prepared, and the call before which it was delivered. This
prevents prediction (delivery before evidence), late delivery (after the next
decision), and cumulative re-sending on unrelated later decisions. Competing
same-turn candidates are prioritized once and suppressed; they are not queued
for stale delivery.

## 4. Deep metrics and causal gates

The same trajectory extractor is used for GT-off, shadow, and treatment. It
records:

- outcome, exit status, and censoring;
- input, output, total, cache-hit, and uncached-input tokens;
- provider and normalized cost;
- API calls, assistant steps, tool actions, and no-action responses;
- search, read, edit, check, submit, and other command counts;
- successful/failed actions, exact repeated commands, and a wasted-action
  proxy;
- steps to first search/read/edit/check/submit;
- context and model-output characters;
- guidance delivered/candidates/suppressed and L1/L2/L3 causal ladder counts
  when a central receipt is present;
- lifecycle boundaries from receipt-v2.

The comparator writes `deep_metrics_baseline.json`,
`deep_metrics_shadow.json`, `deep_metrics_treatment.json`, `deep_delta.json`,
and `DEEP_DELTA.md`. Every delta is `later arm - earlier arm`; a positive
resource delta is bad.

The strict per-task Pareto gate applies only where both arms solved the task:
no positive delta in total tokens, API calls, tool actions, assistant steps, or
normalized cost, and at least one strict improvement. Any baseline solve lost
by treatment fails the experiment. Any treatment censoring fails the
experiment. Lower resource use caused by failure never passes.

## 5. Termination and receipt integrity

The workflow now fixes the budgets at 100 assistant calls, 900 seconds for the
model loop, 300 seconds per model request, and the existing per-command limit.
Step, cost, wall, and request limits receive distinct exit statuses. The agent
always writes a receipt-v2 partial trajectory with `censored=true` and a reason.
This directly addresses the schemelike long-tail problem without mislabelling a
cut-off run as an efficiency win.

## 6. Execution sequence

1. Provider-free unit/integration tests and the forced all-17 census must pass.
2. Run a three-task GitHub treatment canary on
   `break-filter-js-from-html`, `write-compressor`, and
   `llm-inference-batching-scheduler`, the clearest prior regressions.
3. Require three verifier results, receipt-v2, healthy sensors, valid payload
   boundaries, no duplicate advisory, no private feature-name leak, and no
   censoring.
4. Run all ten tasks in shadow. This measures host-only observation overhead
   without model-visible guidance.
5. Run all ten tasks in treatment.
6. Compare frozen GT-off to shadow, shadow to treatment, and frozen GT-off to
   treatment with the shared extractor.
7. Repeat shadow and treatment three times and use task-level medians. Report
   all repetitions; do not discard bad or censored trials.
8. Unblock the 89-task workflow only if all expected artifacts exist, no solve
   regresses, no treatment task is censored, the strict per-task Pareto gate
   passes for every comparable solve, and guidance reaches L2/L3 often enough
   to demonstrate behavioral use rather than receipt activity alone.

The 89-task run remains blocked. The next authorized live action is the
three-task GitHub treatment canary, not a local Docker run and not a full
matrix.

## 7. Implemented files

- `gt_engine/central_runtime.py`: feature boundaries, private/visible policy,
  deduplication, budgets, lifecycle and action counters.
- `eval/gt_central_agent.py`: one-advisory path, receipt-v2 metrics, request and
  wall timeouts, censored partial receipts.
- `gt_engine/deep_metrics.py` and `scripts/central_deep_metrics.py`: shared
  extraction and strict comparison.
- `.github/workflows/tb2_miniswe_central.yml`: explicit budgets, deep metric
  artifact, expanded merge telemetry.
- `tests/test_gt_central_runtime.py`, `tests/test_gt_central_agent.py`, and
  `tests/test_gt_deep_metrics.py`: boundary, deduplication, timeout, metrics,
  censoring, and Pareto proofs.

The provider-free census reports the five gates
`ALL_17_PRODUCERS_PROVEN`, `ALL_17_CONSUMERS_PROVEN`,
`ALL_EFFECTS_TIMING_VALID`, `ALL_PAYLOADS_GROUNDED`, and
`ALL_17_CONSUMER_PATHS_PROVEN`; the terminal gate cannot pass on producer
receipts alone.

## 8. Live canary status

Implementation commit `27c2652` and preflight-bound commit `eb0aaf2` were
pushed to `inline-engine`. GitHub run `30875492432` was cancelled while still
in provider preflight after the request exceeded the agent's 120-second budget;
zero task jobs started. The workflow was then fixed to terminate preflight as a
process after 150 seconds, rather than leaving an HTTP worker alive.

The bounded retry, GitHub run `30875688484`, ended at that 150-second deadline
with exit code 124. Again, dataset enumeration and all task jobs were skipped.
This proves the preflight termination control works. It provides no benchmark,
trajectory, feature-timing, or delta evidence because the provider returned no
response and no task container ran.

Remaining TODOs:

1. Re-run the same three-task treatment canary with the corrected 300-second
   per-request bound.
2. Require all three results to be uncensored before interpreting a delta.
3. Audit receipt-v2, transient decision windows, feature payload/boundary timing,
   and per-task deep metrics.
4. Only after that gate passes, run the ten-task shadow and treatment arms.
5. Repeat each arm three times, compute task-level medians, and apply the strict
   outcome-first Pareto gate.
6. Keep the 89-task workflow blocked until every prior gate passes.

## 9. Canary 30876371075 audit and correction

GitHub run `30876371075` completed its three-task treatment canary. The
central runtime was healthy and enabled all 17 features in each task. Its live
timing proof is valid: the only model-visible advisory, `covering_red` in
`llm-inference-batching-scheduler`, was derived from action 15 after model call
14 and inserted only before model call 15. It was not predictive, late, or
persisted into later calls. No private receipt became model-visible.

The run is not an efficiency pass. `break-filter-js-from-html` solved but used
211,289 tokens (+14.09%), 20 actions (+25.00%), and 15 assistant steps
(+25.00%) versus the frozen GT-off result. It carried zero model-visible GT
guidance, so this single temperature-1.0 sample is outcome variance rather than
evidence that a late GT payload caused the regression.
`llm-inference-batching-scheduler` solved with 1,795,038 tokens (-42.06%), 38
actions (-9.52%), and 37 assistant steps (-9.76%). `write-compressor` was
censored after its second model request exceeded the workflow's 120-second
per-request bound; it cannot be compared against its GT-off baseline.

The canary exposed a harness configuration defect, not a central-runtime
boundary defect. The workflow now uses `model_timeout_sec=300`, still below the
900-second model-loop deadline, and a unit assertion pins both bounds. The next
three-task canary must be run from that commit. This run blocks the ten-task and
89-task stages.

## 10. Context and payload remediation

The corrected three-task canary `30877236786` removed censorship and solved
3/3, but it exposed a false-positive `covering_red` payload: a heredoc Python
experiment containing `Test 1` was detected as a test and its `python3: command
not found` result (127) was called an attributable regression. This is a
general command-classification/provenance defect and blocks the ten-task run.
The detailed, non-overfitting remediation and experimental protocol is in
`gt_finalstand/GT_CONTEXT_EFFICIENCY_REMEDIATION_PLAN.md`.

## 11. Active-engine correction after smoke 30882949319

The completed ten-task treatment smoke was first misread as “no GT delivery”
because it had zero model-visible advisory deliveries. That wording was false.
The engine generated 340 valid host-side feature receipts, ran 107 changed-file
lint decisions, and evaluated eight submissions. All controller decisions were
`PASS`; no fresh grounded failure required a submit hold.

The correct diagnosis is narrower. Commit `27c2652` intentionally changed the
historical generic-guidance policy to expose only five failure/impact FACTs.
That removed the prior 94-advisory regression, but this task panel had no event
that satisfied the remaining active triggers. Engine observation worked; its
trajectory-shaping policy was unreachable on the successful paths.

The correction is one deterministic, bounded engine control rather than a
return to generic advice: after three **material** source revisions with no
successful recognized behavioral validation and with an explicit task check,
the existing `GT_EDIT_CHECK` capability emits one next-decision validation-debt
payload. It carries the declared check and changed paths, resets after a real
validation, ignores bytecode/test-cache artifacts, and is deduplicated/capped
like every other intervention. This is a host-loop policy decision, not a
model-requested sidecar call.

Before the next authorized smoke, prove all of the following locally:

1. the debt payload is absent for cache-only changes, missing declared checks,
   and after a fresh successful validator;
2. it is present exactly once after the third material unvalidated edit;
3. it reaches only the immediately next model request and is absent thereafter;
4. historical trajectories replay to intended trigger points without turning
   passive localization/obligation/`PASS` receipts back into guidance; and
5. all existing failure, submit-hold, payload-validity, and all-17 timing
   contracts remain green.

Only after that audit passes may the already-authorized GitHub smoke be
dispatched. Its receipt review must separately report receipt counts,
controller decisions, active interventions, timing, model use, and deep
outcome-first deltas. A zero-intervention trajectory is valid when it remained
correctly validated; it is not evidence that the engine was absent.
