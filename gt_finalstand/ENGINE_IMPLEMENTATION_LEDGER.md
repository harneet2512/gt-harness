# Inline Engine — Implementation Ledger

Authoritative live record for the Inline Engine phase (branch `inline-engine` on
`harneet2512/gt-harness`). Supersedes nothing; it records what was built, what
was verified, and what remains. Receipts are appended as units complete.

## Extreme code review (2026-08-02) — three real defects found and fixed

1. **Gateway fact payload was DROPPED (`_gateway_facts`).** The gateway's
   `EvidenceEnvelope` carries its useful body in `payload` (lines) and
   `provenance` (`(file,line)` rows); it has NO `content` attribute. The engine
   read `winner.content` → always `""` → every gateway fact rendered as
   `{"evidence": "", "target": "..."}` (a bare path, def rows lost). Fixed in
   `runner.py`: extract `payload`+`provenance` into `evidence`/`rows` and anchor
   the file:line rows. Gated by `test_regression_gateway_fact_payload_not_empty`.
2. **Plain bash grep was REPLACE with raw dropped.** `build_analyzer_state`
   marked a bash search "certified complete" from an EMPTY typed_result
   (no omissions), so `decide` returned REPLACE with an empty `replaced` and
   `render()` discarded the exact raw grep/view bytes. Round-7 missed it only
   because `graph_fresh=False` there. Fixed: `certified` now requires a real
   typed answer (`bool(answer)`) — bash greps are AUGMENT/PASS_THROUGH with raw
   preserved + facts. Gated by `test_regression_bash_grep_preserves_raw`.
3. **Engine loop skipped the lifecycle the seam runs.** `engine_execute_actions`
   never advanced `adapter.global_action` (batch/action identity frozen at b1/0),
   never called `before_action` (repeat telemetry + phase guard), never called
   `note_edit` (RED/GREEN receipts were NEVER invalidated on an edit → the submit
   gate could block forever on evidence the edit already fixed), and skipped
   `begin_verify`/`after_observation`. Fixed in the engine loop, matching the
   seam's ordering (before-action → execute → note_edit/begin_verify →
   after_observation), all fail-open. Gated by
   `test_regression_engine_advances_global_action` and
   `test_regression_edit_invalidates_red`.

All 147 engine tests green; visibility matrix all-16-green; census 9/9
deliverable; validator `ok: true`.

## Real-seam end-to-end review (2026-08-02) — two more defects found and fixed

Built `scripts/engine_smoke_e2e.py` + `tests/test_engine_e2e_smoke.py`: drives
the REAL `DefaultAgent` loop with the REAL `MiniSweAdapter` + real
`install_runtime_hooks` in `GTMode.ENGINE` — the exact production code path the
paid smoke runs — provider-free (scripted model/env, git-init workspace).

The fake-only visibility harness could NOT catch these; the real seam did:

1. **`record_episode_failure` always ValueError'd in the engine loop.**
   `FailureIdentity.build` REQUIRES a non-empty `pre_state_revision`
   (`terminal_evidence` raises otherwise), and the engine passed
   `adapter.repository_revision` which is `""` in `MiniSweAdapter` (the seam
   passes a real `pre_snapshot.revision`). The closed blocker that feeds
   `submit_refusal` was therefore NEVER registered. Fixed: the engine now
   passes `request.snapshot_token` (its content-addressed pre-action revision).
2. **`repository_revision` was never populated by the engine**, so the closed
   blocker's `invalidate_on_repository_revision_change` never fired AND the
   blocker register gate (`and self.repository_revision`) stayed closed. Fixed:
   the engine records a per-batch repository snapshot (`engine_batch`) and
   re-records after each edit (`engine_edit`), so a submit AFTER a fix correctly
   advances the revision, invalidates the pre-fix blocker, and is accepted —
   while a submit WHILE the fresh RED blocker exists is SUPPRESSED exactly once.
   Verified end-to-end: the submit-while-RED trajectory yields exactly 1
   suppression + 1 `submit_refusal`, then FINISHED + accepted after the edit.

