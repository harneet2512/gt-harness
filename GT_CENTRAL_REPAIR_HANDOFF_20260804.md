# GT Central Engine Repair — Complete Session Handoff

Date: 2026-08-04
Branch: `inline-engine`
Remote: `https://github.com/harneet2512/gt-harness.git` (`origin`)
Head: `ab39af1`
Audit baseline: frozen GT-off `D:\gt_runs\miniswe_tb2_gtoff_20260731\per_task_tokens.json`
Smoke runs: `30922119991` (invalid — stale code), `30924339091` (valid — repaired code)

---

## 0. Executive verdict

The repair was implemented end to end and is **proven as an engine**: the
false oracle is gone, the source-revision model is correct, validation
classification is unified, all 17 features have consumers, payloads are
grounded, and certificates are truthful on live runs.

The repair is **NOT proven as an efficiency or outcome win**. The valid
ten-task smoke solved 7/10 (GT-off solved 9/10) — two GT-off solves
(`schemelike-metacircular-eval`, `write-compressor`) were lost to
`ModelTimeout` at the pre-existing 120-second provider timeout, and 4/7
comparable solved tasks had positive token deltas. **The 89-task run stays
blocked.** A re-smoke on the raised timeout is gated on the owner.

Also documented plainly: my first smoke dispatch ran the **stale remote code**
(`be9ce1c`) because I had not pushed the repair commits; I pushed, re-dispatched
(`30924339091`), and audited only the valid run. That mistake cost one paid
run and is flagged in §6.

---

## 1. Diagnosis source

`THIS_WILL_BE_EVERYTHING.md` (795 lines, SHA-256
`63F3BB8FBCD79C4A0BBABE1F2C248FFA3B709C602EE8EECD567EAC34901A2D93`, also at
`C:/Users/Lenovo/Downloads/this will be everything.md`).

Confirmed root causes:
1. `ALL_17_DELIVERABLE` was a producer-only oracle; it could not detect that
   most features have no consumer.
2. Whole-workspace revision made validation evidence stale on artifact churn
   and let validation debt fire on `benchmark_out.txt`, `callback-test.txt`,
   `report.jsonl`.
3. Runtime, ledger, and metrics used different validation classifiers — 14
   real validation actions were invisible to the ledger; all 8 submission
   certificates reported `check_count=0 / unverified`.
4. Multi-action model responses executed every action before GT feedback.
5. Payloads were generic booleans, not concrete anchors.

---

## 2. What was implemented (commit by commit)

All on `inline-engine`, all pushed to `origin`.

| Commit | Message | Phase |
|---|---|---|
| `3cb445a` | Replace false `ALL_17_DELIVERABLE` oracle with consumer gates | 0 |
| `e4a663d` | Split source revision from workspace and unify validation classification | 1–2 |
| `b944f08` | Add consumer/effect registry and interrupt multi-action batches | 3–4 |
| `4494111` | Ground all model-visible payloads and track engine actions | 5–6 |
| `d71c2cd` | Add all-17 producer/consumer proof suite and fix `GT_CERT_DELIVERY` gating | 7 |
| `b6ad9e3` | Replace L1/L2/L3 metrics with the feature funnel and update behavioral docs | 8 |
| `d1f8eca` | Add provider-free replay harness and report all change origins | 9 |
| `ab39af1` | Raise engine-matrix model timeout to match the canary | 10 (remediation) |

### Key artifacts
- `gt_engine/central_controls.py` (new): `EffectKind`, `FeatureEffect`,
  `ConsumerSpec`, `CONSUMER_SPECS` — operational role for all 17 IDs.
- `gt_engine/central_runtime.py`: `ChangeOrigin`, `ClassifiedChange`,
  `RevisionState`, `classify_change`, `source_revision_of`,
  `task_deliverable_paths`, `select_declared_check`, extended
  `ValidationClassification` (with `.with_result()`), `feature_payload_grounded`,
  effect routing/consumption, batch-interrupt recording, per-action
  `validation_log`, receipt schema v3.
