# DeepSWE v1.1 — GT capability inventory and pre-paid-run test plan

Source of truth: `D:\gt-context-plan` @ `codex/phase5-harness-proof`, HEAD `5fa957be` (includes
`7f62b891`). Tasks: `datacurve-ai/deep-swe` @ `435ee89e`. Per-task data: `deepswe_tasks.json`
(113 rows, 64 fields, 20 `pinned_smoke20`). Verdicts use `docs/AUDIT-ABILITY-SPEC.md` §5A;
tests are tagged L1–L4 per §4.

**Repo identity.** `D:\gt-harness` is a *different*, host-owned line (`eval/gt_central_agent.py`:
"GT is host-owned; there is no agent package to install in the task environment"). It has no
`persistent_plan/`, no `feature_accounting.py`, and zero synthetic-transport machinery. Everything
below is `gt-context-plan`. Ranked findings already fixed by `7f62b891` (`enrichment_cited_base_pruned`,
`shared_tmp_spool_external_delete`, `compound_test_unknown_outcome`, `suite_equivalent_scoped_undercredit`)
are **not** reported as open.

---

## (a) Breakdown

113 tasks, one image base (`mars-base:latest`), uniform contract: `cpus=2`, `memory_mb=8192`,
agent `timeout_sec=5400`, verifier `1800`, **`network_mode="no-network"` on both, all 113**.
All deps baked at build; no runtime egress.

| lang | n | verifier runners | graders |
|---|---|---|---|
| typescript | 35 | vitest 22, jest 12, mocha 5, deno 1, `node --test` 1 | vitest-junit-to-ctrf 11, jest-ctrf 8, vitest-junit+junit-to-ctrf 5, junit-to-ctrf 4, +7 |
| go | 34 | `go test -json` 35 | go-ctrf-json-reporter 24, gotest 10 |
| python | 34 | pytest 33, stestr 1 | pytest-junitxml 25, pytest 7, +2 |
| rust | 5 | cargo-nextest 5 | cargo-nextest 5 |
| javascript | 5 | mocha 3, jest 1, lib0/testing 1 | mocha-ctrf 3, jest-ctrf 1, lib0-junit-adapter 1 |

**Shape.** f2p min 2 / median 44 / max 254. **p2p min 1 / median 165 / max 66,265**; 12 tasks
> 2,000, 38 > 500. Monorepo/multi-root on 72/113 (`pnpm-workspace.yaml` 16, `cmd/` 22, `packages/` 14,
`turbo.json` 3, `go.work` 3). 11 verifiers `cd` into a sub-package. Repo size median 9.7 MB, max
3.6 GB (`opa-*` ×2); 8 repos > 200 MB.

**Two structural facts that dominate everything.**

1. **The scored tests do not exist during the agent run.** On all 113, `tests/test.patch` adds the
   f2p test files *and* `/app/test.sh` at grade time. The workspace has no task test entry point and
   no scored test to run. Every covering/RED capability can therefore only observe a **regression in
   the pre-existing p2p suite**, never the graded behaviour — and the agent must improvise its
   verification command, which is why compound/piped shapes dominate.
2. **`test_command_scope` is pytest-only**, still, after `7f62b891` (`covers_known_suite` is a
   pytest-path addition). **83 of 113 (73%) can never yield a suite verdict.**

**GT-off (deepseek-v4-flash, n=4, 452 trials):** 241/452 = **pass@1 0.5332**, mean 152.9 steps /
1,439 s — reproduced exactly from `deepswe_tasks.json`. 23 tasks 4/4, 22 tasks 0/4. Max single trial
3,641 s vs the 5,400 s rail; 6 tasks exceed 2,700 s.

**GT-on (smoke20, run 34801009507):** `status: FAIL`, **6/20 solved** vs a GT-off expectation of
**10.25/20** — ≈42% relative loss. Two 4/4 GT-off tasks (`bandit-interprocedural-taint-checks`,
`claude-code-by-agents-recursive-delegation`) failed under GT. `gt-live-gate.json`: `passed:false`,
9/21 identities exercised, `graph_refreshes: 0`, `utility_scored: 0`, `progress_transitions: 0`.
$6.857 for 20 tasks, 2,916 provider calls, 45 failed.

