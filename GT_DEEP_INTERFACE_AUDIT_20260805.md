# GroundTruth × Mini-SWE Deep Interface Audit — Evidence from Implementation

Date: 2026-08-05
Branch: `inline-engine` @ `baaa827`
Files audited: `eval/gt_central_agent.py` (908 lines), `gt_engine/central_runtime.py` (2695 lines), `gt_engine/central_controls.py` (220 lines)
Active agent: `eval.gt_central_agent:MiniSweCentralAgent`

Every claim below cites `file:line`. Nothing here is inferred from an ideal architecture; it is what the code executes.

---

## Part 1 — Find the Interface

There is **no MCP tool, no hook, no sidecar, no separate process, no event bus**. The interface is a **single host-owned Python class** that owns the model/action loop and calls the runtime **synchronously, in-process**.

| Role | Symbol | Location |
|---|---|---|
| Loop owner (host agent) | `MiniSweCentralAgent` | `eval/gt_central_agent.py:90` |
| Loop entry | `MiniSweCentralAgent.run()` | `eval/gt_central_agent.py:327` |
| GT engine | `CentralFeatureRuntime` | `gt_engine/central_runtime.py:1071` |
| Effect registry | `CONSUMER_SPECS` / `FeatureEffect` | `gt_engine/central_controls.py:94` / `:35` |
| Evidence ledger | `EvidenceLedger` | `gt_engine/central_runtime.py:738` |
| Workspace observer | `WorkspaceSensor` | `gt_engine/central_runtime.py:841` |

Every caller→callee edge and its payload:

| Caller (`file:line`) | Callee (`file:line`) | Payload in | Returns |
|---|---|---|---|
| `run()` :380 | `begin_task()` `central_runtime.py:1345` | `instruction, revision, source_revision, explicit_checks, task_deliverables` | `None` |
| `run()` :614 | `observe_action()` `central_runtime.py:1400` | `action_id, command, output, returncode, transition, revision, source_revision, snapshot, validation` | `None` |
| `run()` :669 | `consume_effects()` `central_runtime.py:2383` | `action_id, call` | `list[FeatureEffect]` |
| `run()` :696 | `model_feedback()` `central_runtime.py:2584` | `deferred=True` | `str` (rendered advisory) |
| `run()` :432 | `confirm_prepared_guidance()` `central_runtime.py:2504` | — | `dict \| None` (delivery metadata) |
| `run()` :558 | `record_submit()` `central_runtime.py:2081` | `action_id, revision, source_revision, refused, sensor_healthy, check_count, passing_checks, failing_checks, blockers` | `None` |
| `_run_lint()` :642 | `record_syntax()` `central_runtime.py:2030` | `action_id, revision, source_revision, failed, reason, path, command, returncode, diagnostic` | `None` |

**Conclusion:** the interface is `MiniSweCentralAgent.run()` calling `CentralFeatureRuntime` methods directly. The only data crossing the boundary is the shell command string, its output/return code, the workspace diff, and classification objects.

---

## Part 2 — Trigger Discovery

GT has exactly **one input surface**: the model-selected shell command, its result, and the before/after workspace diff. Triggers are **regex + classifier matches inside `observe_action()`** plus explicit calls from the agent (`record_syntax`, `record_submit`).

The triggers, where detected, and how:

