# Mini-SWE ↔ Canonical GT — integration design

_Status: design — 2026-09-24. Pins the verified canonical head `canonical/gt-har90` @ `ea8889ae` (R5 PASS + delta @ `8c5d6ae3`; R6/R6b PASS @ `ea8889ae`). Producer: binary `d4655bcc…`, source `1e83ea68`, wheel `658cad06…` — all Route-B certified. Nothing in this document proposes modifying certified producer artifacts._

This is the integration design the HAR-90 canonical work gated on. It defines how Mini-SWE (the pinned `minisweagent` 2.4.6 loop, driven through the Harbor/Pier installed-agent surface) consumes the verified canonical GT implementation — what is already wired, what remains to build, the honest capability posture per feature, and the phase gates with their verification requirements.

---

## 1. What already exists (verified, not proposed)

The canonical implementation is not a library waiting for a consumer — it is already integrated along one axis. The verified state:

| Layer | Status today |
|---|---|
| Certified bundle staging | `MiniSweAgent.install()` uploads the digest-pinned wheel, producer binary, harness wheel, uv installer, Python archive, wheelhouse, dense model dir, and LSP bin dir. Missing mandatory assets are **setup errors**, not quiet downgrades (`_lsp_bin_host`, `_dense_model_host` raise `FileNotFoundError`). |
| Runner chain | `pier run --agent-import-path eval.pier_gt_harness_adapter:…` → `MiniSweGtAgent.run` → `scripts/miniswe_supervisor.py` (deadline + abort polling) → `scripts/miniswe_gt_run.py main` → `build_agent` → `BoundedHistoryAgent`. |
| Model surface | `GroundTruthLitellmModel` (subclass of `LitellmModel`) exposes exactly one extra tool: `groundtruth` with `kind ∈ CERTIFIED_TYPED_KINDS` (16 kinds) + `arguments` + `requested_fidelity`. Alias spellings (`find_definition` etc.) normalize to canonical kinds. |
| Typed execution | `build_action_request` → `execute_typed_action` → vendored `groundtruth.runtime.deterministic_queries`, shared by the tool path and all 31 host-side facades. Harness-only args (today: `sanitizers` on `taint`) are stripped before wheel arg validation. |
| Loop hooks | `install_runtime_hooks` wraps `prepare_messages` (context compile, supersession/demotion, decision-packet admission), `query_transport` (wire-level directives), `query` (one-time bootstrap select-catalog + persistent-plan), `execute_actions` (typed dispatch, submit gate, snapshot→diff→EditTransaction→amend→republish, evidence classification, churn governor). |
| Delivery | Compiled path (default): `admit_decision_packet` + `admit_model_visible_delivery` append ≤4 units/request (≤1,400–2,000 B/unit, ≤9,600 B/request) to the last message. Legacy system-prompt block exists behind `GT_LEGACY_MODEL_VISIBLE=1` (never set in practice). |
| Freshness | `EngineState` owns source/graph revisions; edits mark graph unavailable; synchronous batch amend republishes (copy-of-parent; uncertifiable parents refuse by name → clean rebuild). Goldens pin `working_tree_sha256`/`graph_revision` on every answer. |
| Modes | `GTMode` ladder: `off` / `shadow` / `advisory` / `assistive` / `enforced`. Only `enforced` may block a baseline action. Benchmarks ran `advisory`. |
| Isolation | `--gt-off` runs the stock `LitellmModel` — no GT imports reach the runner (`validate_gt_off_control` proves zero GT hook events). `GT_KILL_SWITCH` forces off globally. Exit codes are typed (`TERMINAL_EXIT_CODES`); a timeout is a gradeable result, not an infra fault. |

**So the design problem is not "how to connect GT to Mini-SWE" — the connection exists and is verified.** The design problem is: which of the 60 canonical capabilities should be *model-facing*, which host-side-only, through what surfaces, with what honesty guarantees, and in what rollout order.

---

## 2. Integration surfaces