---

## (b) Capability verdicts on DeepSWE

### 21 feature identities — `gt-audit.json` records only `WITNESSED` / `INELIGIBLE`

**12 identities were INELIGIBLE on 20/20 tasks with reason `no_trigger_observed` and zero
deliveries:** `covering_red`, `recovery`, `signature_delta`, `submit_refusal`, `syntax_result`,
`GT_CERT_DELIVERY`, `GT_CHANGE_SURFACE`, `GT_EDIT_CHECK`, `GT_HYPOTHESIS`, `GT_LOC_RESLOT`,
`GT_PATCH_DELTA`, `GT_SS_SUBMIT_RED`. The 9 that fired: `cochange_prior` 557 deliveries,
`caller_contract` 174, `localization` 101 (20/20), `newfile_precedent` 42 (15/20),
`select_catalog` 32, `plan_gate` 27 (**10/20 only**), `obligations` 20, `persistent_plan` 19,
`def_partition` **1 delivery, 1/20 tasks**.

**`action_observed` and `action_consistent` are false for every identity on every one of the 20
tasks**; `gt-live-gate.json` `action_consistent_features: []`. Not one GT feature is recorded as
having changed an agent action.

| identity | precondition (`attribution.py:20-167`) | applies / N/A | verdict |
|---|---|---|---|
| `localization`/`GT_LOC_RESLOT` | ranking *placed into* task-start or next search request | 113 | **PARTIAL** — base 20/20, reslot alias INELIGIBLE 20/20 |
| `caller_contract` | viewed/edited callable has *verified* callers | 113 | **PARTIAL** — fires, correctness untested; `file_view` is outside `DECIDABLE_ABSENCE`, so silence is never provably wrong |
| `cochange_prior` | verified companion at the indexed revision | 113 (images keep real history) | **PARTIAL** — highest volume, zero action-consistency |
| `def_partition` | search result partitionable into defs/refs | 113 | **BROKEN** — 1 delivery in the whole cohort |
| `newfile_precedent`/`GT_CHANGE_SURFACE` | new file exposes a verified precedent; needs `edit_transactions/` | 41 create-a-file goldens / **72 edit-only N/A** | **PARTIAL** — base 15/20, alias INELIGIBLE 20/20 |
| `obligations` | issue text yields evidence-backed obligations | 113 | **PARTIAL** — 20/20, but `obligation_reverified` ×36 all `commands_run: 0` |
| `persistent_plan` | plan built pre-first-edit from prompt + base repo + graph | 113 | **BROKEN** — baseline input never captured |
| `plan_gate` | unevidenced row at submit, **or a test green pre-edit now failing** | 2nd clause ≤30 pytest / **83 N/A** | **BROKEN** — 17/67 decisions accepted with `completion_proven:false`, `baseline_status:"unknown"`, closing `terminal:"submitted_verified"` |
| `select_catalog` | versioned bootstrap catalog offered | 113 | **PARTIAL** |
| `covering_red` | executed covering test fails *because of* an edited file | induced p2p regression only / **113 N/A for the scored tests** | **UNPROVEN** — INELIGIBLE 20/20; partly instrumentation (`gtbridge_owned_features_unwired`, fixed 2026-09-15) |
| `recovery`/`GT_HYPOTHESIS` | same failure recurs across an edit | needs ≥2 `test_result` rows / 83 N/A (`_TEST_FAILURE_FILE_RE` = `\.py` only) | **UNPROVEN** |
| `signature_delta`/`GT_PATCH_DELTA` | signature change + verified call sites; needs `transaction_artifacts/` | signature goldens / body-only N/A | **UNPROVEN** |
| `syntax_result`/`GT_EDIT_CHECK` | check *fails* on an edited file, `syntax[].status == "exact"` | certified-parser languages / rest N/A (probe degrades to `py_compile`) | **UNPROVEN** — `uncontrolled_negative: True`; no artifact has ever produced non-empty `diagnostics` |
| `submit_refusal`/`GT_SS_SUBMIT_RED`/`GT_CERT_DELIVERY` | submit with unresolved positive RED | induced regression only | **UNPROVEN** — reads INELIGIBLE although the journal has **50× `action_suppressed reason=submit_refused`**: an observability defect |