| Trigger event | Detection site | Detection mechanism | Receiver |
|---|---|---|---|
| Search command | `observe_action` :1420 | `_SEARCH` regex `central_runtime.py:1083` on `normalize_command(command)` | `localization`, `def_partition`, `caller_contract`, `newfile_precedent`, `GT_LOC_RESLOT` (emits :1650–1750) |
| Workspace change | `observe_action` :1517 | `transition.changed_paths` non-empty (from `diff_snapshots` :448) | `GT_CHANGE_SURFACE` :1517, `GT_PATCH_DELTA` :1574 |
| Validation command | `observe_action` :1446 | `classification.is_validation` from `classify_validation_command` :624 | lifecycle + `GT_CERT_DELIVERY` pass :1469 |
| Validation failure | `observe_action` :1766 | `returncode != 0` and `classification.is_validation` and `failure_kind == "validation_failure"` | `covering_red` :1766, `GT_HYPOTHESIS` :1799, `recovery` :1873 (on repeat ≥2) |
| Signature edit | `observe_action` :1975 | `_semantic_signature_deltas` :1325 over `transition.before_contents/after_contents` | `signature_delta` :2005 |
| Syntax failure | `_run_lint()` `agent:641` → `record_syntax` :2030 | host-side `py_compile`/`node --check` etc. via `lint_commands` :994 | `syntax_result` |
| Submit command | `run()` :543/691 → `record_submit` :2081 | `is_submit_command` :489 (`COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` marker :37) | `submit_refusal`, `GT_CERT_DELIVERY`, `GT_SS_SUBMIT_RED` |
| Validation debt | `observe_action` :1606 | ≥3 unvalidated authored edits (`_unvalidated_material_edits >= 3`) | `GT_EDIT_CHECK` :1606 |

Payload example (obligations, `central_runtime.py:1374`):
```json
{"requirements_present": true, "obligation_ids": ["pytest -q"], "declared_checks": ["pytest -q"], "message": "..."}
```
Payload example (covering_red, `central_runtime.py:1766`): carries `command`, `command_class`, `failure_kind`, `attribution`, `diagnostic`, `returncode`, `phase`.

---

## Part 3 — Timeline (one full iteration)

```
[LLM call N]  model.query(query_messages)                         agent:504
     │
     ▼
actions = extra["actions"]                                        agent:527
     │
     ├─► for action in actions:
     │      command = action["command"]                            agent:542
     │      result = environment.exec(command)                     agent:580
     │      after = sensor.scan(...)                               agent:597
     │      transition = diff_snapshots(snapshot, after)           agent:598
     │      source_revision = source_revision_of(after)            agent:605
     │      classification = classify_validation_command(...)      agent:606
     │      features.observe_action(command, output, returncode,   agent:614
     │                                transition, revision, ...)   │
     │            └─► GT emits FeatureReceipts (Part 2)            central_runtime:1211
     │            └─► _route_effect → FeatureEffect                 central_runtime:2172
     │      features.consume_effects(action_id, call)              agent:669
     │            └─► _apply_effect → CentralControllerState        central_runtime:2217
     ▼
[after all actions]
features.model_feedback(deferred=True)  → pending_guidance         agent:696
     └─► _prepared_guidance = metadata                              central_runtime:2692
     ▼
[LLM call N+1]
if pending_guidance:
    _inject_runtime_evidence(messages, pending_guidance)            agent:426-431
        └─► appends evidence to the LAST tool message content       agent:67-81
    confirm_prepared_guidance() → delivery_metadata                 agent:432
model.query(query_messages)                                         agent:504
```

So the real order per decision is: **LLM reasons → action executes → GT observes → GT delivers into the request for the next LLM call → next LLM reasons.** GT never runs between "LLM decides" and "action executes."

---

## Part 4 — Bash / Tool Detection

**Hypothesis CONFIRMED: GT watches bash execution.** There is only one tool (`BASH_TOOL`, imported at `agent:38`; the model has a single `bash` function).

Evidence chain:

| Stage | Code |
|---|---|
| The model's action is a bash command string | `command = str(action.get("command") or "")` `agent:542` |
| The command is executed by the host | `environment.exec(command, cwd=self.cwd, env={}, timeout_sec=self.command_timeout_sec)` `agent:580` |
| The command string is parsed | `normalize_command` `central_runtime.py:485`; `_shell_segments` :559; `_recognized_validation` :578 |
| The command is classified | `classify_validation_command(command, explicit_checks)` `central_runtime.py:624` |
| The command+output+returncode are handed to GT | `observe_action(...)` `agent:614` |

