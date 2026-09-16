# Plan: benchmark-ready, proven on 10 DeepSWE tasks — with no paid run until every task is certified offline

Owner rule this plan exists to enforce: **we stop discovering bugs by spending money.** Every GT-side
behaviour that does not depend on the model's judgement is exercised offline, per task, on the exact
commit and the exact task image, and the paid workflow refuses to dispatch a task that has no
certificate. The 10-task paid run is the *confirmation* of a proof, not the search for the next bug.

Read with: `docs/BENCHMARK-CONFORMANCE-2026-09-16.md` (what works / is broken / is unproven today),
`docs/AUDIT-ABILITY-SPEC.md` (vocabulary and layers), `BENCHMARK_READINESS_STATUS.md` (the ledger).
Repo: `D:\gt-context-plan`, branch `codex/phase5-harness-proof`. Every commit auto-pushes (HAR-58 hook).

## 0. Definitions

**Clean from GT's side** for one task means all of the following are true in the artifacts, regardless
of whether the model solved the task:

| # | check | where it is read |
|---|---|---|
| C1 | terminal ∈ {`submitted_verified`, `submitted_unverified`} — never `timeout`, `internal_error`, `setup_error`, `provider_failed` | `agent/gt-run.json` `terminal` |
| C2 | product receipt status COMPLETED; no `receipt_issuance_failed`; no `gt_degraded_fail_open`; no engine disable | `gt-run.json`, `diagnostics.json` |
| C3 | journal hash-chain valid; `run_terminal` row present; zero orphaned provider requests | `gt-state/<task>/events.jsonl` via `verify_event_journal` |
| C4 | baseline: `captured` **or** a typed honest status (`no_test_command`, `no_tests_observed`, `timeout`) — never `spawn_failed`, never `no_test_verdicts` after the collection retry | `persistent_plans/*.json` |
| C5 | graph: at least one `graph_publication`; zero `amend_failed` streaks without escalation; no `GT_INDEX_MEMORY_GUARD_TRIGGERED` | journal |
| C6 | LSP: every leg WORKING or env-bound with a named reason; `lsp_promotion` row never `required=True` + FAILED | attestation capability rows |
| C7 | plan: built (or typed post-edit); every bound check has a non-empty `protocol`; `plan_gate_decision` rows exist if submit was requested | journal, `plan_check_bound` |
| C8 | feature accounting: zero `unattributed`, zero `triggered=unknown`; every NOT_REACHED names its boundary; no STARVED | `scripts/feature_accounting.py` |
| C9 | every `execution_evidence` row for a test command has `kind=test` and non-`unknown` scope when it is a plain runner invocation | journal + `test_command_shape` |
| C10 | attestation PASS on product integrity (verifier outcome recorded separately) | `deepswe20-attestation.json` `errors == []` for the task |
| C11 | `model.patch` collected (or typed `model_patch_copy_failed`, never `official_verifier_missing`) | `official-verifier-result.json` |
| C12 | cost/tokens recorded (`input_tokens`, `output_tokens`, `cached_tokens`) | `gt-run.json` |

**Certificate**: a JSON file `artifacts/readiness/deepswe10/<task_id>.cert.json` with
`{schema: gt.task_readiness_certificate.v1, task_id, source_sha, container_digest, wheel_sha256,
producer_sha256, checks: {C1..C12: PASS|NOT_APPLICABLE(reason)}, evidence: {run ids, journal sha256},
issued_at}` produced only by the offline pipeline in §3, never by hand. The paid workflow's plan job
must refuse a task whose certificate is missing, whose `source_sha` ≠ SOURCE_SHA, or whose
`container_digest` ≠ the bundle's pin.

## 1. The 10 tasks (proposal; all have pinned images + digests in `config/deepswe_product_bundle_v1.json`)

Two per language, deliberately including the historically failing ones — proving a clean GT side on
the hard cases is the point.