### 11 capabilities (`tests/fixtures/capability_matrix.json`, 64 failure modes)

| capability | precondition here | applies / N/A | verdict + observed break |
|---|---|---|---|
| `runtime_observation` | `TEST_RUNNER_RE` matches; `_execution_outcome_guard` ≠ `unknown` | 113 for *kind*; **30 for scope** / 83 N/A | **BROKEN 83/113.** `go test`, `cargo test`, `npm test`, `deno test` return `unknown` scope by construction |
| `persistent_plan` | `discover_command` finds a suite finishing in **120 s** (`0.03 × 5400`, clamped) | 113 | **BROKEN.** Simulated: `npm test` ×38, `pytest` ×30, `go test ./...` ×18, `make test` ×16, `cargo test` ×4, `tox` ×3, **None ×3**; 68/113 `confidence: low`. Measured: **0 of 18 reached `captured`** — `spawn_failed` 27, `no_tests_observed` 20, `unknown` 16, `timeout` 4. `plan_check_bound` ×30 all with empty `selected_test_ids`/`protocol`; `binding_pending` 2,244 vs `bound` 552 |
| `lsp_promotion` | serviceable leg: toolchain, launch, `candidate_unit_count>0`, `probe_requests_issued>0` | go 34, rust 5, python 34, ts/js 40 | **BROKEN for ts/js and rust.** 345 receipts: **ts 111 + js 155 legs → 0 selected**, each carrying `install_missing_reason: "no workspace TypeScript install (node_modules/typescript) and no tsserver.path"`; **rust 29/29 `project_ready:false` after `project_ready_wait_ms: 180002`** (46 attempts), 9,414 `failed_empty`. gopls **WORKING OFFLINE** (36/36, median 1,092 ms); pyright PARTIAL (34/85 not ready). 17% of promotions published, 45% obsolete. `--tsserver-path` still reaches TLS 6.0.0, which rejects it |
| `graph_pipeline` | index + amend inside `0.35 × wall` | 113 | **BROKEN.** 942 `graph_refresh_failed`, **100% `ValueError` / `frozen_source_incomplete:missing=1`**; 601 identical `amend_failed:GT_INDEX_PROCESS_FAILED` in one task, zero rebuilds. `GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT`, gt-index **exit −9** at 8 GB. Initial index is *not* the bottleneck (`elapsed_ms: 0`, cache-served) |
| `receipts_attestation` | diagnostics survive finalisation | 113 | **BROKEN.** **8/20 emitted no diagnostics** — the same 8 with `product: ERROR` and audit reason `"Mini-SWE trajectory has no terminal exit_status"`. One mechanism; it hit a *solved* task |
| `verifier_reward_binding` | `model.patch` copies out | 113 | **BROKEN.** No `model.patch` ⇒ `docker cp` raises a bare `RuntimeError` instead of the typed receipt — root of the 8-task cascade |
| `delivery_lanes` | boundary reached + byte budget | 113 | **PARTIAL.** 2,397 deliveries, **1,382 (57.7%) `seen_no_action_on_content`**; `delivery_refused` ×820 (~777 `localization_fire_once`). oxvg's legitimate post-recovery redelivery scored a fault (`duplicate GT delivery identity: 4ec9a448…`), driving the audit RED |
| `provider_pipeline` | route reachable at 20-way | 113 | **PARTIAL.** 45/2,916 failed; `FormatError` with empty body ×4; boa exit 7 / `provider_request_failed`; `"trajectory api_calls 52 + bootstrap calls 0 != provider requests 53"` |
| `churn_governor` | recognisable build/test commands | 113 | **PARTIAL.** fd `churn_abort` at 45 calls was *correct*; `quiet_non_convergence` at 282/218/210/144 calls on four unsolved tasks |
| `submit_gate` | an observed `active_red` | ≤30 / 83 N/A | **UNPROVEN.** `claude-code` accepted with 14 `active_red` predicates |
| `tb2_host_side` | — | **NOT APPLICABLE** (`covering_tests: []`, separate repo track) | n/a |

