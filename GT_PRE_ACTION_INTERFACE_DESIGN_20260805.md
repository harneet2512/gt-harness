# GroundTruth Pre-Action Interface Design

Status: superseded by the implemented provider-free receipt in
`GT_PRE_ACTION_IMPLEMENTATION_RECEIPT_20260805.md`. No paid assistive smoke is
authorized by this document.

## Coverage interpretation rule

The 17-feature inventory is a lifecycle contract, not a promise that every
random task trajectory triggers every feature. A provider-free census uses
forced fixtures to prove all 17 producer and consumer paths. A paid smoke must
be audited separately for the feature IDs actually present in its receipts.
In run `30976148466`, 15/17 fired naturally; `recovery` and `signature_delta`
were absent because their exact events did not occur.

Effect counts and model-delivery counts are also different. That smoke applied
361 effects and delivered 36 model-visible effects. The remaining effects were
engine work or audit state, not missing payloads. Accounting must use the
effect-trace dispositions `provider_payload`, `existing_engine_actuation`,
`engine_internal_state`, `audit_only`, and `unread_private_state`; it must not
call all non-provider effects inert. Model delivery proves host injection, not
model comprehension or causal efficiency.

## 1. Current-state call graph and confirmed gaps

```text
LitellmModel.query(messages)                         agent.py:610-613
  -> dict assistant message
  -> message["extra"]["actions"]                   agent.py:623-633
  -> for action in actions                           agent.py:674
  -> command = action["command"]                    agent.py:675-677
  -> environment.exec(command, cwd, env, timeout)   agent.py:713-719
  -> WorkspaceSensor.scan + diff_snapshots          agent.py:731-739
  -> classify_validation_command                    agent.py:740-747
  -> CentralFeatureRuntime.observe_action           agent.py:748-758
  -> consume_effects                                agent.py:816-818
  -> record_predecided_continuation (audit only)    agent.py:819-837
  -> model_feedback(deferred=True)                  agent.py:842-846
  -> next model query
```

| Claim | Status | Caller -> callee and exact payload | Coverage |
|---|---|---|---|
| `environment.exec` precedes `observe_action` | TRUE | `MiniSweCentralAgent.run` calls `environment.exec(command, cwd=self.cwd, env={}, timeout_sec=...)`, then calls `observe_action(action_id, command, output, returncode, transition, revision, source_revision, snapshot, validation)` | `test_gt_central_agent.py` loop tests prove postflight but contain no preflight ordered spy |
| no GT function receives the proposal first | TRUE | action is read at 674-677 and the next GT call is submit-ledger logic, not a general proposal boundary | No contrary test |
| GT cannot stop/rewrite/augment before execution | TRUE | literal `command` flows unchanged to `environment.exec` | Existing tests assert model-selected commands execute literally |
| several response actions may run without reasoning | TRUE | one `for index, action in enumerate(actions)` loop surrounds a single `model.query` | multi-action tests exercise this |
| `record_predecided_continuation` audits only | TRUE | runtime line 2724 explicitly says “never cancel them”; method only edits effect counters | `test_gt_central_runtime.py:830` |
| semantics reconstructed from Bash | TRUE | active schema has only required string field `command`; validation/search/edit logic receives command text | `test_miniswe_typed_actions.py` proves a separate inactive typed-tool path, not central integration |

## 2. Actual model action structure

The assistant response is a Mini-SWE message dictionary. The central loop consumes
`message["extra"]["actions"]`, a sequence of mappings. In the frozen 2.2.8 Bash path
each normalized action contains `command` and normally `tool_call_id`; tests also
use `tool_name`. The public tool schema is exactly one function, `bash`, with one
required string property, `command`. Several actions are representable. LiteLLM /
Mini-SWE normalizes tool calls before `run` sees them; malformed tool calls raise
`FormatError` from `model.query` and are returned as messages by the existing
`InterruptAgentFlow`/format-error path. Execution begins at agent line 713.

```json
{"command":"sed -n '1,200p' src/models.py","tool_call_id":"call-7"}
```

Before execution GT can know: stable tool-call ID (or synthesize one), exact command,
model-call number, batch position/size, current workspace/source revisions, current
snapshot, explicit checks, prior failures, task deliverables, and already indexed
source evidence. It cannot know command output, resulting diff, syntax result, or the
model's unexpressed purpose.

## 3. Typed proposed-action contract