| language | task | why |
|---|---|---|
| python | `aiomonitor-task-snapshots-diff` | the gate-one task; solved once with attestation failure — the receipt path must be clean now |
| python | `adaptix-name-mapping-aliases` | monorepo pytest; ERROR in smoke20 |
| go | `abs-module-cache-flags` | GT-off 4/4; `make test` discovery class |
| go | `actionlint-action-pinning-lint` | GT-off 4/4; `go test ./...` class |
| typescript | `awilix-async-container-initialization` | solved under GT; npm-on-npm class |
| typescript | `arktype-json-schema-refs-dependencies` | pnpm workspace, ERROR in smoke20, the class where discovery emits `npm` on pnpm |
| javascript | `katex-multicolumn-array-spans` | solved under GT; mocha |
| javascript | `csstree-shorthand-expansion-compression` | solved under GT; GT-off 0/4 |
| rust | `pest-character-class-coalescing` | cargo workspace; rust LSP leg env-bound |
| rust | `fd-deterministic-multi-key-sorting` | churn-abort ERROR in smoke20 |

## 2. Why bugs kept appearing, and what changes

Each paid run exposed one environment × capability interaction (container test layout, command shape,
collection abort, `/tmp` sweep, pruned enrichment base, missing patch copy). None of those need a
model to reproduce. They appeared in paid runs because the provider-free gates exercised **one
synthetic task on one image** and the live gate accepted with `min_exercised = 0`. The change:

1. The deterministic stack runs **per task, on the task's real image**, offline (§3 phase A).
2. Every recorded paid trajectory becomes a **permanent replay fixture** driven through the current
   adapter (§3 phase B), so a fix is proven against every run that ever failed.
3. Each capability has a **mutation check** that proves its test detects breakage (§3 phase C).
4. The **certificate gate** (§0) makes the paid run structurally unable to start without 1–3.
5. New failure classes are admitted only through a fixture first (`tests/fixtures/command_corpus`,
   `tests/fixtures/benchmarks`, replay fixtures) — a fix without a fixture is not a fix.

## 3. The offline pipeline (build order)

### Phase A — per-task installed rehearsal on the real image (L3)
Reuse `scripts/gt_installed_rehearsal.py` (Pier + synthetic OpenAI-compatible transport, port 80/443,
external provider calls zero). Today it runs one synthetic repair scenario on one image. Extend:

- `--task-id <id>` selects the task from `config/deepswe_product_bundle_v1.json` (image@digest,
  language, base_commit) and materialises the task exactly as the paid path does (`pier run` with the
  same adapter and environment classes).
- A **task-aware scripted scenario** generated from `tests/fixtures/benchmarks/deepswe_tasks.json`:
  (1) view an anchor file from the issue text; (2) run the repo's declared test command scoped to one
  file (the verifier command family); (3) make one edit to a source file the covering map links to;
  (4) run the same scoped test; (5) run the suite-equivalent command; (6) submit. The transport replies
  are scripted text; no model judgement is claimed.
- After the trial, evaluate C1–C12 from the artifacts and write the certificate. Any check FAIL ⇒ no
  certificate, and the failing check names the owner file.
- New workflow `.github/workflows/deepswe_task_rehearsal.yml`: `strategy.matrix.task` over the 10
  ids, `ubuntu-24.04`, LSP/dense/producer staging lifted verbatim from `installed_rehearsal.yml`,
  images pulled by digest (reuse `deepswe_cache_images.yml` GHCR mirrors), artifacts uploaded as
  `readiness-<task>-<run_id>` including the certificate. Missing asset ⇒ UNPROVEN, never a skipped pass.

### Phase B — recorded-trajectory replay through the current adapter (L2)
Promote the canary harness (session scratchpad `canary_replay/replay.py` + `adapter_replay.py`) into
`scripts/replay_trajectory.py`: input = a recorded run dir (`gt-state/<task>/events.jsonl`,
`output_evidence/`, `provider_responses/`); it recovers each command by sha256 from the provider
responses, feeds `(command, stored output, returncode)` through `MiniSweAdapter.record_execution_evidence`,
`observe_plan_checks`, `note_edit` and the classifier in action order, and asserts per step: no
exception, evidence `kind`, scope, verdicts, advisory, and the terminal C-checks that are computable
offline (C3, C7, C8, C9). Fixtures: cut with `scripts/extract_replay_fixture.py` from the 20 smoke20
task artifacts of run `34801009507` (still downloadable, minimal file set: `agent/gt-run.json`,
`gt-state/*/events.jsonl`, `output_evidence/`, `provider_responses/`), the two SWE-Live runs
(`34996816912`, `35016130850`, `35052806242`) and gate-one runs. Test: `tests/test_replay_corpus.py`
runs every fixture; a fixture whose recorded run failed on a since-fixed class must now pass the
corresponding check (that is the regression proof), and any new FAIL is a new defect with an id.