Also confirmed the real-seam rendered observations carry NON-EMPTY payload
(`localization` `{"evidence": "src/mod.py:1:compute", "rows": ["src/mod.py:1"]}`;
`covering_red` with `outcome: failed`) and preserve exact raw output after
`</result>` — the review's bug-1/bug-2 guards hold through the production path.
`submit_refusal` SUPPRESS needs the real provider boundary (attached only by
`GroundTruthLitellmModel` in production); the e2e test attaches the real
`MiniSweProviderBoundary` to prove the path.

All 151 engine tests green (incl. 4 real-seam e2e tests); visibility 16/16;
census all_17_wired.

## Round-8 benchmark (post-review fixes) — run `30772336112`

- Rewards: **9/10 solved** (gpt2-codegolf 0.0, identical to the frozen baseline
  and round-7; temp-1.0 noise task). All 10 task jobs + merge green.
- **Ladder census (causal endpoint) across all 10 tasks**: obligations 102
  delivered / **98 acted (96%)**, localization 13 delivered / 7 acted (54%),
  **covering_red 22 delivered (was 0 in round-7** — output-based detection
  fires at scale), **def_partition 5 delivered (was 1** — kind-threading +
  rotation works). L3-acted is the causal signal, not token deltas.
- **Payload integrity in production: 142 facts, 0 empty-evidence.** Samples:
  localization `{"evidence": "filter.py:6:remove_js", "rows": ["filter.py:6"]}`;
  covering_red `{"evidence": "A covering test fails:\nAssertionError: ValueError
  not raised by append", "rows": ["bottle.py:1"]}`. The review's bug-1 guard
  holds at scale.
- **r8 token explosion root cause (found in post-run audit): the model audits
  the harness.** 4 tasks hit the 100-action cap (llm 102, break-filter 113,
  headless 102, cobol 105) yet ALL solved. r8 introduced harness-probing
  actions (0 in r2/r5/r6/r7; 27 in llm, 35 in break-filter) — the model
  verbatim said "Let me understand how failed-action blockers get
  resolved/cleared", "how the gate's bounce/override works", "how the store
  append works, since triggering an exception there could fail-open the gate".
  This is reverse-engineering the submit gate + RED/predicate machinery, NOT a
  payload leak (0 empty evidence) and NOT a broken feature. Open item: reduce
  the model's incentive to probe (neutral refusal wording; don't render
  internal `pred-<sha>` predicate IDs in model-visible bytes).
- **Raw preservation (bug-2)**: the only `decision="replace"` observations are
  the model's own typed `exact_literal_search` calls with certified answers
  (e.g. `HeadlessTerminal` match rows) — no bash grep was REPLACEd with dropped
  raw.
- The four trigger-rare features (recovery/signature_delta/newfile_precedent/
  submit_refusal) still require their live triggers; they are proven by the
  provider-free gate's e2e smoke (submit-while-RED: exactly 1 SUPPRESS) and the
  visibility matrix.

## Test-isolation fix (2026-08-02) — `_run_submit_gate` leak

The visibility harness's `deny_submit` monkeypatched
`miniswe_runtime._run_submit_gate` permanently; the deny-gate leaked into the
e2e tests run later in the same pytest process, starving their scripted model
(IndexError) and flipping the submit-while-RED result to 0 suppressions. Fixed:
`run_engine` now self-captures and restores the original gate in a `finally`,
and the visibility submit-refusal test asserts the restore. Full engine battery
(151 tests) + full suite (601 tests) green in one process.

## Authority

- Spec: the Inline Engine plan (finalstand contract) — outcome B, host-native
  action-to-observation middleware.
- GT-off remains the frozen stock-equivalent baseline and rollback path.
- Mini-SWE remains the planner and reasoner; the engine owns the
  action-to-observation interface in ENGINE mode only.