```python
class ActionOperation(StrEnum):
    READ="read"; SEARCH="search"; EDIT="edit"; CREATE="create"
    DELETE="delete"; VALIDATE="validate"; SUBMIT="submit"
    INSTALL="install"; OTHER="other"

@dataclass(frozen=True)
class ActionTarget:
    path: str
    role: str = "operand"

@dataclass(frozen=True)
class ProposedAction:
    action_id: str
    raw_command: str
    operation: ActionOperation
    targets: tuple[ActionTarget, ...]
    mutates_workspace: bool
    validation_kind: str | None
    source_revision: str
    workspace_revision: str
    model_call: int
    batch_index: int
    batch_size: int
    parser_confidence: float
```

`raw_command`, revisions, call, and batch coordinates are deterministic and required.
`operation`, targets, mutation, and validation kind are deterministic classifier
outputs but not necessarily semantically complete. Recognized simple commands receive
high confidence; compounds, shell expansion, ambiguous redirects, and unknown tools
remain `OTHER` at low confidence. Unknown always passes. Targets are repo-relative only;
ambiguous/outside paths are omitted, never guessed.

## 4. Preflight API and dispositions

```python
def preflight_action(self, proposed, snapshot, *, revision, source_revision,
                     ledger=None) -> PreflightDecision
```

`PreflightDecision` contains disposition, original/effective command, evidence,
reason codes, confidence, latency, and source revision. Dispositions:

- PASS: original executes unchanged. This is every failure/timeout/unknown default.
- AUGMENT: original executes; evidence is attached to its resulting observation once.
- RETURN_TO_MODEL: command does not execute; a synthetic observation is formatted and
  the next provider call decides again.
- REWRITE: effective command executes only with a registered mechanical proof. Disabled
  in the initial production policy; the typed result supports unit-tested policy hooks.
- SUPPRESS: no execution only for an explicit deterministic invariant. Initial central
  policy limits this to proven submit blockers; general destructive-shell safety remains
  the host/sandbox's job.

Budget: measure every call; target 25 ms p95 for state-only checks and 100 ms hard host
timeout. These are initial engineering limits, not measured benchmark facts. Any overrun
passes.

## 5. All-17 lifecycle placement

| Feature | Current trigger/timing | Preflight? | Postflight required? | Required inputs | Decision |
|---|---|---:|---:|---|---|
| obligations | task start/private | submit | yes | contract, ledger | preflight read only at submit |
| localization | search result/task-start | edit/create if graph exact | yes | parsed target, graph revision | two-sided |
| GT_LOC_RESLOT | search result/task-start | edit/create | yes | ranked source anchors | two-sided |
| def_partition | search result | edit if graph exact | yes | definition/reference graph | two-sided |
| caller_contract | search/edit result | edit if caller direction proven | yes | symbol + directed callers | two-sided, otherwise quiet |
| newfile_precedent | search/edit result | create | yes | exact new path + sibling precedent | two-sided |
| GT_CHANGE_SURFACE | workspace diff | no | yes | before/after snapshot | postflight only |
| signature_delta | edit diff | no | yes | before/after contents | postflight only |
| GT_PATCH_DELTA | edit diff | no | yes | changed paths/delta | postflight only |
| GT_EDIT_CHECK | edit result | validate/edit debt | yes | changed paths, declared check | two-sided |
| syntax_result | generated source/lint | no | yes | actual file + compiler result | postflight only |
| covering_red | failed validation | no | yes | actual output/return code | postflight only |
| GT_HYPOTHESIS | failure transition | repeated action only | yes | prior failure fingerprint | two-sided |
| recovery | repeated failure | repeated action only | yes | prior repeated failure | two-sided |
| submit_refusal | failing check/submit | submit | yes | fresh grounded ledger blockers | two-sided |
| GT_SS_SUBMIT_RED | failure/submit | submit | yes | blocker set/source revision | two-sided |
| GT_CERT_DELIVERY | submit | submit precheck | yes | readiness vector | two-sided; postflight receipt authoritative |

No feature is invoked as 17 separate preflight calls. One proposal is normalized once;
the runtime consults only evidence available at that boundary.

## 6. Batching decision