### Phase C — mutation checks (L4)
For each of the 21 identities, one test that disconnects a boundary in an isolated substitution
(producer returns nothing on eligible input; dispatch disconnected; stale revision; admission drops the
candidate; prepared text never reaches the request; response binding absent; graph ancestor deleted;
conclusion exceeds evidence) and asserts the identity's own test goes RED. Location:
`tests/test_mutation_<identity>.py`. An identity whose test stays green under mutation is a proof
defect and blocks its certificate row.

### Phase D — the certificate gate
- `scripts/issue_task_certificate.py` (evaluates C1–C12 from a rehearsal or replay artifact tree;
  emits the JSON; refuses on any FAIL).
- `deepswe_gt_harness_product_p0731.yaml` `plan` job: for `cohort_stage=subset` with the 10 ids,
  download `readiness-<task>-*` for `readiness_run_id`, verify certificate `source_sha == SOURCE_SHA`
  and `container_digest == bundle pin` per task, else fail the plan job before `provider_gate`.
- `scripts/gt_live_gate.py`: replace `--min-exercised 0` with `--expected-capabilities-from
  <certificate dir>`: an applicable capability (per the certificate's NOT_APPLICABLE set) that is
  silent in the paid run fails the attestation.

## 4. Known defects that must close before any certificate can be issued
(ids from the corpus tests and the TB2/verify reports; each already has a RED test)

1. `deepswe_npm_on_pnpm` / `deepswe_npm_on_yarn` — lockfile-aware manager selection (repo-side
   override in `persistent_plan/baseline.py`; the wheel's `_cfg_package_json` is pinned). Affects
   arktype and every pnpm task.
2. `spawn_failed` class — discovered runner not resolving in the child env for npm/make/go/cargo/tox:
   attempt exec via `sh -lc` / `node_modules/.bin` / `.venv/bin` before declaring spawn failure.
3. `protocol_unknown_js_runners` — `_protocol` vocabulary lacks jest/vitest/mocha/deno; bound checks
   cannot match by protocol on 39 verifier commands.
4. `covering_py_only` — `_TEST_FAILURE_FILE_RE` is `.py`-only (go/rust/ts covering results empty).
5. `swelive_cov_value_options` / `checkspec_admits_env_assignment` / `deepswe_subshell_scope_unknown`
   — parser gaps with pinned rows.
6. Large-suite baseline: collect-only name universe + covering-file subset within budget
   (`persistent_plan/baseline.py`), so C4 can be `captured` on suites > ~300 tests.
7. `def_partition` — 1 delivery in 20 tasks; find why `_produce_def_ref_partition` never admits.
8. Efficacy instrumentation — `action_consistent` is empty on every run; either the measure is broken
   or GT never changes an action. Decide which before the cohort, because the cohort's claim depends
   on it.

## 5. Sequence and exit criteria

| step | done when |
|---|---|
| S1 close §4 items 1–5 with the pinned tests flipping from xfail to pass | corpus tests: 0 xfails for those ids; gates green |
| S2 phase B replay harness + fixtures for all 20 smoke20 tasks and 3 SWE-Live runs | `tests/test_replay_corpus.py` green; every historical failure class asserted |
| S3 phase A rehearsal per task | 10 runs of `deepswe_task_rehearsal.yml`, each producing a certificate on the current SHA |
| S4 phase C mutation checks | 21 identities, each with a mutation test that goes RED |
| S5 phase D gate wired | `plan` job refuses a task without a matching certificate (test: `tests/test_paid_workflow_gates.py`) |
| S6 owner review of the 10 certificates | every C-check PASS or NOT_APPLICABLE with a reason, on the SHA that will be dispatched |
| S7 paid 10-task run (`cohort_stage=subset`, typed approval citing the readiness run id) | attestation PASS on all 10 for product integrity; verifier outcomes reported separately; per-task tokens vs the frozen GT-off slice (`eval/deepswe_v4_flash_gtoff_trials.json`) |

**Not started until:** S1–S6 complete on the same SHA, no open BROKEN row for DeepSWE in the
conformance document, and the certificate directory contains exactly the 10 ids.

**Never:** run GT-off; dispatch a paid run without typed owner approval in-session; fix a paid-run
failure without first adding the fixture that reproduces it offline.
