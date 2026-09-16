# Offline ability audit — obligations / persistent plan / submit / receipt (D4, D7)

- **Date:** 2026-09-15 (local run, this working tree)
- **Branch:** `codex/phase5-harness-proof` @ `5fa957be` (shared worktree, no commits made)
- **Scope:** `obligations`, `persistent_plan`, `plan_gate`, `submit_refusal`,
  `GT_SS_SUBMIT_RED`, `GT_CERT_DELIVERY` — exercised end to end on real objects,
  offline, provider-free.
- **Evidence:** `tests/test_audit_plan_submit.py` — 34 tests, all passing.
- **Companion runs:** `tests/test_persistent_plan*.py`, `test_persistent_plan_gate.py`,
  `test_feature_accounting.py`, `test_attest_deepswe.py` — all green
  (296 tests, 2 expected Linux-boundary skips).
- **Defect found and fixed:** one cross-boundary defect in the uncommitted
  `runtime_observation.py` E1/E2 rewrite — see *Confirmed defect* below.

## Method

Every step runs the shipped producer, not a mock of it:

`extract_task_contract` → `build_requirement_ledger` → `merged_plan_contract` →
`compile_obligation_predicates` → `MiniSweAdapter` (bind/drain/seal/note_edit) →
`GTSession.plan_submit_gate` → `miniswe_runtime._run_submit_gate` →
`GTSession.suppress` → `scripts.feature_accounting.account` →
`gt_harness.runtime_receipts.issue_runtime_receipt_failure` →
`scripts.attest_deepswe.attest_deepswe`.

The only canned boundary is `_StubEnv` — the task's isolation environment. The
drain and seal only ever call `execution_env()` and `execute()` on it; the stub
supplies the verdict, the environment identity, and capture completeness. The
journal chain is verified with `verify_event_journal` after every ability run.

The audit prompt is a fixed unfamiliar-task shape (`AUDIT_PROMPT`): a `##`
heading, a fenced example, a numbered requirement block containing a negation,
a packed line carrying two requirements, and a `**Background**` non-normative
section. No row names a test, so nothing can be proven by leakage.

## Ability verdicts

| Ability | Verdict | Evidence |
|---|---|---|
| obligations | **WORKING OFFLINE** | `TestObligations` — headings mint no rows; fenced example attaches to its row; negation survives verbatim; packed line links two obligations to one row; `merged_plan_contract` mints `plan-*` obligations for ledger-only rows; every obligation compiles to a typed predicate; failed/unrelated/uncollected evidence discharges nothing. |
| persistent_plan | **WORKING OFFLINE** | `TestPersistentPlanD4` — delayed init after early edit, incomplete bootstrap (no contract), restored plan, unmapped rows, stale checks, phantom-payload rejection, deterministic floor preserved. |
| plan_gate | **WORKING OFFLINE** | `TestSealAndGate` — drains pending checks before deciding, refuses on unmet rows, names repair commands in the directive, escapes on budget, concedes after 3 stalled refusals, refuses on baseline regression even when rows pass. |
| submit_refusal | **WORKING OFFLINE** | Pre-execution refusal via `_run_submit_gate` returns the lifecycle to IMPLEMENT; `session.suppress` writes `action_suppressed` with `executed=False`; enforced `submit_decision` refuses once then concedes without minting `verified`. |
| GT_SS_SUBMIT_RED | **WORKING OFFLINE** | Alias of `submit_refusal` in `account()`; DELIVERED on a real suppression row, STARVED on an eligible-but-undelivered one, BOUNDARY_UNKNOWN when no submit witness exists. |
| GT_CERT_DELIVERY | **WORKING OFFLINE** | Same alias path; the D7 test proves a product receipt failure neither erases nor manufactures the official verifier outcome. |

## Findings

### Confirmed defect — quoted program words were masked off the observation surface

**Status: FIXED at the owning layer, regression-covered.**