There are exactly three channels through which GT can reach the Mini-SWE loop. The design assigns every canonical capability to one.

### S1 — Typed tool calls (model-initiated)

The `groundtruth` tool. The model explicitly selects a kind; the answer comes back as a tool result. Today: **16 certified kinds**, all `MODEL_FACING` in the matrix.

Properties the design must preserve:
- **Explicit selection only** — `parse_groundtruth_toolcalls` enters the typed router only when the model names `groundtruth`; nothing silently upgrades a bash command.
- **Bounded wire** — `QUERY_RESULT_MAX_BYTES = 16 KiB`, `QUERY_LINE_MAX_BYTES = 256`, `QUERY_MATCH_LIMIT = 20`.
- **Honesty envelope** — every answer carries `omissions`, `limitations`, `semantics`, `graph_revision`, `source_revision`, `fresh`. Partial is a status, not an exception.
- **Determinism** — same request + same revisions → same answer bytes (goldens enforce).

### S2 — Automatic admission (host-initiated, model-visible)

The sealed delivery lane: evidence computed host-side, admitted into the provider request under token budgets. Today: `hybrid_rank` (gateway_auto:localization), `last_test_result`/`verification_state` (execution evidence), `repeated_failure_state` (steer), `select_catalog`, `reactive_syntax`.

Properties:
- Centralized admission only — `GTSession.admit_decision_packet` / `MiniSweAdapter.admit_model_visible_delivery`. **No facade may emit model-visible text directly** (§B invariant).
- Ordering: failure > obligation > localization > other > cochange.
- Fail-open: stale, duplicate, late, over-budget, ungrounded frames abstain.

### S3 — Host-internal state (never model-visible directly)

Facades and runtime components consumed by the harness itself: freshness state (`index_revision`, `graph_state`, `amend_state`, `fallback_state`, `unit_state`), `edit_transaction`, `affected_tests`, `covering_tests`, `failure_fingerprint`, `communities`, `callable_values`, `cfg`/`reaching_definitions`/`control_dependence`, plus the 13 runtime components (`task_contract`, `persistent_plan`, `plan_gate`, `churn_governor`, `submit_finalization`, `history_supersession`, `drift_relocalization`, `recovery_suspension`, `cochange_priors`, `context_admission`, `incremental_amend`).

These power the S2 producers and the lifecycle machinery; they must never leak into the model surface except through admission.

---

## 3. Per-capability integration plan

The 60-entry matrix sorted by integration posture. **Nothing new becomes model-facing without a named evidence gate.**

### Already model-facing — keep, no change (16 kinds + 5 components)

| Surface | Entries | Notes |
|---|---|---|
| S1 | all 16 `kind.*` | `taint` now carries Python statement-level dataflow (def-use over resolved callsites; named omissions). `sanitizers` is harness-only and flows via `harness_args`. |
| S2 | `hybrid_rank`, `last_test_result`, `repeated_failure_state`, `verification_state`, `select_catalog`, `reactive_syntax` | Automatic lanes already bounded and receipted. |

### Host-side only — keep as engine substrate (39 entries)

Freshness (5), analysis engines (`cfg`, `reaching_definitions`, `control_dependence`, `callable_values`), change internals (`edit_transaction`, `affected_tests`, `covering_tests`), `failure_fingerprint`, `communities`, and the 13 runtime components. These are correctly non-model-facing: they feed S2 producers, receipts, and the lifecycle.

### Candidates for promotion — with gates