- `eval/gt_central_agent.py`: per-action unified classification; effects
  consumed after each action before the next pre-decided action; immediate
  controls cancel remaining batch actions (recorded, not dropped); ledger and
  readiness bound to source revision; `central_receipt.json` → schema v3.
- `gt_engine/deep_metrics.py`: validation_log override for `check_actions`;
  `_feature_funnel` replaces L1/L2/L3 (`feature_produced`, `feature_consumed`,
  `feature_effects_applied`, `guidance_deliveries`,
  `guidance_behaviorally_aligned`); `_lifecycle_metrics`.
- `scripts/central_feature_census.py`: five gates
  (`ALL_17_PRODUCERS_PROVEN`, `ALL_17_CONSUMERS_PROVEN`,
  `ALL_EFFECTS_TIMING_VALID`, `ALL_PAYLOADS_GROUNDED`,
  `ALL_17_CONSUMER_PATHS_PROVEN`); `ALL_17_DELIVERABLE` deleted.
- `scripts/central_replay.py` (new): provider-free replay of archived
  trajectories through the repaired policy.
- `scripts/central_readiness_audit.py`: consumer-path gate.
- `tests/test_gt_central_consumer_proof.py` (new): 17 positive + 17
  adversarial + 8 cross-feature scenarios.
- `tests/test_central_replay.py` (new): replay contract tests.
- `.github/workflows/central_provider_free.yml`: proof suite added.
- `.github/workflows/tb2_miniswe_engine.yml`: integrated feature now passes
  `--ak model_timeout_sec=300 --ak model_loop_timeout_sec=900`.
- `AGENTS.md`, `CLAUDE.md`, `gt_finalstand/CENTRAL_RUNTIME_IMPLEMENTATION.md`,
  `gt_finalstand/GT_EFFICIENCY_REMEDIATION_PLAN.md`: behavioral contract
  updated to the repaired truth.

### Behavior changes that matter
- **Source revision**: only model-authored regular source files advance it.
  Caches, `.so/.o`, `a.out`, `build/`, directories, logs, benchmark output,
  and background writes never do. Task deliverables (e.g. `report.jsonl`) are
  tracked separately and never stale validation.
- **One classifier**: the agent classifies each action once; runtime, ledger,
  receipt, and deep metrics share the immutable object.
- **Certificates**: `readiness_evidence(source_revision)` reports real current
  checks; env failures stay env failures.
- **Batch timing**: a fresh syntax failure interrupts the remaining pre-decided
  actions of the same model response; each cancelled action is recorded
  (`interrupted_actions`, `batch_interrupts`, `predecided_actions_cancelled`).
- **Grounded payloads**: model-visible payloads must name concrete evidence;
  generic prose stays private (`NO_OP_WITH_REASON`).
- **GT_CHANGE_SURFACE** reports every classified change labeled by origin;
  validation debt counts only source-relevant changes.
- **Bug fixed**: `GT_CERT_DELIVERY` was only emitted on refused submits (a
  latent indentation bug caught by the proof suite); now always emitted at the
  submit boundary.

---

## 3. Provider-free verification state (all green)

Commands (all must pass before any paid run):
```
python -m pytest tests/test_gt_central_runtime.py tests/test_gt_central_agent.py \
  tests/test_gt_central_consumer_proof.py tests/test_gt_deep_metrics.py \
  tests/test_central_replay.py tests/test_gt_on_experiment.py -q
python scripts/central_feature_census.py      # 5 x ALL_17_*_PROVEN, exit 0
python scripts/central_readiness_audit.py     # READY, exit 0
python -m ruff check <all touched files>      # clean
```
Result at handoff: **103 provider-free tests green**, census exit 0 with all
five lines, readiness READY, ruff clean.

Note: the CI provider-free workflow (`central_provider_free.yml`) still needs
the new replay test module added if you want it in CI:
`tests/test_central_replay.py`.