The uncommitted E1/E2 rewrite of `gt_engine/runtime_observation.py` added
`_unquoted_command_surface()`, which replaces every quoted span with an
opaque `Q<digest>` placeholder so a `;` inside a quoted payload cannot mint
a boundary the shell never parsed (correct protection). The defect: the
mask also applied to the quoted span in **program position** — the
executable the segment runs. `"C:\...\python.exe" -B -m unittest t` —
the shape every test that invokes `sys.executable` takes, and standard CI
quoting style — became `"Qee94c6a2" -B -m unittest t`. The runner parser
could not identify the interpreter, `_locate_runner` found nothing, and the
pinned wheel's fallback grammar received the masked surface, so
`classify_test_observation` returned `('', '')`, `kind` stayed empty, and
`compile_execution_evidence` returned `None` — not an `unknown` outcome but
no evidence at all. Consequence: `evaluate_observation` returned `()` and
**obligation predicates could never discharge GREEN** through a
quoted-interpreter run — semantic proof silently disabled on that command
shape.

Confirmed by six red tracked tests (`test_obligation_reverify.py` ×5,
`test_dependency_verification.py` ×1), each `assert () == ('pred-…',)`.
Not Windows-specific: `sys.executable` is quoted on Linux too.

**Fix** (same owning file, `gt_engine/runtime_observation.py`):