| Candidate | Surface | Gate before promotion |
|---|---|---|
| `structure.communities` | S2 (localization lane) | Measured p50 1ms; cheap. Needs: a demonstrated decision benefit (does community context change model behavior on real tasks?) — evaluate as SHADOW delivery on a frozen task subset, compare action traces with/without. |
| `analysis.cfg` / `reaching_definitions` / `control_dependence` | S1 (new kinds) or S2 | Currently facades only, consumed by `taint_dataflow`. Promoting them model-facing means new certified kinds — requires wheel-level `deterministic_queries` kinds or a harness-side kind with the same honesty envelope. **Not in scope for phase 1**; the facades suffice for host-side consumers. |
| `change.affected_tests` / `runtime.covering_tests` | S2 (post-edit lane) | Natural fit: after an edit transaction commits, admit the affected-test list alongside `reactive_syntax` evidence. Gate: no regression in per-request byte budget (admission cap already enforced); pin one canonical test showing the unit lands under `edit` freshness. |
| `runtime.failure_fingerprint` | S2 (steer lane) | Already computed for `repeated_failure_state`; exposing the fingerprint itself is redundant — keep host-side. |

**Design rule:** promotion candidates must first run as `SHADOW` (computed + journaled, zero model-visible bytes) so the receipt proves what *would* have been delivered, then graduate to `ADVISORY` only with a demonstrated trace-level effect.

---

## 4. Lifecycle contract (what the integration guarantees)

1. **Task start**: `record_repository_snapshot(boundary="task_start")` binds the source revision before anything else — before the index submission, before the agent's first edit. The initial index builds on a daemon thread (`_StartupIndex`); the adapter starts *graphless* and publishes through the engine-state revision gate when the build lands. A graph that loses the race to early edits becomes the amend parent, never current.
2. **Baseline probe** (persistent plan enabled): synchronous, bounded (`3%` of wall, clamped 20–120 s). Its writes complete before the snapshot binds.
3. **Per provider request**: `prepare_messages` → supersession/demotion → `admit_decision_packet` → `admit_model_visible_delivery`. Bounded slices only.
4. **Per model action**: typed `groundtruth` call → `execute_typed_action` (S1). Bash → submit-gate check → pre-snapshot → execute → post-snapshot → `diff_workspace` → `EditTransaction` → `apply_transaction` + synchronous amend → republish → evidence classification → queued for next admission.
5. **Freshness is enforced structurally**: a source edit marks graph state unavailable; no frame is served until the refreshed graph is complete and revision-current. `unit_state`/`is_stale` expose per-unit staleness; `source_revision` rides every answer.
6. **Submit**: `plan_submit_gate` pre-exec; terminal classes typed; non-submitted terminals can never report as clean pass.
7. **Failure posture**: adapter failures journal `*_unavailable` events and the loop continues (advisory mechanisms must not prevent native execution). Certified-artifact failures (digest mismatch, missing mandatory asset) are setup errors — loud, pre-run.

---

## 5. What Mini-SWE must provide (the inbound contract)

| Requirement | Where consumed |
|---|---|
| `BaseInstalledAgent` kwargs forwarding | `CLI_FLAGS`/`ENV_VARS` declarations — every treatment knob must be declared or Harbor drops it silently (the C6 defect class, already fixed for the declared set). |
| Model hooks | `install_runtime_hooks` wraps `_prepare_messages_for_api` / `query` / `_query` / `execute_actions` on the *instance* — class lists stay untouched, so GT-off is provably clean. |
| Environment exec | `env.execute` returns `returncode`/output; `CredentialIsolatedLocalEnvironment` adds evidence env + descendant-containment witness. |
| Workspace | `cwd` is the repo root; `RuntimeLayout.resolve` derives state/evidence/graph paths under `state_dir`. |
| Submit marker | parsed from the action stream for the submit gate. |
| `output_path` | trajectory persistence for receipts. |

**New Mini-SWE-side requirements for this design:** none beyond what exists — the integration consumes the current `DefaultAgent`/`LitellmModel`/`LocalEnvironment` seam set unchanged.

---

## 6. Configuration surface

