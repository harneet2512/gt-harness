# GT Context and Efficiency Remediation Plan

Status: implementation complete; GitHub development-canary proof pending. No
ten-task run is authorized from the current evidence.

## Decision

Do not tune GT to the three completed canary tasks.  The two all-17 treatment
canaries are diagnostic data, not a training set.  The next change must repair
the general evidence contract exposed by the trajectory, then it must pass
provider-free adversarial tests and independently chosen holdout tasks before
the ten-task smoke is promoted.

## 1. What the live evidence proves

Run `30877236786` produced three solved, uncensored trials with a healthy host
sensor and all 17 runtime features enabled.  Its timing boundary was correct:
each of the two visible `covering_red` messages was appended only to the next
model request after the action that supplied its evidence.

That does not establish efficiency.  Against frozen GT-off:

| task | total tokens | uncached input | API calls / steps | actions | result |
|---|---:|---:|---:|---:|---|
| break-filter-js-from-html | +4.97% | -27.39% | +25.00% | +37.50% | solved |
| llm-inference-batching-scheduler | -35.12% | -36.89% | -19.51% | -9.52% | solved |
| write-compressor | +199.58% | +251.87% | +156.25% | +158.82% | solved |

The strongest concrete defect is in `write-compressor`.  The action was a
heredoc Python experiment that happened to contain the words `Test 1`.  The
current regex searches the whole shell string, labelled that experiment a
check, and treated `python3: command not found` (return code 127) as an
attributable post-edit regression.  It sent: "A required check is failing;
repair the attributable regression."  The timing was correct; the assertion
was false.  A no-op or unavailable executable is an environment capability
failure, not test evidence and not a causal claim about the edit.

The source is general: `is_check_command()` uses a free-text regex over the
entire command.  Therefore comments, heredocs, quoted source, filenames, and
data can all create false validation evidence.  It must not be patched for
this task's wording.

## 2. Research implications

SWE-agent's ACI work reports that concise, purpose-built observations are
important and that excess search context confuses the model.  This supports
strictly bounded, provenance-bearing advisories rather than more prose.
https://github.com/SWE-agent/SWE-agent/blob/main/docs/background/aci.md

Recent agent-evaluation evidence finds substantial trajectory variance even at
temperature zero and recommends repeated independent trials plus uncertainty
analysis; a single temperature-1 run cannot attribute a small delta to GT.
https://arxiv.org/abs/2602.07150

Agentic-SE evaluation guidance recommends retaining trajectories and LLM
interaction data so differences are explainable and reproducible.  The receipt
and trajectory remain the source of truth, but need causal/provenance fields.
https://arxiv.org/abs/2604.01437

The resulting design principle is: **an advisory must be true, attributable,
actionable, bounded, and experimentally useful.**  A correct time stamp alone
does not make an advisory safe or efficient.

## 3. Remediation work, in order

### A. Establish a validation-evidence contract (first change)

Replace free-text check detection with a conservative command classifier that
examines executable positions and shell connectors, never arbitrary heredoc,
quoted-program, comment, or output text.  Classify every non-zero action as:

1. `declared_validation`: matches an explicit validation command extracted from
   the task instruction, with normalized executable/arguments;
2. `recognized_validation`: a top-level test runner or verifier invocation;
3. `environment_failure`: return 126/127, `command not found`, missing
   executable, interpreter/import/bootstrap failure, or transport failure;
4. `exploration_or_unknown`: all remaining commands.

Only a `declared_validation` or `recognized_validation` failure may create
`covering_red`.  Its payload must include `command_class`, `attribution`,
`failure_kind`, `returncode`, `revision`, and a bounded diagnostic fingerprint.
`attribution=post_edit` is permitted only when the validation command is
grounded and the relevant workspace revision differs from the last passing
validation revision.  Otherwise use the neutral wording "inspect the validation
result and environment before changing code"; never call it required or
attributable.

Environment failures remain private diagnostic receipts.  They can never drive
`covering_red`, `recovery`, a submit hold, or a model-visible message.  A
repeated recovery trigger requires the same classified validation command,
fingerprint, and revision relationship; repeated infrastructure failure is not
a repeated product failure.

Provider-free tests must cover heredocs, comments, quoted strings, `python -c`,
pipelines, shell connectors, real test runners, explicit task checks, missing
interpreters, exit 126/127, assertion failures, compiler failures, and a
post-edit/pass/fail/pass revision sequence.  The tests must assert both the
positive contract and that no visible feedback is generated for false cases.

### B. Measure context rather than guessing about it

The 82-character visible advisory did not itself account for millions of
tokens, but a short early intervention can change a stochastic trajectory.
Measure that causal path rather than asserting either explanation.

