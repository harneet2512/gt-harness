# Offline ability audit — runtime evidence lanes (§3.A, §3.B, D2, D3)

- **Date:** 2026-09-15 (local run, this working tree)
- **Branch:** `codex/phase5-harness-proof` @ `5fa957be` (shared worktree, no commits made)
- **Scope:** `covering_red`, `recovery` / `GT_HYPOTHESIS`, `syntax_result` /
  `GT_EDIT_CHECK`, command/observation variations (spec §3.A),
  repository/verification-scope variations through runtime classification
  (spec §3.B), scenarios D2 and D3, plus the six required RED cases.
- **Evidence:** `tests/test_audit_runtime.py` — 26 tests (24 passing,
  2 `xfail(strict=True)` documenting confirmed cross-boundary defects).
- **Method:** every case runs the real shipping path — `execute_actions` →
  `compile_execution_evidence` → `SuiteVerdictLedger` → real provider
  admission/`bind_provider_payload` through `TransportFakeModel`. Journal
  chains are verified with `verify_event_journal` after each lane. The only
  canned boundary is `FakeEnv` (the task's isolation environment).
- **Companion document:** `artifacts/audit/plan-submit-report.md` (D4/D7 lane).

## Ability verdicts

| Ability / lane | Verdict | Evidence |
|---|---|---|
| covering_red (failing test linked to edited file) | **FIXED — WORKING OFFLINE** | `test_vendor_path_is_not_attributed_to_edited_surface`, `test_direct_edited_path_is_still_attributed`, `test_attribution_follows_cd_prefix_paths` — `attribute_test_failure` now compares normalized repo-relative paths for exact equality. |
| recovery / GT_HYPOTHESIS | **WORKING OFFLINE** | `test_recovery_steer_reaches_wire_through_real_admission` — fail → edit → same fail schedules `GT_RECOVERY`; the steer is staged by `query_transport` and committed by `bind_provider_payload` on the exact request bytes. |
| syntax_result / GT_EDIT_CHECK | **WORKING OFFLINE** (pre-existing lane, unchanged) | covered by `tests/test_miniswe_runtime.py` (`test_syntax_probe_catches_broken_edit` family) — green. |
| §3.A command shapes (pipes, redirects, `cd` prefixes, env assignments, `timeout`, wrappers) | **WORKING OFFLINE** | `test_command_scope_shape` parametrize (56 shapes) + command corpus fixture `tests/fixtures/command_corpus/model_test_commands.json`; `test_piped_pytest_still_produces_test_evidence` is the green positive control. |
| §3.B scope classification (`suite`/`scoped`/`unknown`, multi-family runners) | **WORKING OFFLINE** | cargo/go/node/make/jest/vitest/mocha families now classify; `cargo test` with no selection is `suite`, `make lint`/`python analyze.py` companions keep `unknown`. |
| D2 (missing vs captured baseline) | **WORKING OFFLINE** | `basis="suite_observed"` lane in `SuiteVerdictLedger` + baseline-classification tests — missing baseline no longer disables suite-observation classification. |
| D3 (recovery delivery through real admission) | **WORKING OFFLINE** | recovery test above exercises admission → transport staging → payload binding, not a mock of it. |
| `uv run --project … pytest` / `uvx pytest` boundary | **FIXED — WORKING OFFLINE** | `test_uv_project_wrapper_preserves_test_evidence` (4 parametrize forms), `test_uv_project_wrapper_pass_outcome_is_classified`, `test_uv_project_wrapper_failure_reaches_baseline_classification`. |
| Whole-suite-green promotion | **PARTIAL** | fully-named green suite now opens the advisory (`test_fully_named_green_suite_marks_whole_suite_state`); partial-inventory overclaim is a confirmed UNFIXED defect (below). |
| Delivery accounting (prepared vs exposed) | **PARTIAL** | unbound prepared candidates write no delivery row and are not committed (`test_prepared_delivery_without_binding_writes_no_delivery_row`); audit-level `feature_accounting` still credits prepared-only identities — confirmed UNFIXED defect (below). |

## Required RED cases — disposition

| # | Case | Disposition |
|---|---|---|
| 1 | Commit-message text mistaken for verification | **FIXED.** `_unquoted_command_surface` (runtime_observation.py:1437) masks quoted spans to `Q<digest>` placeholders before segmentation/classification; `compile_execution_evidence` (2056), `_classify_test_output` (2155), `note_search_drift` and the churn governor (miniswe_runtime.py:2070-2076) all consume the masked surface. `git commit -m 'x;pytest tests/'` mints no test evidence. Tests: `test_quoted_semicolon_in_commit_message_is_not_test_evidence`, `test_double_quoted_and_escaped_commit_text_is_not_test_evidence`, `test_commit_message_flag_value_is_not_suite_scope`, `test_unquoted_commit_message_never_mints_a_pytest_segment`, `test_commit_message_never_mints_evidence_but_real_pytest_does`. |
| 2 | Partial test inventory mistaken for suite coverage | **CONFIRMED, UNFIXED — cross-boundary.** `SuiteVerdictLedger.covers_known_suite` (runtime_observation.py:1628) treats the observed-name universe as complete; `pytest tests/unit/` over a unit-only observed set claims whole-suite truth. The ledger holds no repository-wide test inventory; the fix belongs in the feeder (`miniswe_integration._record_suite_observation`, 3217-3220) which must compare coverage against the baseline command's declared scope or an enumerated repo inventory — outside this task's owned files. Kept as `xfail(strict=True)`; abstention direction is already correct (overclaim, never suppression). |
| 3 | Fully named green suite fails to open the advisory | **FIXED.** `record_suite_run` returned early on the `named_all` branch before setting `_whole_suite_green`. Now `named_all` only bounds name promotion; a qualifying unrestricted green run sets `_stale.clear()` + `_whole_suite_green = True` (runtime_observation.py:1758-1764). Tests: `test_fully_named_green_suite_marks_whole_suite_state`, `test_named_green_scoped_run_still_does_not_claim_suite`, `test_fully_named_suite_with_a_failure_records_verdicts_not_green`. |
| 4 | `uv --project` wrapper loses the test boundary | **FIXED.** `wrapper_stripped_command` (runtime_observation.py:1413) peels `uv run [--project <dir>]`, `uvx`, `npx`, `pnpm exec`, env prefixes and assignments, then hands the runner argv to the canonical classifier via `_classification_command` (1479). `--project <dir>` is preserved as the run's `cwd` coverage hint. Baseline join confirmed: a baseline-passing name failing in a scoped `uv run` surfaces "passed at baseline". |
| 5 | `vendor/src/a.py` attributed to edited `src/a.py` | **FIXED.** `attribute_test_failure` (miniswe_covering.py:144) replaced substring matching with `_output_repo_paths` (114) — every path token in the output is normalized (absolute, repo-relative, `cd`-relative, `file::nodeid`, `file:line[:col]`) and only exact repo-relative equality links a failure. Positive control `test_direct_edited_path_is_still_attributed` holds. |
| 6 | Prepared-only evidence counted as model exposure | **CONFIRMED, UNFIXED — cross-boundary.** `scripts/feature_accounting.account()` resolves an identity to a feature the moment ANY row carries it, including `delivery_prepared` rows never bound to a provider request; `REFUSAL_EVENTS` in `gt_engine/delivery_budget.py:80` does not treat `prepared_deliveries_discarded` as a refusal. Owners: `scripts/feature_accounting.py` + `gt_engine/delivery_budget.py` — outside this task's owned files. Kept as `xfail(strict=True)`. Note: the *runtime* path is already correct — `test_prepared_delivery_without_binding_writes_no_delivery_row` and `test_bound_delivery_is_counted_as_exposure` prove only bound requests commit exposure. The defect is in offline audit accounting, not in delivery itself. |

## Positive control

`test_piped_pytest_still_produces_test_evidence` — `pytest ... 2>&1 | tail -10`
still produces `kind == "test"` evidence classified on the real output bytes:
no false negative, no false whole-suite promotion.

## Test commands and counts

| Command | Result |
|---|---|
| `python -m pytest tests/test_miniswe_runtime.py tests/test_baseline_classification.py tests/test_gt_session.py tests/test_audit_runtime.py -x -q` (required) | **311 collected: 308 passed, 1 skipped (installed Linux producer), 2 xfailed** |
| Affected suite — `test_runtime_observation test_miniswe_covering_syntax test_miniswe_integration test_admission_transactions test_exposure_chain_admission test_delivery_budget test_delivery_payload test_recovery_exposure test_recovery_checkpoint test_capability_matrix test_capability_preservation test_event_journal test_churn_governor test_miniswe_supervisor test_output_evidence test_feature_accounting test_audit_graph_lifecycle test_audit_plan_submit` | **432 collected: 423 passed, 5 skipped (Linux-boundary), 3 xfailed, 1 flaky failure** — `test_audit_graph_lifecycle.py::test_interruption_after_admission_keeps_the_delivered_pin` fails only inside the full batch (`tmp_path` collision in that file's `_adapter` helper); **passes in isolation**. That file is another worker's in-flight, uncommitted test — flagged, not modified. |

Two stale committed expectations in `test_baseline_classification.py` were
updated to the E1 multi-family semantics: `cargo test` → `suite` (cargo is a
runner family now), `pytest -q && echo done` → `suite` (a benign companion
segment does not make a command compound). Genuinely compound shapes
(`; make lint`, `| python analyze.py`, two runner segments, bare
non-runners) still read `unknown`.

## Evidence tiers

- **Source-level:** all verdicts above are source-level offline evidence on
  this working tree (real classes, real journal, real admission seam).
- **Installed-bundle:** not re-proven in this pass — the wheel-identity and
  installed-producer checks remain the release gate's, not this audit's.
- **Full-flow:** recovery (D3) is proven through real transport staging and
  payload binding; submit-window advisory proven through `record_execution_evidence`.
- **Paid:** none — no provider calls were made.

## UNPROVEN / residual unknowns

1. **Partial-inventory whole-suite overclaim** (RED-2) — unfixed, owner
   `miniswe_integration._record_suite_observation` + a repository-wide test
   inventory source. Until repaired, `has_whole_suite_green()` can overclaim
   when a scoped run happens to cover every name observed so far.
2. **Prepared-only delivery accounting** (RED-6) — unfixed, owners
   `scripts/feature_accounting.py` + `gt_engine/delivery_budget.py`.
   Runtime delivery is correct; audit accounting over-credits.
3. **Installed-bundle behavior** of the new command-shape parser — the
   classification seam is harness-side and travels with the bundle, but the
   pinned-wheel + installed-producer combination was not re-run here.
4. **Cross-platform path normalization** in `_output_repo_paths` — exercised
   on Windows paths and POSIX-style output tokens; producer runs on Linux
   were not re-executed (Linux-boundary tests skipped locally).
5. **H5 name-donation bound** — benign companion segments (`cat`, `echo`)
   can stream arbitrary bytes into the captured output; the `kind == "test"`
   gate plus canonical name extraction is the accepted bound, unchanged by
   this audit.

## Paid-smoke authorization

**Not authorized by this audit.** Two confirmed defects remain open
(RED-2 suite overclaim, RED-6 prepared-credit accounting) and both sit
outside the files this task was permitted to change. A paid smoke's verdict
would be interpretable anyway — neither defect manufactures a pass — but the
release gates named in `BENCHMARK_READINESS_STATUS.md` (installed-bundle
identity, official-verifier binding, credential isolation, task-result
conservation, workflow reachability, provider-free clean-container
acceptance) are unchanged by this pass and remain the gating evidence.