| Knob | Channel | Effect |
|---|---|---|
| `--gt-mode` / `gt_off` | run arg | `off` → stock loop, no GT imports. `shadow`/`advisory`/`assistive`/`enforced` → the ladder. |
| `GT_KILL_SWITCH` | env | global force-off, wins over everything. |
| `GT_RL_PROFILE` / `apply_profile_env` | internal | fans out the Profile-2 producer flags (gateway, verify-execute, edit-check, patch-delta …). Never from container env — the model can read `env`. |
| `GT_SUBMIT_SUPPRESSION_ENFORCE`, `GT_VERIFY_EXECUTE`, `GT_PERSISTENT_PLAN` | `setdefault` in `build_agent` | enforcement arms; explicit `0` still wins. |
| Treatment knobs | `CLI_FLAGS` (declared) | `integration_mode`, `policy_mode`, `enable_*`, budgets — all declared since C6. |
| `MINISWE_AGENT_VERSION` | env | closed set `{2.4.6}` — the A/B treatment is activation, not package drift. |

---

## 7. Honesty & certification invariants (non-negotiable in integration)

1. **Certified artifacts only** — the bundle is digest-pinned end to end (wheel `658cad06…`, binary `d4655bcc…`, uv installer, Python archive). A mismatch raises at staging, not mid-run.
2. **No fabricated completeness** — `partial`/`abstain`/`unavailable`/`error` statuses propagate to the wire; `omissions` ride every answer; the `statement_level_dataflow_unavailable` label is only dropped when analysis actually ran.
3. **A/B identity** — GT-off must be *provably* GT-free (`validate_gt_off_control`, zero hook events); GT-on differences come only from activation, never from scaffold/package drift.
4. **Determinism** — canonical goldens pin answer bytes + revisions; the R6 lesson is now structural (stray-check rejects `*.db`/`*.sqlite*` under fixtures).
5. **Timeouts are results** — exit-3 absorbance keeps a budget-exhausted run gradable; all other non-zero codes re-raise.
6. **Boundedness** — every model-visible path has a byte cap; every analysis has iteration/path caps; every async lane has a deadline.

---

## 8. Rollout phases

| Phase | Content | Exit gate |
|---|---|---|
| **0 — done** | Canonical implementation verified @ `ea8889ae`; bundle staging; S1 16 kinds; S2 lanes; lifecycle. | R6b VERIFIED PASS. |
| **1 — integration hardening** | `affected_tests` post-edit S2 lane (SHADOW first); `communities` SHADOW probe; regression-check the admission budget with the new candidate units on the canonical fixture. | Canonical suite stays 103/103 + one new golden per promoted unit; receipts show shadow-delivery decisions. |
| **2 — evidence** | Frozen matched A/B against the existing GT-off baselines (`D:\gt_runs\miniswe_tb2_gtoff_20260731` TB2-89, DeepSWE-10 control). Never run GT-off locally — use frozen/leaderboard artifacts per policy. | Official workflow runs only; no paid run authorized from a working tree. |
| **3 — graduation** | Promote SHADOW-proven units to ADVISORY; only `ENFORCED` mechanisms may ever block — none proposed. | Mode-ladder evidence per unit. |

Explicitly **not** in this design: new wheel kinds (producer feature), GT-off runs, any provider/paid execution, any claim of solve-rate benefit (none exists yet).

---

## 9. Open risks

- **Partial semantics are the norm, not the exception** — 14 of 16 kinds are `partial`. The model sees honest `omissions`; the integration must never polish these into implied completeness in prompts or docs.
- **Non-Python taint** is symbol-level only — `dataflow_python_only:<file>` must stay visible to the model.
- **Admission budget contention** — every new S2 unit competes for the same ≤4 units / ≤9,600 B per request. Promotion order matters; failure-evidence keeps top priority.
- **Linear-side record** — the design lands here in-repo; a HAR-90 comment summarizing it is pending MCP stability.

---

## 10. Verification checklist for any integration change

1. `tests/canonical` 103/103 + `tests/test_typed_graph_real_producer.py` 19/19 on the certified binary.
2. Golden diffs reviewed by hand before re-mint; re-mint only on a clean tree (the stray-check enforces).
3. GT-off control validation after any hook-surface change.
4. `verify_producer_binding` → VERIFIED after any manifest/vendor touch.
5. Section re-render after any registry/capability change; Linear pin updated.
6. Independent delta verification before claiming a new integration head.