**Mandatory rows** (`_mandatory_capability_rows`, `(name, state, evidence, required)`): only
**12 of 20 tasks emitted any rows**. Of those, `dense_retrieval` / `gt_engine_enabled` /
`capability_negotiation` / `receipt_writer` WORKING 12/12; **`lsp_promotion` WORKING 4 / DEGRADED 8**,
4 of the degraded with `required=True`. `dense_retrieval` also disagrees with the attestation, which
raises `treatment_dense_index_not_ready` on 5 tasks — two planes contradict each other.

---

## (c) Equivalence classes — language × GT command × layout × verifier

All 113 covered. `pytest-scope` = tasks where `test_command_scope` can return other than `unknown`.

| class | n | GT `discover_command` (basis/conf) | layout | verifier | pytest-scope | representative |
|---|---|---|---|---|---|---|
| EC1 py-pytest-single | 18 | `pytest` (pyproject/ini/cfg, medium) | single | junit | 18 | `mashumaro-flattened-dataclass-fields` (p2p 30,014) |
| EC2 py-pytest-monorepo | 10 | `pytest` (medium) | monorepo | junit | 10 | **`adaptix-name-mapping-aliases`** [S20] |
| EC3 py-tox/none | 5 | `tox` ×3, **None ×2** | monorepo | junit | 0 | **`bandit-interprocedural-taint-checks`** [S20] |
| EC4 go-gotest | 19 | `go test ./...` (go_mod, low) | cmd/monorepo | ctrf | 1 | **`actionlint-action-pinning-lint`** [S20]; `expr-try-catch-errors` p2p 66,265 |
| EC5 go-make-test | 15 | `make test` (makefile, low) | monorepo | ctrf | 0 | **`abs-stepped-slices`** [S20]; `opa-*` 3.6 GB |
| EC6 ts-npm-on-pnpm | 14 | **`npm test` on a `pnpm-lock.yaml` repo** | pnpm workspace | ctrf | 0 | **`arktype-json-schema-refs-dependencies`** [S20] |
| EC7 ts-npm-on-npm/yarn | 14 | `npm test` | mixed | ctrf | 0 | **`awilix-async-container-initialization`** [S20]; `meriyah` p2p 51,469 |
| EC8 js-npm | 3 | `npm test` (medium) | monorepo | ctrf/junit | 0 | **`testem-bail-on-test-failure`** [S20] |
| EC9 rust-cargo | 5 | `cargo test` (cargo, low) | workspace | nextest | 0 | **`pest-character-class-coalescing`** [S20] |
| EC10 deno | 2 | `pnpm test` ×1, **None ×1** | no `node_modules` | ctrf | 0 | `cliffy-config-file-parsing` |
| EC11 polyglot/mislabelled | 6 | `npm test` ×4, `pytest`, `make test` | mixed | mixed | 1 | `prometheus-transactional-reload-status` (labelled *typescript*, repo is **Go**, 290 MB) |
| EC12 exotic | 2 | `npm test` | custom runner / service | junit/ctrf | 0 | `yjs-map-conflict-detection` (lib0/testing); `eicrud-…` needs **mongod** |

Six language mislabels route the wrong LSP server: `httpx-deterministic-cookie-store`
(*typescript* → Python repo), `koota-entity-snapshot-rollback` (*python* → TypeScript),
`claude-code-by-agents-recursive-delegation` (*typescript* → Swift), `prometheus-…` (→ Go),
`kea-…`, `katex-…`.

---

## (d) Test plan

No provider call, benchmark dispatch or GT-off rerun anywhere. Docker only at L3.

**T0 — corpus tests (L1).** All inputs already in `deepswe_tasks.json`.
- **T0.1 `discover_command` corpus, 113 cases.** Materialise each `repo_root_test_config` with the
  real config bytes and assert the exact `(command, basis, confidence)` triple. There is currently
  **no test at all for `go.mod`, `Cargo.toml` or `package.json` discovery** — only pytest. Locks the
  38 `npm test` / 16 `make test` / 3 `None`.