---

## 4. Phase 9 replay (archived run `30887276162`)

Harness: `scripts/central_replay.py`. Artifacts:
`D:\gt_runs\30887276162\` (downloaded from GitHub), report at
`D:\gt_runs\30887276162\central_replay_report.json`.

Result: **REPLAY_OK — 10/10 tasks pass.**

- Zero artifact-driven validation-debt triggers across all tasks (portfolio
  `benchmark_out.txt`, schemelike `callback-test.txt`, fix-vuln `report.jsonl`
  no longer fire debt).
- portfolio: 4 declared validation actions reach the ledger
  (`validation_declared=4`, `ledger_checks_total=4`).
- fix-vuln: `report.jsonl` is a task deliverable; its `pytest -rA` is
  recognized and reaches the certificate (old `check_count=0` → new `1`).
- No generic obligation/localization guidance stream; no external guidance
  growth without a grounded effect.

---

## 5. Phase 10 smoke — valid run `30924339091` (repaired `d1f8eca`)

12/12 jobs succeeded (plan + 10 tasks + merge). All 10 graded. **7/10 solved.**

| Task | Solved | Agent exit | Cert (checks/pass) | old cert |
|---|---|---|---|---|
| break-filter-js-from-html | 1 | Submitted | 2/2 validated | 0/unverified |
| cobol-modernization | 1 | Submitted | 0/0 unverified (truthful) | 0/unverified |
| fix-code-vulnerability | 1 | Submitted | 1/1 validated | 0/unverified |
| gpt2-codegolf | 0 | ModelTimeout | — | — |
| headless-terminal | 1 | Submitted | 1/1 validated | 0/unverified |
| llm-inference-batching-scheduler | 1 | Submitted | 1/1 validated | 0/unverified |
| modernize-scientific-stack | 1 | Submitted | 1/1 validated | 0/unverified |
| portfolio-optimization | 1 | Submitted | **4/4 validated** | 0/unverified |
| schemelike-metacircular-eval | 0 | ModelTimeout | — | — |
| write-compressor | 0 | ModelTimeout | — | — |

Repair audit: **PASS** (schema v3 everywhere; truthful certs; portfolio's 4
declared checks certified; zero validation-debt receipts; zero artifact-driven
debt; runtime/ledger/metrics agree; no generic guidance).

Promotion gates: **FAIL** (2 GT-off solves lost; 3 ModelTimeouts; 4/7
comparable solved tasks positive token deltas — descriptive, single-sample).

### 5.1 Feature firing in the valid smoke (10 tasks)

12 of 17 IDs produced receipts; 5 were correct-quiet (their triggers did not
occur in this sample, not evidence of breakage):

| Fired (tasks) | Never fired |
|---|---|
| obligations 10, GT_CHANGE_SURFACE 7, GT_PATCH_DELTA 7, GT_CERT_DELIVERY 7, syntax_result 6, GT_EDIT_CHECK 6, localization 3, GT_LOC_RESLOT 3, covering_red 2, GT_HYPOTHESIS 2, def_partition 1, newfile_precedent 1 | caller_contract, recovery, signature_delta, submit_refusal, GT_SS_SUBMIT_RED |

Model-visible deliveries: only `covering_red` (2 total). The provider-free
census proves all 17 can fire/consume/deliver.

### 5.2 Descriptive resource deltas vs frozen GT-off (single sample)

| Task | off→on solved | token delta % |
|---|---|---|
| break-filter-js-from-html | 1→1 | +95.3 |
| cobol-modernization | 1→1 | +33.3 |
| fix-code-vulnerability | 1→1 | **−51.2** |
| gpt2-codegolf | 0→0 | −95.5 (timed out) |
| headless-terminal | 1→1 | **−73.0** |
| llm-inference-batching-scheduler | 1→1 | **−23.9** |
| modernize-scientific-stack | 1→1 | +98.5 |
| portfolio-optimization | 1→1 | +100.6 |
| schemelike-metacircular-eval | 1→0 | −99.7 (timed out) |
| write-compressor | 1→0 | −99.9 (timed out) |

No efficiency claim is defensible from a single temperature-1 sample (see
diagnosis §6, finding 3). At least 3 matched repetitions per arm with
task-level medians are required before any causal claim.

---

## 6. What went wrong (owner should know)

1. **First smoke ran stale code.** I dispatched run `30922119991` before
   pushing the repair commits; the remote `inline-engine` was still at
   `be9ce1c`, so that run produced v2 receipts and is invalid. I pushed and
   re-dispatched `30924339091`. Cost: one wasted paid run. Guard: always
   `git push origin inline-engine` and verify the run's `headSha` equals the
   intended commit before treating results as valid.
2. **Two GT-off solves lost to `ModelTimeout`.** `schemelike` (3rd call) and
   `write-compressor` (1st call, 1,161 tokens) timed out at the default
   `model_timeout_sec=120`. This is provider latency on deepseek-v4-flash, not
   a policy regression. Fixed for future runs by `ab39af1`
   (`model_timeout_sec=300`), but the timeout fix has not been re-smoked.
3. **Efficiency is not demonstrated.** Even the solved tasks show mixed
   deltas; the engine reduces token cost on some tasks and not others. The
   intervention is still mostly the validation-debt/submit-hold/lint path —
   most features remain passive internal consumers in real runs.

---

## 7. Remaining work (honest gaps)

From the diagnosis TODOs, these are **not** fully implemented yet:

- **P0/P1 — auto-validation (Phase 6 §2):** validation debt is still advisory
  only; the engine does not yet auto-run cheap declared checks host-side.
  `engine_actions` metric tracks lint but no auto-check path exists.
- **P1 — semantic signature delta:** the detector still keys on explicit
  `sed -i` before/after signatures; ordinary file rewrites, patch application,
  and scripted edits are not yet recognized as signature deltas.
- **P1 — newfile_precedent placement validation:** precedent is recorded from
  search markers only; there is no new-file placement/registration validator.
- **P2 — full deep-metrics set:** the funnel and lifecycle basics are in; the
  complete engine-resource set (sensor scans/hash bytes, source churn, failure
  → discriminating-action delay, pre-decided actions prevented per task) is not
  all emitted.
- **P3 — repeated matched trials:** no shadow/treatment repetition run yet; no
  task-level median/uncertainty gate exercised.
- **P3 — 89-task run:** blocked (outcome preservation + efficiency unproven).
- **Docs:** `THIS_WILL_BE_EVERYTHING.md` TODO checkboxes not updated; a few
  `gt_finalstand` planning docs still reference the old oracle wording.

---

## 8. Data locations

- Diagnosis: `THIS_WILL_BE_EVERYTHING.md`
- Archived smoke (old code, invalid results): `D:\gt_runs\30887276162\`
- Replay report (archived): `D:\gt_runs\30887276162\central_replay_report.json`
- Invalid smoke (stale code): `D:\gt_runs\30922119991\`
- **Valid smoke:** `D:\gt_runs\30924339091\` (10 receipts + trajectories + MERGED)
- Frozen GT-off baseline: `D:\gt_runs\miniswe_tb2_gtoff_20260731\per_task_tokens.json`
- Temp audit scripts: `D:\tmp\opencode\audit_smoke.py`, `audit_smoke2.py`,
  `audit_timeout.py`, `audit_delta.py`, `count_features.py`

## 9. Recommended next steps

1. Re-smoke the same ten tasks on current `inline-engine` (`ab39af1`) to test
   the timeout fix and re-check outcome preservation. **Requires owner
   approval (paid run).**
2. If outcome preservation holds, run ≥3 matched shadow/treatment repetitions
   per arm on a small comparable subset for the median/uncertainty efficiency
   gate.
3. Then, and only then, reconsider the 89-task run.

All other promotion gates from the diagnosis §10/§11 remain in force.