There is **no separate "edit"/"search"/"open" tool abstraction** — the agent's model emits only `bash` actions (see `BASH_TOOL` at `agent:38`, and the tool definitions written to ATIF at `agent:309`). GT detects semantics from the command text via the class regexes `central_runtime.py:1083–1096` (`_SEARCH`, `_EDIT`, `_SIGNATURE`, `_FAILURE`, `_PRECEDENT`, `_CALLSITE`, `_DEFINITION`).

---

## Part 5 — Reasoning Boundary

**Answer: D (GT runs after execution) for every trigger; delivery is injected before the next `model.query()` call.**

Proof by call order in `run()`:
1. `model.query(query_messages)` — the LLM reasons (`agent:504`).
2. The returned `actions` are executed (`agent:580`) and *then* `observe_action` fires (`agent:614`) — GT runs strictly **after** execution of the action.
3. `model_feedback(deferred=True)` is called only after the action loop (`agent:696`), so GT's deterministic work for action N happens after action N's execution.
4. On the next loop iteration, `_inject_runtime_evidence` (`agent:426–431`) places the evidence into the message list **before** `model.query` (`agent:504`) for call N+1.

There is **no code path where GT runs before the LLM's first decision within the same step, and no pre-execution interception.** The engine cannot prevent an already-returned action. This is confirmed by the comment at `central_runtime.py:2415`: `record_predecided_continuation(...)` is "Audit actions already chosen in the same model response; **never cancel them**."

So the "solves the timing problem" claim is narrower than "GT runs before reasoning": it is **post-execution observe, pre-next-reason deliver** — the evidence for action N is present in the exact provider request that decides action N+1. The receipt field `one_step_late` is computed at `agent:461` (`calls != pending_prepared_after_call + 1`) and the window is labeled `first_next_model_call` (`agent:459`).

---

## Part 6 — The 17 Features

Model-visible set: `_MODEL_ACTIONABLE_FEATURES` = `{covering_red, newfile_precedent, recovery, signature_delta, submit_refusal, syntax_result}` (`central_runtime.py:38`) plus two special cases: `GT_EDIT_CHECK` (only with `intervention == "validation_debt"`) and `GT_LOC_RESLOT` (only when `discarded_anchor_count > 0`) (`_is_model_actionable` :1386–1398).

| # | Feature | Trigger | Exact Event | Implemented | Input Payload | Output Payload | Before LLM? | After LLM? | Before Tool? | After Tool? |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | obligations | task start | `begin_task` :1345 emits :1367 | `central_runtime.py:1345` | instruction, explicit_checks, deliverables | `{requirements_present, obligation_ids, declared_checks}` | no | no | n/a | n/a |
| 2 | localization | non-empty search output | `observe_action` :1420 → :1650 | `central_runtime.py:1650` | command, output | `{anchors, query}` | **yes (next call)** | yes | no | yes |
| 3 | def_partition | search output has definition anchors | :1420 → :1699 | `central_runtime.py:1699` | output | `{definition_anchors, reference_anchors}` | yes (next) | yes | no | yes |
| 4 | caller_contract | search output matches `_CALLSITE` | :1420 → :1725 | `central_runtime.py:1725` | output | `{callers}` | yes (next) | yes | no | yes |
| 5 | newfile_precedent | precedent marker in search OR new source file created | :1420 → :1957; :1951 | `central_runtime.py:1957` | output / transition | `{precedent_path, created_files}` | yes (next) | yes | no | yes |
| 6 | covering_red | validation failure | :1766 | `central_runtime.py:1766` | command, output, returncode, classification | `{command, diagnostic, attribution, failure_kind}` | yes (next) | yes | no | yes |
| 7 | recovery | same failure repeated ≥2 at unchanged source revision | :1873 | `central_runtime.py:1873` | failure fingerprint, repeat_count | `{repeat_count, alternate_action}` | yes (next) | yes | no | yes |
| 8 | signature_delta | signature-shaped edit (semantic or `sed -i`) | :1975 → :2005 | `central_runtime.py:2005` | before/after contents | `{symbol, before_signature, after_signature, callers}` | yes (next) | yes | no | yes |
| 9 | submit_refusal | submit while fresh grounded check failing | `record_submit` :2081 | `central_runtime.py:2081` | ledger blockers | `{refused, blockers}` | yes (next) | yes | no | n/a (submit) |
| 10 | syntax_result | host lint failure | `_run_lint` agent:641 → `record_syntax` :2030 | `central_runtime.py:2030` | path, command, diagnostic, returncode | `{ok, path, command, returncode, diagnostic}` | yes (next) | yes | no | yes |
| 11 | GT_CERT_DELIVERY | submit boundary OR fresh validation pass | :1469, `record_submit` :2081 | `central_runtime.py:2081` | ledger readiness | `{check_count, passing_checks, failing_checks, readiness}` | yes (next) | yes | no | n/a |
| 12 | GT_CHANGE_SURFACE | any workspace change | :1517 | `central_runtime.py:1517` | transition | `{created, modified, deleted, source_relevant, origins}` | no (private) | yes | no | yes |
| 13 | GT_PATCH_DELTA | source change | :1574 | `central_runtime.py:1574` | transition | `{changed_paths}` | no (private) | yes | no | yes |
| 14 | GT_EDIT_CHECK | ≥3 unvalidated authored edits | :1606 | `central_runtime.py:1606` | `_unvalidated_material_edits` | `{declared_check, changed_paths, intervention}` | yes (next, only if validation_debt) | yes | no | yes |
| 15 | GT_HYPOTHESIS | validation failure fingerprint | :1799 | `central_runtime.py:1799` | failure signature | `{failure_fingerprint, repeat_count}` | no (private) | yes | no | yes |
| 16 | GT_LOC_RESLOT | search anchors + rank | :1666 | `central_runtime.py:1666` | anchors | `{selected_anchors, discarded_anchor_count}` | yes (next, only if anchors discarded) | yes | no | yes |
| 17 | GT_SS_SUBMIT_RED | submit refusal | :1833 | `central_runtime.py:1833` | blockers | `{owner_feature, refused}` | no (private) | yes | no | n/a |