- `_unquoted_command_surface` now tracks program position (segment head,
  after `;|&\n(`/`$(`/backtick, skipping env assignments, wrapper flags, and
  redirection words). A quoted program word is emitted as a
  metacharacter-free literal (shell syntax chars and whitespace → `_`,
  `\` → `/`), so the basename stays readable while `"x;nox"` still cannot
  mint a `;nox` boundary. Argument payloads keep the `Q<digest>` mask;
  `VAR="a;pytest"` values stay masked.
- `_runner_basename()` strips a Windows executable suffix
  (`.exe/.bat/.cmd/.com`) at the runner-resolution sites in
  `_runner_call`/`_strip_wrappers`, so a quoted `python.exe` locates through
  the parser instead of relying on the wheel's raw-text tolerance.

**Regression:** `TestObservationSurface` (7 tests) in
`tests/test_audit_plan_submit.py` — quoted interpreter/runner visible,
interpreter flags before `-m` still resolve while `-c`/script paths stay
conservative, assignment value still masked, quoted payload still cannot
mint a boundary, metachar program name sanitizes (`"x;nox"` → `x_nox`), and
`evaluate_observation` discharges the semantic predicate through the quoted
interpreter end to end.

### Other observations (no defect)

1. **Delayed plan init forfeits the predicate channel, not the plan.**
   `register_plan_predicates` is legal only at `workspace_epoch == 0`; after an
   early edit it returns 0 and leaves `plan_row_predicates` empty. Rows then
   carry no predicate mapping, but bound checks still prove them — the check
   channel is the fallback and it works. Documented design, exercised.

2. **Staleness is measured against `repository_revision`, which is only as
   current as the last recorded snapshot.** The runtime records a snapshot at
   every action boundary, so in production the ledger is always live; the audit
   test mirrors that by snapshotting after each direct edit. A write that
   bypassed the action seam entirely would be unobservable to GT for every
   purpose, not only staleness — consistent, not a defect.

3. **`restore_plan` is a startup path.** On a live store whose journal was
   written after construction, `startup_plan_events` is empty and restore
   returns `None` without a rejection row. Correct: recovery replays only at
   startup, and the resumed adapter sees the checkpoint. Rejection IS journaled
   when the resumed store is asked for a wrong-task or missing-blob checkpoint.

4. **Advisory submit with active RED is journaled `enforced=False` and reads
   STARVED in the census.** The capability was reachable but not armed by mode;
   the accounting honestly says so rather than claiming a delivery.

5. **`verified=True` is honestly reachable and honestly gated.** The seal test
   reaches it only when every predicate is GREEN at the current epoch AND every
   plan row is CHECK_PASSED/PROVEN on the submitted tree; the concession tests
   prove a submitted task with unmet rows keeps `verified=False`.

## Layer-4 mutation coverage

`TestLayer4Mutations` corrupts one boundary per test and requires the ability
evidence to go red:

- Executor reports failure → `CHECK_FAILED`, never a pass.
- `capture_complete=False` → `UNVERIFIED`, never a pass.
- Declared environment ≠ executed environment → `UNVERIFIED`.
- Checkpoint blob bytes tampered → restore rejected, journaled.
- `action_suppressed` with a non-`submit_refused` reason → census reads
  `STARVED`, not `DELIVERED`.

A green audit verdict under any of these mutations would itself be a proof
defect; none occurred.

## D7 receipt separation (producer-level)

`test_receipt_failure_after_valid_verifier_success` drives the real
`issue_runtime_receipt_failure` over the attestation fixture's completed-task
artifacts:

- ERROR product receipt preserves `terminal="submitted_unverified"`,
  `exit_code=0`, `effective_model` recovered from `report["gt"]["resolved_model"]`.
- `verify_runtime_receipt` flags `product_not_completed`.
- `attest_deepswe` → `FAIL` with `product_receipt:<task>:*` errors while the
  official outcome stays `GRADED`, `solved=True`, `reward=1`.

The valid verifier solve is neither erased by nor converted into product
receipt health — the separation the product contract requires.

## What this audit does NOT claim

- No provider benchmark was run; no paid path was exercised.
- `verified=True` was demonstrated on a small repo whose behavior obligations
  all carry bound-check evidence. On a real task, `behavior` predicates also
  need `verify_live_submit` live assertions or exact operator/literal matches —
  that channel was exercised only through its negative cases here.
- The frozen GT-off baselines (TB2 66/89, DeepSWE 4/10) were not re-derived;
  they are referenced, not re-run.
- Component and installed-path evidence remain separate from any future paid
  result; this audit closes the offline plan→submit→receipt slice only.

## Canonical-suite attribution (shared worktree)

`python -m pytest -q -ra tests` was run over the whole suite twice.

**First run — 13 failures** (none in this audit's file):

| Failure | Cause |
|---|---|
| `test_obligation_reverify.py` ×5, `test_dependency_verification.py` ×1 | The confirmed defect above — `evaluate_observation` returned `()` under a quoted interpreter. **Fixed**; all six pass on re-run. |
| `test_baseline_classification.py::test_command_scope_shape[cargo test-unknown]`, `[pytest -q && echo done-unknown]` | Mid-run edit race: the parametrized table was updated (`unknown`→`suite`, E1) by concurrent work on this shared worktree while the suite was in flight. Both pass on re-run. |
| `test_product_acceptance.py` ×2 (`source_closure_differs_from_head`) | Environmental: the bundle reproducibility check requires the source closure to match `HEAD`; this worktree carries uncommitted changes from several audit threads, so it fails by design on a dirty tree. Expected to pass on a clean checkout. |
| `test_failure_id_validator.py::test_repository_snapshot_and_ci_are_wired` | Environmental: the validator scans the repo root and a leftover `.tmp/audit21-2026-09-15/` run-artifact tree (≈35k files) contains JSON that trips `malformed_definitions`. Not a product defect; resolves on a clean checkout or artifact cleanup. |
| `test_audit_graph_lifecycle.py` ×2 | Another audit thread's untracked file; both passed on the re-run (the owner thread's in-flight work landed mid-run). |

**Second run (after the observation-surface fix) — 3 failures, all
environmental:** the two `test_product_acceptance.py` closure checks (dirty
worktree vs `HEAD`) and `test_repository_snapshot_and_ci_are_wired`
(`.tmp` artifact scan pollution). Everything else passed — including all
six tests the defect had broken and both earlier lifecycle failures.

## Reproduce

```text
python -m pytest tests/test_audit_plan_submit.py -q -ra
```

34 tests, all passing at `5fa957be` + working-tree fixes.