- **T0.2 verifier-command corpus.** All 113 `verifier_commands` through `TEST_RUNNER_RE`,
  `_protocol`, `test_command_scope`, `_execution_outcome_guard`, `classify_test_observation`.
  Makes the pytest-only scope hole show up as 83 red rows instead of silence. `test_command_scope`
  has **zero non-pytest cases** today. *RED cases: "Commit-message text mistaken for executable
  verification" (add `echo 'go test ./...'`, `git log` negatives); "`uv --project` wrapper losing
  the test boundary".*
- **T0.3 improvised-command corpus.** Mine the recorded trajectories for what agents actually wrote
  (`cd pkg && npx vitest run 2>&1 | tail -30`, `cargo nextest run -p meta`, `pnpm -r --filter core test`,
  `deno test -A`) and assert per-language classification. *RED: "Fully named green suite failing to
  open the advisory"; "Partial test inventory mistaken for complete suite coverage".*
- **T0.4 output-name parsing.** `test_runner_output_name_parsing_per_language` covers cargo/go/dotnet/
  jest/bun/mix/phpunit/bazel/sbt — **not pytest, not vitest**. Add both; vitest is 22 of 35 TS tasks.
- **T0.5 `CheckSpec` admission corpus.** Every emitted command through `CheckSpec.from_dict` /
  `decompose_check_command`; assert which raise `"automatic check requires a canonical test-runner
  invocation"` and which refuse `unsupported_shell_operator:(`. *RED: "`vendor/src/a.py` failure
  attributed to edited `src/a.py`" — `_TEST_FAILURE_FILE_RE` is `\.py`-only.*

**T1 — per-class fixture repos (L2), via `run_loop_real_repos.py --repo NAME=/abs/path`** (it
accepts arbitrary paths, so point it at a `git clone --depth 1` of the real repo at `base_commit`;
no image needed). Fixtures must be the real repos — the defects are layout defects.

| class | fixture | assertion |
|---|---|---|
| EC1/EC2 | `mashumaro`, `adaptix` | baseline reaches **`captured`** at least once (today 0/18); `test_file_digests` non-empty |
| EC3 | `bandit` (tox), `numba` (None) | `status == "no_test_command"` → `no_baseline`; **no verified rows minted** |
| EC4 | `actionlint`, `expr` (p2p 66,265) | gopls `probe_requests_issued>0`; `expr` lands `timeout`, not a false `captured` |
| EC5 | `abs`, `opa` (3.6 GB) | no `GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT`, no gt-index exit −9 at 8 GB |
| EC6 | `arktype` (pnpm) | the emitted `npm test` actually runs; else prefer the lockfile manager |
| EC7 | `meriyah` (p2p 51,469) | `MAX_OUTPUT_CHARS = 20_000` truncation ≠ `no_tests_observed` |
| EC8 | `testem`, `arktype` | reproduce `install_missing_reason` on **all** ts/js legs; `--tsserver-path` must not reach TLS 6.0.0 |
| EC9 | `pest`, `oxvg` | `project_ready` true inside a bounded wait — **not 180,002 ms ×46** |
| EC10 | `cliffy` (Deno) | LSP `status == "no_op"` ⇒ `required=False`, not FAILED |
| EC11 | `prometheus` | stage gopls from observed repo content, not the task label |
| EC12 | `yjs` | no fabricated verdict from lib0 output |

Stub only clock and transport at their own boundaries; real producers, real `gt-index`. Spec §4 L2:
do not hand-mint delivery rows; do not use GTBridge as Mini-SWE proof. `run_loop_real_repos.py`
currently **exits 0 regardless** (8/18 SIGKILLs passed) and never exports a patch — fix both before
it gates anything.

**T2 — installed rehearsal (L3).** `gt_installed_rehearsal.py` via Pier, synthetic transport,
external provider calls zero. Today: **1 synthetic Python task on 1 of 113 images**. Extend to one
real task image per equivalence class (12), reusing the `deepswe_cache_images.yml` GHCR mirrors.
Per run assert the three mandatory rows with exact evidence strings, the baseline status, and an
honest non-zero synthetic request count. Missing assets ⇒ **UNPROVEN**, not a skipped pass.

**T3 — mutation checks (L4).** Break one boundary per ability and require red: producer returns
nothing on eligible input; `discover_command` → `None`; localization prepared but never placed in
the request; graph ancestor manifest deleted; `probe_requests_issued` forced to 0; `model.patch`
removed (must yield a typed receipt, not `RuntimeError`).