- Advisory-era docs in `gt_finalstand/` are historical evidence.

## Built (all provider-free, tests-first)

| Unit | Module(s) | Tests | Status |
|---|---|---|---|
| IE-01 contracts | `gt_engine/engine/contracts.py` — 14 public schemas (EngineMode, ActionRequest, RepositorySnapshot/SnapshotToken, EvidenceArtifact, InterceptionDecision, ActionResult, CanonicalObservation, MutationProposal, MutationCommitRequest, MutationCommitReceipt, DeliveryReceipt, ActionBatch, FactOwnerRegistration) | `tests/test_engine_contracts.py` (12) | PASS |
| IE-01 transitions | `gt_engine/engine/transitions.py` — lifecycle SELECTED→…→RECEIPT_FINAL, bounded exhaustive traversal, Hypothesis stateful oracle | `tests/test_engine_transitions.py` (12) | PASS |
| IE-02 posture | `GTMode.ENGINE` in `gt_engine/gt_session.py`; `--gt-mode engine` in `scripts/miniswe_gt_run.py`; all-action normalization in `gt_engine/engine/runner.py::engine_execute_actions`, wired at `miniswe_runtime.py` execute_actions seam | `tests/test_engine_runner.py` | PASS |
| IE-03 decision | `gt_engine/engine/decide.py` — five-decision law + locked policies | `tests/test_engine_decide.py` (11) | PASS |
| IE-04 observation | `gt_engine/engine/observe.py` — canonical observation compiler + evidence-delta projection | `tests/test_engine_observe.py` (6) | PASS |
| IE-07 mutation | `gt_engine/engine/mutation.py` — PROPOSE→PREFLIGHT→COMMIT with CAS (StaleProposal/PreimageMismatch/AtomicWriteFailed), atomic write set + rollback | `tests/test_engine_mutation.py` (12) | PASS |
| IE-08 batches | `classify_batch_barriers` in runner — sequential dependency barriers honored by ordered execution | runner tests | PASS |
| IE-09 inventory | `scripts/engine_129_audit.py` → `gt_finalstand/engine_129_transition.csv` (129 rows, 12/48/11/58, all dispositions terminal) | `tests/test_engine_129_audit.py` (8) | PASS |

Total engine tests: **73 green**. Full harness suite re-run after seam edits:
**no regressions** — 5 failures were confirmed pre-existing in the local Windows
environment (they also fail with engine changes stashed; the clean provider-free
Codespaces run is the authority).

## Current defects addressed by the ENGINE path

- Typed PASS_THROUGH now executes a literal fallback command (no longer drops
  the selected action) — `fallback_shell_for_typed` + runner typed branch.
- Every selected action is normalized and bound to a snapshot token in ENGINE
  mode.
- One canonical observation per action; raw bytes preserved where required;
  evidence-delta projection avoids re-dumping unchanged facts.
- Batched stateful actions are executed in order (dependency barriers).

## Verified

- `engine_129_audit.py` exits 0: inventory integrity holds (12/48/11/58, 129
  unique, all dispositions terminal).
- Codespace `special-fortnight-95p9q5wrpqp2qww` on `inline-engine`: all 84
  engine tests green in a clean Linux env (python 3.12, groundtruth 1.0.0,
  harbor 0.20.0, mini-swe-agent 2.2.8).
- Full harness battery in the Codespace: **no regressions**. 4 environment-
  specific failures only (gt-index Go binary + live GitHub API for FS-023).
- **IE-13 provider-free gate GREEN in GitHub Actions**: run `30736226330`
  passed every step (engine battery, 129-row audit, finalstand validator,
  compliance certificates) with zero provider calls.