Use hybrid Option D. Read-only READ/SEARCH actions may remain batched. After any action
classified as workspace-mutating, VALIDATE, SUBMIT, INSTALL, DELETE, or OTHER, cancel the
unexecuted suffix and return the accumulated real observations to the model. Also break
after any failed action or material postflight frame. This is stricter than independent
preflight because later actions were selected against stale state. It costs turns only at
dependency barriers, preserves safe read batching, needs no model/API schema change, and
matches the barrier logic already present in the legacy engine runner.

## 7. Bounded evidence and delivery

- READ: PASS; no duplicated file. AUGMENT only for proven stale/missing alias (160 chars).
- SEARCH: PASS; no predicted result. Optional exact scope/alias, 160 chars.
- EDIT: exact definition/callers/coupled validation, maximum 320 chars, confidence >= .9.
- CREATE: existing equivalent or sibling precedent, maximum 240 chars, >= .9.
- VALIDATE: declared/minimal check and stale duplicate state, maximum 200 chars, >= .9.
- SUBMIT: fresh blockers/unresolved declared requirement, maximum 320 chars, confidence 1.

Material means the evidence mechanically contradicts the proposed target/submit, proves
a duplicate, or proves the proposal's source revision stale. Material evidence uses
RETURN_TO_MODEL. Non-material evidence uses AUGMENT or controller-only state. It is never
added to the task instruction, never rewrites the user prompt, and never injects a full
file. A synthetic tool observation is the return surface because it preserves the Bash
interface and causally brackets the selected tool call.

## 8. Failure-mode registry

Parser error, timeout, missing graph, stale graph, ambiguous path, low confidence,
unsupported compound command, missing source revision, evidence rendering failure, and
runtime exception all produce PASS plus an audit receipt. Revision mismatch between
decision and dispatch invalidates non-PASS to PASS. Duplicate evidence fingerprints are
suppressed. REWRITE requires original/effective equivalence proof and confidence 1;
otherwise PASS. SUPPRESS requires a named invariant and source-bound proof.

## 9. Test and minimal patch plan

Tests cover the 17 cases specified by the request plus an end-to-end edit/reconsider/edit
trajectory. Primary files:

- `gt_engine/preflight.py`: contracts, adapter, conservative parser, receipts/metrics.
- `gt_engine/central_runtime.py`: one `preflight_action`, source-bound material policy,
  lifecycle placement registry.
- `eval/gt_central_agent.py`: flag, timeout wrapper, pre-exec dispatch, synthetic result,
  hybrid batch barrier, postflight action-ID join, metrics.
- `tests/test_gt_preflight.py`: unit/lifecycle/failure cases.
- `tests/test_gt_central_agent.py`: ordered spy and end-to-end trajectory.
- `AGENTS.md`, `CLAUDE.md`: actual two-sided behavior and limits.

## 10. Non-goals, risks, rollback

No prediction, MCP, GT model call, indiscriminate file injection, speculative fact,
benchmark-task special case, or replacement of postflight. Initial policy does not enable
general command rewrite. Risks are false mutation classification, extra calls at barriers,
and submit over-intervention. Mitigations are conservative OTHER, correct-or-quiet,
source binding, one-return dedup, and exact receipts. One constructor flag
`enable_preflight=False` restores the current post-action loop. Git revert of the isolated
preflight files/wiring is the code rollback. Paid smoke remains blocked until provider-free
ordered-spy, all-17 census, and full regression gates pass.

## 11. 2026-08-07 outcome-preserving efficiency extension

Preflight and postflight now share a normalized executable invocation that
unwraps literal environment, command, sudo, and timeout wrappers. The resulting
validation classification separates executable recognition from task-contract
authority. Only a task-declared check can create required-check provider text
or submission debt; custom probes remain private.

Every task-environment call is routed through `HostExecutionRecorder`, so model
actions, sensor calls, syntax checks, completion probes, and auto-submit appear
in the same execution ledger. The corrected resource measure is
`effective_task_actions`, not model action count.

Provider context is preserved exactly until the measured prepared request
crosses its headroom reserve. Compaction then creates a single immutable
checkpoint epoch and appends later turns. A fresh bounded state frame is added
only to a provider-view copy of the latest safe tool surface, not frozen into
the checkpoint. Partial completion plans execute no probes. Active mode may grant a bounded timeout
only to a high-confidence terminal declared or standard validation runner.

See
`details_done/GT_OUTCOME_PRESERVING_EFFICIENCY_IMPLEMENTATION_20260807.md`
for implementation and archived replay evidence.