**CI matrix.** T0 in the unit job on every push; T1 as `strategy.matrix.class` over the 12
representatives; T2 nightly on 12 mirrored images; T3 weekly. Reuse `installed_rehearsal.yml`'s LSP
staging (gopls v0.23.0, rust-analyzer 2026-08-31, pyright 1.1.413, TLS 6.0.0 + typescript 5.9.3)
per class rather than once. **Branch risk:** none of these tests exist on `main`, and
`central_provider_free.yml` — the actual gate on the paid `deepswe_miniswe_central.yml` — runs none
of `test_wheel_language_matrix`, `test_persistent_plan_baseline`, `test_baseline_classification`,
`test_batch_graph_installed`, `test_smoke20_recorded`. Land the branch or the gate is decorative.

---

## (e) Ranked failure classes likely to hit the next paid run, uncovered today

1. **Missing-patch bind crash cascades into total blindness.** A trial with no `model.patch` dies at
   `docker cp` with a bare `RuntimeError`; product ERROR, and diagnostics are never emitted. Hit
   **8/20 (40%)**, including a *solved* task. At 113 tasks expect ~45 blind trials. Highest leverage.
2. **Baseline capture succeeds on nothing: 0/18 `captured`.** `spawn_failed` 27 / `no_tests_observed`
   20 / `unknown` 16 / `timeout` 4. `spawn_failed` dominating means the *discovered executable does
   not resolve in the child env* — untested for `npm`, `make`, `go`, `cargo`, `tox`. This kills
   `persistent_plan`, `plan_gate`'s regression clause and the submit advisory on the whole benchmark.
3. **The gate says `verified` on runs that proved nothing.** 17 of 67 `plan_gate_decision` rows
   accepted with `completion_proven:false` and `baseline_status:"unknown"`, closing
   `terminal: "submitted_verified"`. This corrupts the attestation the benchmark rests on.
4. **Non-pytest scope blindness, 83/113.** No suite verdict for go/rust/npm/deno. `7f62b891` fixed
   only the pytest variants; no test asserts the non-pytest outcome, so it reads as silence.
5. **All 266 ts/js LSP legs blocked, 0 edges selected** — `install_missing_reason` on every one,
   plus `--tsserver-path` crashing TLS 6.0.0. 40 tasks (35%) get no TS semantics at all.
6. **rust-analyzer burns 180,002 ms per leg × 29 legs and promotes almost nothing** (`project_ready:
   false` 29/29, 9,414 `failed_empty`). Pure spend on 5 tasks.
7. **Graph refresh fails 942× and the audit reports `graph_refreshes: 0`.** 100% `ValueError /
   frozen_source_incomplete`; 601 identical `amend_failed:GT_INDEX_PROCESS_FAILED` with zero
   escalation. A measurement bug that makes a broken subsystem look idle.
8. **`npm test` emitted for 17 pnpm-lockfile repos.** `_cfg_package_json` hardcodes `npm` and wins
   before the `extension_fallback` that would have chosen `pnpm`/`yarn`. Never tested.
9. **The scored tests are absent during the run**, so `covering_red`, `recovery`, `submit_refusal`,
   `GT_SS_SUBMIT_RED`, `GT_CERT_DELIVERY` have no positive path except an induced p2p regression —
   all five INELIGIBLE 20/20. Nothing constructs that case.
10. **57.7% of deliveries are `seen_no_action_on_content`** and `action_consistent` is false on every
    identity on every task. This bounds any efficacy claim regardless of the bugs above.
11. **gt-index OOM (exit −9) at 8 GB.** 8 repos exceed 200 MB, `opa-*` is 3.6 GB; none indexed.
12. **Six language mislabels**, and exotic singletons: `yjs` (lib0/testing — outside GT's entire
    runner vocabulary), `eicrud` (needs `mongod`), `cliffy`/`optique` (Deno/JSR, no `node_modules`).
13. **Concurrency.** 45/2,916 failed calls and `FormatError` with an empty body at 20-way; the 113-task
    run is ~5.6× the exposure, and nothing provider-free tests concurrency at all.