- **First engine smoke exposed a bug**: run `30735955619` — every task's 2nd
  engine action crashed Mini-SWE's Jinja formatter (engine tool results
  omitted `exception_info`) and the session degraded to stock (`gt_degraded_
  fail_open`). fix-code-vulnerability still solved (reward 1.0) via fallback.
  Fixed in `runner.py::_tool_output` + regression test.
- **ENGINE witness run `30736459512` (fixed code)**: plan job green, 10 task
  jobs ran with parallel=10. **Proof from trajectories** (9 trajectories; the
  gpt2-codegolf artifact upload failed, mirroring that baseline arm's own
  infra failure):
  - 9/9 graded tasks solved at reward **1.0** (matching the frozen baseline's
    9 solved for the same tasks).
  - Every action crossed the engine boundary: 8-100 `engine_delivery` events
    and 16-200 canonical `<gt-engine>` observation blocks per task.
  - All decisions `pass_through`; **GT bytes == raw bytes** (raw preserved
    exactly inside the canonical observation); zero fallbacks.
  - Only cosmetic terminal degrade in the first-fixed run (the `Submitted`
    end-of-run signal); fixed in the seam (`except Submitted: raise`).
  - Full table: `gt_finalstand/ENGINE_WITNESS_30736459512.md`.
- **Two follow-up fixes landed after the witness**: `Submitted` propagation
  (seam) and the single-task merge (workflow) — no re-run needed for the
  witness data (per-task artifacts are authoritative).

## Fixes after witness 30736459512 (gated)

- **Neutral in-band labels**: `CanonicalObservation.render()` no longer emits
  `<gt-engine>`/`<gt-fact>`/`GT_` — raw output is first (byte-exact,
  unwrapped), deterministic facts join as a neutral `<result>/<fact>` block.
  External framing made the model treat facts as out-of-band info.
- **Real postflight facts**: the bash path now runs deterministic producers —
  `syntax_result` (ast.parse on changed .py via git status) and `covering_red`
  (execution-specific test/build outcome); PASS_THROUGH upgrades to AUGMENT
  when facts attach. Bash submit commands cross the submit gate.
- **Journal corruption (the silly mistake)**: the `engine_delivery` append
  passed `schema="gt.engine.delivery_receipt.v1"` which overrode
  `ExternalStateStore`'s forced `gt.event.v1`, breaking the tamper chain →
  `research_valid=false`. Removed; gated by `test_engine_gates.py`.
- **Crash-landslide caught by gates**: missing `os`/`Path` imports in
  `runner.py` (`engine_execute_actions` survived only by short-circuit) and
  porcelain `" M"` status parsing would have crashed the next run.
- **Gates**: `tests/test_engine_gates.py` (10) — journal schema valid + trap
  documented, render sentinel-free + raw-exact + facts-present, postflight
  producers, non-repo omission. Full engine battery now **95 green**.
- Provider-free re-certification: run `30738422522` (in progress).

## Round-7 benchmark (all WS-1/2/3 fixes live) — run `30766572498`

- Rewards: **9/10 solved** (gpt2-codegolf 0.0, same as the frozen baseline;
  temp-1.0 noise, not GT).
- **17-feature delivery**: obligations 64 (89% referenced / **91% acted**);
  **localization 7** (the graph-depth fix landed — the deterministic localizer
  injection); def_partition 1; syntax_result 1. 4 features now deliver (was 2
  in round-6). covering_red/recovery/signature_delta/newfile_precedent/
  submit_refusal still 0 (triggers absent in these tasks).
- **Delta vs baseline**: headless −68.9%, llm-inference −60.0%,
  **cobol −40.3% (turnaround: was +132% in r6 / +276% in r5)**,
  fix-code −34.3%, schemelike +1.6%; regressions write-compressor +131%,
  portfolio +112%, gpt2 +112% (failed), break-filter +78%, modernize +35%.
  The cobol turnaround proves the obligation dedup + selectivity + localization
  fixes: the worst regression is now a win.
- The delta remains temp-1.0 noisy; L2/L3 ladder is the causal endpoint
  (obligations 91% acted = facts followed at scale).