"yes (next)" = the payload is placed in the provider request for the **next** model call (not the one that produced the action).

---

## Part 7 — Marker Detection

GT does **not** detect tool-name markers such as `open_file`, `edit_file`, `replace`, `run_tests`. There is no enum, protobuf, or hook for tool names — the model has a single `bash` function (`BASH_TOOL`, `agent:38`).

The actual markers GT uses:

| Abstraction | Code | Purpose |
|---|---|---|
| Bash command string | `agent:542` | the only model action input |
| Submit marker string | `_SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"` `central_runtime.py:37`; `is_submit_command` :489 | submit detection (string normalization, not a tool name) |
| Command regexes | `_SEARCH`/`_EDIT`/`_SIGNATURE`/`_FAILURE`/`_PRECEDENT`/`_CALLSITE`/`_DEFINITION` `central_runtime.py:1083–1096` | search/edit/failure/precedent detection |
| Validator classifier | `classify_validation_command` :624; `_recognized_validation` :578 | pytest/ctest/mvn/etc. detection |
| Path classifier | `classify_change` :140 | source vs artifact vs deliverable |
| Dataclass objects | `WorkspaceTransition` :96, `ValidationClassification` :495, `FeatureReceipt` :1020, `FeatureEffect` (`central_controls.py:35`) | the real event objects |

---

## Part 8 — Data Flow (end to end)

```
messages (list[dict]) ──► model.query(query_messages)                    agent:504
        │  returns assistant message with extra.actions
        ▼
actions[].command ──► environment.exec(command) ──► ExecResult          agent:580
        ▼
output {output, returncode}                                              agent:592
        ▼
after = sensor.scan(environment, previous=snapshot) ──► WorkspaceSnapshot (central_runtime.py:87)
transition = diff_snapshots(snapshot, after) ──► WorkspaceTransition      agent:598 / :448
source_revision = source_revision_of(after, deliverables)                agent:605 / :166
classification = classify_validation_command(...).with_result(...) ──► ValidationClassification  agent:606 / :495
        ▼
observe_action(action_id, command, output, returncode, transition,
               revision, source_revision, snapshot, validation)          agent:614
        ▼  (in _emit :1211) FeatureReceipt  ──► _route_effect :2172 ──► FeatureEffect
        ▼
consume_effects(action_id, call) ──► _apply_effect :2217 ──► CentralControllerState :1038
        ▼
model_feedback(deferred=True) ──► rendered advisory str  (central_runtime.py:2584 → render_runtime_advisory :831)
        ▼
pending_guidance ──► _inject_runtime_evidence(messages, evidence)         agent:426 / :67
        └─► appends "\n\n<advisory>" to the last tool message content      agent:75-79
        ▼
model.query(query_messages)                                              agent:504
```

