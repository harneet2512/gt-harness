# PROVE IT v3 — all 17 accounted for (16 delivered + 1 REMOVE), one smoke

Status: APPROVED (2026-08-02). Implementation on branch `inline-engine`.

## Objective

Every GT DIRECT feature must deliver a **correct-scope, usable, fresh** fact at
the **correct time** (same canonical observation as its trigger, before the next
model call) — proven through the full `engine_execute_actions` loop, not just
producer-level forcing. **No benchmark smoke until the visibility matrix shows
all 16 non-REMOVE features green.** Exactly one smoke at the end.

## Inventory (all 17)

9 FACT: `def_partition`, `syntax_result`, `covering_red`, `obligations`,
`localization`, `recovery`, `signature_delta`, `newfile_precedent`,
`submit_refusal`.

7 CAP_OWNER: `GT_EDIT_CHECK`(syntax_result), `GT_PATCH_DELTA`(signature_delta),
`GT_LOC_RESLOT`(localization), `GT_SS_SUBMIT_RED`(submit_refusal),
`GT_HYPOTHESIS`(recovery), `GT_CHANGE_SURFACE`(newfile_precedent),
`GT_CERT_DELIVERY`(delivery_receipt).

`caller_contract` = **REMOVE by 129-row disposition** — "fix" is documenting the
REMOVE, shipping nothing.

## The three structural gaps (code-verified)

**Gap 1 — `def_partition` structurally unreachable through the engine path.**
`_gateway_facts` hardcodes `ToolEvent(kind="bash", ...)` (runner.py:669).
`classify_outcome` (gateway.py:1897) returns `SATISFIED` for any
`kind != KIND_SEARCH`, so the search-outcome lattice never runs. Result:
`AMBIGUOUS_HIT/FLOOD → def_partition`, `ZERO_ABSENT → newfile_precedent`,
`WRONG_SURFACE → wrong_surface`, `ZERO_NAME → name_fold`,
`ZERO_BEHAVIOR → body` can never fire through the engine. Fix (F1): thread
`kind=classify_command(command)`.

**Gap 2 — `_EVIDENCE_TO_OWNER` drops two real evidence_types.**
- `caller_break` (cross-language caller-impact, gateway.py:3974) is unmapped →
  non-Python signature breaks never deliver `signature_delta`.
- `missing_role[:...]` / `missing_role_postcreate[:...]` (the "wire up next"
  half of `change_surface`, emitted as `evidence_type=fact_kind`) is unmapped →
  the half the `newfile_precedent` edit-path depends on is silently dropped.
Fix (F2): map `caller_break → signature_delta`; resolve the `base:suffix` form →
`newfile_precedent`.

**Gap 3 — correct-time asserted at producer level, not engine level.**
The forcing suite proves producers CAN emit; `test_engine_interface.py` proves
correct-time only for covering + submit. Fix (Part 2): extend the full
`engine_execute_actions` loop pattern to all 16 triggers.

## Fixes (Part 1, engine-side)

- **F1. ToolEvent kind threading.** `_gateway_facts` passes
  `kind=classify_command(command)` (KIND_SEARCH/KIND_VIEW/KIND_EDIT/KIND_TEST/
  KIND_SUBMIT). Regression test: ambiguous search (>=2 def files in graph)
  delivers `def_partition` through `engine_execute_actions`.
- **F2. Owner-map completeness.** Add `caller_break → signature_delta`; resolve
  `missing_role[:...]` / `missing_role_postcreate[:...]` → `newfile_precedent`.
- **F3. covering_red.** Output-based test-failure detection (non-zero run whose
  output carries `FAILED`/`Traceback`/`AssertionError`/`E   `/`ERROR: `) in
  BOTH `_covering_red_artifact` and `_covering_result`.
- **F4. recovery.** New engine `_recovery_fact`: on the 2nd identical normalized
  failure (failure-identity registry fed by `record_episode_failure`) emit a
  `recovery` fact — independent of the search lattice, not starved by covering.
- **F5. signature_delta.** Feed the CALLS-edge caller query
  (runtime_observation.py:480) into the edit path; any signature-changing edit
  with graph callers → `signature_mismatch`. Plus F2's `caller_break` map.
- **F6. newfile_precedent.** Keep the edit-trigger create path + missing_role
  half (needs F2 map) + `issue_text` already threaded; verify
  `detect_change_surface` availability.
- **F7. syntax_result.** Fire on file-creation (new .py → syntax confirmation)
  alongside ERROR; keep dropping zero-gain OK on edits.
- **F8. Verify only (already wired):** obligations, localization, submit_refusal.

## Part 2 — PROVE-IT visibility harness (fast, no provider)

Extend `tests/test_engine_force_17.py` through `engine_execute_actions` (the
`test_engine_interface.py` pattern) for all 16 triggers; each test asserts:
**fired + `_valid_fact_payload` (scope+shape+fresh) + `model_visible` +
correct-time** (fact in the SAME rendered observation as its trigger,
pre-next-call). `scripts/engine_visibility.py` → 16-row matrix
`feature | fired | payload_valid | fresh | correct_time`.

## Part 3 — Provider-free GHA green

Full battery + census + visibility matrix + validator `ok:true`.

## Part 4 — ONE smoke (existing 10 tasks)

Run once, measure L2/L3 + `first_acted_index` + rewards; delta descriptive.
No further smokes.

## Definition of done

All 16 non-REMOVE features appear in the visibility matrix as
**fired + payload_valid + fresh + correct_time**; provider-free green; then the
single smoke. `caller_contract` documented REMOVE.