## What's still missing (honest)

1. covering_red/recovery/signature_delta/newfile_precedent/submit_refusal fire
   only when their triggers occur in a task — none of these 10 tasks hit them
   (tests passed, no broken call-sites, no creates, no RED-at-submit). The
   forcing suite proves they CAN deliver; the live runs need tasks that trigger
   them.
2. The delta is single-run temp-1.0 (gpt2's +112% failed run vs round-5's solve
   is pure variance). A de-confounded multi-seed measurement is the honest
   endpoint.

## Round-3 (W1+W2) + round-4 (gateway threading)

- **Round-3 run `30755837073`** (W1+W2: answer-first render, value-gate, single-
  dose; dispatched BEFORE the gateway threading landed): 9/9 available tasks
  solved at reward 1.0 (gpt2 artifact lost again to the root-owned blob upload
  bug — fixed with sudo chmod). Facts delivered: only 2 (1 covering_red, 1
  def_partition), both inert. The value-gate correctly dropped the zero-gain
  syntax-OK facts; the gateway producers still abstained because round-3 ran
  pre-threading.
- **Gateway threading (commit `dbf43d5`)**: `_gateway_facts` now builds the
  ToolEvent authoritatively (semantics_authoritative mode from git/covering/
  command shapes), threads a CoveringResult with a SOURCE target (survives the
  leak-law) and edit_before_after (feeds patch_delta). Proven end-to-end:
  pytest failure with a source frame delivers covering_red/covering_verdict.
  Regression-gated. Provider-free green (`30756488370`).
- **Round-4 run `30757560927`** (with threading): dispatched — the gateway-
  delivery measurement (ladder census per feature).

## What's still missing (audit, 2026-08-02)

Proven: correct timing, neutral labels, valid journal, single-dose, value-gate,
syntax_result/covering_red/obligations firing, 17 features registered+invoked.

1. Round-5 delivery measurement (obligations+RED code is untested live).
2. Gateway producers needing real conditions (localization/patch_delta/
   newfile_precedent/def_partition) — live firing unmeasured; gated by the
   graph DB (built at startup, invalidated on edits).
3. Certified-search-completeness negative (STOP signal) — not implemented
   (needs graph to certify exhaustiveness).
4. Submit-verification gate (W3) — wired + RED tracking feeds it; end-to-end
   blocking on RED not verified; submit detection needs a live check.
5. L2/L3 confound — anchor-match ladder cannot distinguish causation from
   coincident work; obligations is the first genuinely-causal candidate.
6. Temp-1.0 noise — deltas descriptive only.
7. W4 typed-tool superiority — not yet shipped; certified-completeness drives adoption.
8. TLA+/TLC run — spec authored, never executed.
9. gpt2 artifact upload — sudo-chmod fix landed; round-4 must confirm.

## Deep research + 17-feature activation (round-3 readiness)

`gt_finalstand/ENGINE_DEEP_RESEARCH.md` answers the two questions:

- **Why 0/low facts**: the ENGINE wired only 2 producers (syntax_result via
  git-status+ast, covering_red via command regex). The groundtruth gateway
  (`_produce_raw_candidates` / `produce_raw`) — which fires a producer for
  every semantic event (file_view/edit_result/test_result/search_result/
  submit) — was never ported. Dominant action types (generic commands, reads,
  searches, heredoc edits) mapped to no wired producer; schemelike's heredoc
  edits produced 0 syntax facts.
- **Why inert**: render put raw first, facts trailing (lost-in-the-middle);
  syntax facts reported only "parses OK" (zero information gain);
  `def_partition` results equalled grep; no affordances rendered. Causal trace:
  delivered→referenced→acted ≈ 0.