Adapters/wrappers: `_inject_runtime_evidence` (`agent:67`) is the only wrapper — it copies the message list, mutates a copy's last tool message, and returns `(prepared, index, chars)`. No serialization boundary exists between GT and Mini-SWE; they share live Python objects in one process.

---

## Part 9 — Synchronization

There is **no buffering, no event replay, no execution wrapper, no separate thread**. Synchronization is **synchronous in-loop interception**:

1. Effects are consumed immediately after each action: `consume_effects` (`agent:669`) stamps timing and calls `_apply_effect` (`central_runtime.py:2383`, `:2217`).
2. Controller state is updated in place (`_apply_effect`, `central_runtime.py:2217`), so the controller is never "one edit behind" — it reflects action N before action N+1 begins.
3. One advisory is prepared after the batch: `model_feedback(deferred=True)` (`agent:696`) stores `_prepared_guidance` (`central_runtime.py:2692`).
4. The prepared advisory is injected into the **exact** next request: `_inject_runtime_evidence` at the top of the next iteration, before `model.query` (`agent:426–431`, `:504`).
5. Lateness is measured, not assumed: `one_step_late = calls != pending_prepared_after_call + 1` (`agent:461`); `predecided_actions_executed_after_evidence` is recorded (`central_runtime.py:2395`, `:2414`).

The one deliberate limit: **already-decided actions in the same model response are not cancelled.** `record_predecided_continuation` audits them (`central_runtime.py:2414`) but the contract comment is explicit: "never cancel them" (`central_runtime.py:2415`).

---

## Part 10 — Evidence-only verdicts

- **No MCP/hook/sidecar interface.** The entire interface is `MiniSweCentralAgent.run()` → `CentralFeatureRuntime` in one process. (`agent:327`, `agent:614`, `agent:669`, `agent:696`.)
- **GT watches bash.** The command string is the sole trigger input (`agent:542`, `agent:614`); detection is regex/classifier on the command text (`central_runtime.py:1083–1096`, `:624`).
- **Reasoning boundary = D for triggers, "before next call" for delivery.** Observe after execution (`agent:614` vs `agent:580`); deliver before the next `model.query` (`agent:426` vs `agent:504`).
- **Never more than one advisory per decision.** `model_feedback` renders up to 3 concatenated facts (`central_runtime.py:2638` caps at `len(facts) >= 3`), bounded by `render_runtime_advisory` (`central_runtime.py:831`), one payload per request.
- **GT cannot block.** The only submit-time gate is a one-time hold (`EvidenceLedger.submit_decision`, `central_runtime.py:779`); pre-decided actions are never cancelled (`central_runtime.py:2415`).

### Real execution-order sequence diagram

```
Mini-SWE LLM ──decide N──► bash cmd ──► host exec ──► result
                                                │
                                     sensor.scan + diff_snapshots
                                                │
                                     observe_action ──► 17 producers ──► FeatureReceipt
                                                │                        │
                                     consume_effects ──► _route_effect ──► FeatureEffect
                                                │                        │
                                                └───────── _apply_effect ─► CentralControllerState
                                                ▼
                                model_feedback(deferred) ──► _prepared_guidance
                                                ▼
Mini-SWE LLM ──decide N+1◄── model.query ◄── _inject_runtime_evidence(evidence in last tool msg)
```

The single interception point is `agent:614` (observe) and `agent:426–431` (deliver). Nothing else touches the model stream.