Add receipt/deep-metric fields for every model call:

- total context characters/tokens before the call;
- stock task, prior assistant, prior tool-observation, and GT-advisory portions;
- advisory feature, evidence action, delivery call, and expiry call;
- command class and outcome for the evidence action;
- next-action relation: ignored, inspection, validation, edit, or submit;
- cumulative retained-context growth and cache-miss growth per call.

Keep current transient delivery: one advisory in one next decision request,
then remove it.  Do not add a running GT summary, a global reminder, or an
extra tool.  Any future context compaction must be arm-neutral, versioned, and
evaluated as its own intervention in baseline, shadow, and treatment; otherwise
it confounds GT's effect with a new agent policy.

Use a fixed per-advisory budget: one factual sentence, no feature names, no
unverified diagnosis, and no more than one advisory per evidence revision.  An
advisory that cannot name its evidence class and action cannot be model-visible.

### C. Separate feature coverage from treatment exposure

All 17 features remain enabled and auditable.  This does not mean every feature
should speak to the model.  The inventory must report four separate states:
`enabled`, `eligible`, `observed`, and `model_visible`.  Private CAP/lifecycle
receipts are successful only when they observe their legitimate boundary; they
must not be fabricated to raise feature counts.

Create a provider-free event matrix for all 17 features and an adversarial
negative matrix.  The latter proves that absent events, environmental failures,
stale revisions, and ungrounded checks produce no visible payload.  This is
where broad feature coverage is proved—not by making the three benchmark tasks
trigger every feature.

### D. Evaluate causal efficiency without overfitting

Freeze this remediation design before its first provider run.  Use three
disjoint sets:

1. **Synthetic contract suite:** provider-free, broad command/outcome grammar.
2. **Development canary:** the existing three tasks, used only to verify the
   known false-positive class has disappeared.
3. **Holdout smoke:** the other seven members of the predefined ten-task set;
   do not alter the rule based on their trajectories.

Run matched GT-off, shadow, and treatment arms with the same model/version,
workflow limits, task image, concurrency policy, and prompt.  If the provider
supports a seed, verify that it is accepted and record it; otherwise use at
least three independent repetitions per arm and compare task-level medians with
bootstrap confidence intervals.  Never compare a censored or unsolved resource
trace as an efficiency win.

The primary efficiency measures are uncached input tokens, normalized cost,
assistant steps, actions, time to first relevant validation, failed-action
count, and wasted-action proxy.  Total tokens and cache-hit rate remain
mandatory secondary measures.  Report the per-call context decomposition to
determine whether a delta came from retained history, GT guidance, or behavior.

### E. Promotion gates

The three-task rerun may proceed only after A-C pass locally.  The ten-task
smoke may proceed only when its development canary has 3/3 verifier outputs,
zero censorship, zero timing/payload violations, zero false-positive visible
advisories, and full receipt coverage.

The ten-task result is not a promotion merely because it solves tasks.  Before
claiming GT improves efficiency, require three repetitions and publish all of:

- solve non-inferiority for each task;
- no positive median delta in total tokens, uncached input, normalized cost,
  calls, actions, or assistant steps for a comparable solved task;
- a confidence interval that does not support a material regression;
- at least one causal behavioral-use trace (L2 reference or L3 action) for any
  visible advisory; and
- no new change after looking at holdout outcomes without restarting the split.

The literal requirement that every single stochastic trial be negative is not
scientifically defensible; model trajectories diverge early.  The strict,
auditable replacement is no positive **median** on every declared resource
metric, uncertainty bounds against material regression, and no solve loss.
Failure on any item keeps the 89-task run blocked.

## 4. Immediate TODOs

1. Completed: structural command/provenance classification rejects heredoc and
   comment text; missing executables and return 126/127 cannot emit
   `covering_red`, `GT_HYPOTHESIS`, recovery, or visible feedback.
2. Completed: valid `covering_red` receipts carry command class, failure kind,
   and a deliberately non-causal attribution; recovery requires the same
   normalized validation command, failure fingerprint, return code, and
   revision.
3. Completed: receipt-v2 records per-call stock/system/assistant/tool/advisory
   context decomposition and next-action relation; deep metrics expose advisory,
   stock, and maximum context totals.
4. Completed: lint, 32 central-runtime/agent/deep-metric tests, and the
   provider-free all-17 timing census passed.
5. Pending: commit and push one immutable remediation commit, then run the
   three-task development canary once and audit every receipt and action.
6. If it passes the gates, run the untouched seven-task holdout smoke, then the
   predeclared ten-task shadow/treatment repetitions.
7. Do not run 89 tasks until the repeated outcome-first gates pass.