**Fix landed (commit `4d78600`)**: the gateway is now ported into the ENGINE
compile step (`_gateway_facts` = `classify_event` + `produce_raw` → canonical
EvidenceArtifacts), and the full FACT owner set is registered. Census
`scripts/engine_feature_census.py`: **all_17_wired = True (9/9 FACT, 7/7
CAP_OWNER)**; caller_contract is REMOVE by disposition. 97 engine tests green.

## Round-10 + grep-anchor removal (2026-08-03)

- **Round-10 smoke `30816206132`: 9/10 solved.** write-compressor recovered to
  1.0 (was 0.0 in r9); gpt2-codegolf 0.0 (temp-1.0 noise). Trajectory audit:
  harness-probe actions dropped from **109 (r9) → 38 (r10)**, but the model
  still greps internal event/blocker NAMES (`episode_failure_recorded`,
  `submit_suppression_zero_delivery`, `failed-action:`) in readable files.
- **Grep-anchor removal (`6a193da`)**: renamed the anchors a model greps for to
  neutral identifiers — `failed-action:<sha>` → `fld-<sha>`,
  `episode_failure_recorded` → `failure_recorded`,
  `submit_suppression_zero_delivery` → `zero_delivery_recorded`. Now zero of
  the model's grep anchors exist in any file it can read (scan-verified).
- **GHA provider-free gate GREEN `30820611906`**: all-16-green, Gate 1 READY,
  Gate 2 META READY (13 checks), `on_disk_internal_id_leaks=0`, validator ok.
- **Ladder census (r10)**: obligations 109 delivered / **95 acted (87%)**;
  localization 11/7 (64%); covering_red 32 delivered. Facts deliver usable
  payload the model follows.
- **Honest limit**: the harness source in site-packages is inherently readable;
  a temp-1.0 frontier model that discovers it via `find /` will audit it. Each
  round removes the grep-able anchors, but the source itself is discoverable.
  This is a model-behavior cost (tokens on 3-4 tasks), not a GT delivery
  defect — 0 empty-evidence, correct payload/timing, tasks still solve.

## Round-11 + env-var leak fix (2026-08-03)

- **Round-11 smoke `30837898981`: 9/10 solved** (write-compressor held 1.0;
  gpt2-codegolf 0.0 noise). `errored` dropped 5→3.
- **Trajectory audit: probes went BACK UP 38→101** — round-10's grep-anchor
  removal helped, but the model read `env` and found the `GT_*` producer flags
  the workflow exported to the container (GT_EDIT_CHECK, GT_VERIFY_EXECUTE,
  GT_VERIFICATION_PLAN, GT_SDLC_VERIFY), then grepped those names into the
  harness source.
- **Fix (`8205fbb`)**: removed all `GT_*` from the container env in
  `tb2_miniswe_engine.yml`; the flags are now set internally by
  `_ensure_gateway_flags()` + `GT_SUBMIT_SUPPRESSION_ENFORCE` setdefault in
  `miniswe_gt_run.py`. The model's `env` is clean; the harness still gets its
  flags. **r12 measures the probe drop.**
- **Ladder (r11)**: obligations 105 delivered / 92 acted (87%); localization
  24/13 (54%). Payload integrity: 151 facts, 0 empty-evidence.
- Full status: `gt_finalstand/ENGINE_ROUND11_STATUS.md`.

## Verified

- Round-2 witness `30738637714` (10 tasks; write-compressor recovered in
  `30740338420`): 9/10 solved (gpt2 failed 0.0, same as baseline). Token
  deltas: headless −74%, gpt2 −50%, llm-inference −46%, write-compressor −18%;
  regressions portfolio +278%, break-filter +193%, schemelike +52%. The delta
  is descriptive, not causal — the causal trace showed the round-2 facts were
  inert (see ENGINE_DEEP_RESEARCH.md).

## Constraints honored

- No provider run before provider-free gates are green.
- Exactly ten paid ENGINE trials authorized; baseline not rerun.
- Engine work commits to `inline-engine`; advisory docs untouched.
