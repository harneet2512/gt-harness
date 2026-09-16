# Benchmark conformance — what works, what is broken, and what proves it (2026-09-16)

Scope: DeepSWE v1.1 (113 tasks, 20 pinned), SWE-bench-Live Lite (300), Terminal-Bench 2.0 (89).
Method: every task of each benchmark was reduced to the inputs GT's capabilities consume, the
recorded paid runs were mined for failure mechanisms, and provider-free tests were written so a
regression is loud before money is spent. Verdict vocabulary and layers follow
`docs/AUDIT-ABILITY-SPEC.md` (§5A: WORKING OFFLINE / PARTIAL / BROKEN / UNPROVEN / NOT APPLICABLE;
§4: L1 reproducer, L2 runtime integration, L3 installed rehearsal, L4 mutation).

Source identities: `gt-context-plan` `d448f9fd` (branch `codex/phase5-harness-proof`);
TB2 reproductions on `gt-harness` branch `tb2/host-route-offline-repro` (`dff90fd8`, based on
`a4431c1a`); groundtruth wheel `groundtruth-mcp 1.0.0` sha `56a65a20…`; producer `b47f5bf9…`.
Full per-task tables and inventories: `artifacts/audit/benchmarks/{deepswe,swelive,tb2}/`.

## 1. The shape of each benchmark, and what it does to GT

| | DeepSWE | SWE-Live Lite | TB2 |
|---|---|---|---|
| Route | in-container Mini-SWE + GT engine (`scripts/miniswe_gt_run.py`) | same | **host-owned** `eval/gt_central_agent.py` + `CentralFeatureRuntime`; no baseline, plan, gate or LSP |
| Languages | ts 35 / go 34 / py 34 / rust 5 / js 5 | python 300 | shell/sysadmin/data; 7 git repos, 4 with any test config |
| Scored tests visible to the agent? | **No** (test patch adds them at grade time, all 113) | No (F2P in test patch; 15 instances have F2P files that do not exist yet) | **No on 81/89** (mounted at `/tests` after the agent phase) |
| Test runner the verifier uses | vitest 22, jest 12, mocha 5, go test 35, pytest 33, cargo-nextest 5, … | pytest (292 bare `pytest`, 8 make/tox) | `bash /tests/test.sh` → hidden pytest, 82 via `uvx` |
| Baseline capture budget | 3% of 5400 s, clamped 20–120 s | 3% of 1800 s = 54 s vs median 1,710 tests | not on this route |
| Network | none, all 113 | none | varies |
| Consequence | covering/RED abilities can only see regressions in the pre-existing suite | most baselines cannot capture; ledger starts empty | three abilities structurally dead on 81 tasks; graph abilities degraded |

## 2. What was fixed today (each with a RED-first test, all gates green)

| commit | defect | proof |
|---|---|---|
| `a8eb0a88` | baseline aborted on pytest collection errors (dynaconf: 4 errors, 0 names) | retry with `--continue-on-collection-errors`; proven online: re-smoke 35016130850 captured 30 names, classifier fired 5×, agent submitted before deadline, verifier solved |
| `b407e7f4`, `4f40e73a` (other editor) + `ccec1aa5` | classifier pytest-only, piped/compound commands unread, no names for cargo/go/jest/vitest/mocha/npm | segment parser + `gt_engine/test_names.py`; corpus of 964 real model commands pinned (unknown rate on real invocations: pytest 11.7%, npm 13%, js 12.9%, cargo 12.9%, go 25.4%) |
| `4f400d37` | rehearsal auditor rebuilt the evidence line without the new clauses → every classified run would fail the bind | row-only keys merged over the hashed blob |
| `cadd778c` | cohort compare read a `usage` key no receipt has → tokens always null | reads receipt shape + attestation; smoke20 frozen result recorded |
| `1b174b87` | feature accounting: 19 unattributed evidence items, 7 identities unknown | every identity resolves; covering_red exposed as the one that measurably failed on dynaconf |
| `27fb11a6` | rust LSP leg never project-ready classified as required failure | env-bound reason `project_not_ready:readiness_budget_exhausted` |
| `4488ef8e` | failed `model.patch` copy filed as `official_verifier_missing` | typed `model_patch_copy_failed` from the runner manifest |
| `9d945b3d` | audit counted a graph-refresh event no emitter writes → `graph_refreshes: 0` always | counts the emitted names; fake-green test pinned to its branch |
| `d448f9fd` | `submitted_verified` on a blind baseline; plan built after the first edit claimed "before implementation began" | terminal consults the gate's `completion_proven`; post-edit builds typed and stale anchors named; checkpoint layout v5 |

Provider-free gates on the last head: acceptance and rehearsal dispatched (`35055408506`, `35055410186`);
previous head `d9dd5809`: both green (`35049683364`, `35049685863`).

## 3. Per-benchmark capability verdicts (condensed; full tables in the inventories)

### DeepSWE
- **WORKING (offline-proven, online-observed):** localization, obligations, select_catalog, cochange_prior, caller_contract deliver on 20/20 tasks. Scope parsing and test naming now cover all five languages (corpus-pinned).
- **PARTIAL:** baseline capture — collection abort fixed, but `spawn_failed` for npm/make/go/cargo/tox runners is untested (both newest paid tasks were Python); discovery still emits `npm test` on 17 pnpm repos (xfail `deepswe_npm_on_pnpm`, wheel-owned). LSP: TS/JS legs fixed at `aef3a2f5` and observed working; rust legs now env-bound, never useful (no cargo in images). Graph amend escalation fixed (942→0 failures across three runs).
- **BROKEN:** `def_partition` (1 delivery in 20 tasks); `protocol` unknown for jest/vitest/mocha/deno (xfail `protocol_unknown_js_runners`, 39 rows) so bound checks cannot match by protocol; `_TEST_FAILURE_FILE_RE` is `.py`-only (xfail `covering_py_only`).
- **UNPROVEN:** covering_red, recovery, signature_delta, syntax_result, submit_refusal on real tasks — their trigger (a failing test caused by an edit) has never been constructed offline; gt-index on repos > 200 MB (typed refusal exists, real OOM never exercised); 20-way provider concurrency.
- **Efficacy bound (not a bug):** 48–58% of deliveries `seen_no_action_on_content`; `action_consistent_features` empty on every task of every run; the live gate accepts at `min_exercised = 0`.

### SWE-bench-Live Lite
- **WORKING:** baseline capture on small suites (S1, 38 instances); classification and advisory (proven online on dynaconf).
- **PARTIAL:** baseline on medium/large suites (S2+S3, 204 instances) — 54 s budget, no covering-subset or collect-only mode (design gap, owner `persistent_plan/baseline.py`); `--cov*` option values read as test paths (xfail `swelive_cov_value_options`, 13 instances); pyright empty answers for ~40% of units on `src/` layouts with readiness excluded as the cause (matrix row `pyright_empty_answers_project_ready`, open).
- **BROKEN:** covering-test mapping for the 15 instances whose F2P tests are new files (structurally unreachable); grader/GT id truncation on space-bearing parametrized ids (xfail `swelive_failed_name_space_id`, 254 instances carry truncated ids).
- **UNPROVEN:** L3 installed rehearsal over more than one image; behaviour under the 1800 s rail on S3.

### Terminal-Bench 2.0
- **NOT APPLICABLE by design (correctly reported):** cochange_prior, persistent_plan, plan_gate (replaced by `persistent_execution_state`, which reports `not_applicable_no_supported_source` on class F).
- **PARTIAL:** select_catalog (only ability with real host-route coverage); localization/def_partition/newfile_precedent/signature_delta fire from the agent's own search output without a graph.
- **BROKEN (24 strict xfails on `tb2/host-route-offline-repro`):** a transport death is byte-identical to a command exiting −1; the docker timeout string never reaches `probe_timeout`; a dead container still ends `Submitted`; invalid UTF-8 output aborts the run; `central_receipt.json` declares no integrity class; replay-fixture extraction fails untyped; class D (46 tasks) reads as `SUBSTRATE_FAILURE`; a deliberate `enable_repository_intelligence=False` reads as substrate failure; `_run_lint` swallows exceptions silently; bare `make` exit 0 books a passing test. covering_red / recovery / submit_refusal red-leg never fire on any class, including A/B where a test boundary genuinely occurs.
- **UNPROVEN:** everything graph-backed (no `gt-index` binary offline); L3 (needs an image).
- **Historical signal:** the only GT-on TB2 run scored 53/89 against the 66/89 GT-off anchor (not a controlled A/B).

## 4. Frozen anchors for the cohort decision
- DeepSWE GT-off `deepseek-v4-flash`: pass@1 0.5332 (241/452), 113 tasks, n=4. Never re-run.
- Matched 20-task smoke20 (run 34801009507, commit `aef3a2f5`): total tokens at parity (19.13 M vs 19.39 M), **uncached 10.6×** (2.21 M vs 0.21 M), solved 6/18. Open question for the cohort: provider-route confound (native DeepSeek vs OpenRouter→relace) vs real.
- TB2 GT-off: 66/89 frozen at `D:\gt_runs\miniswe_tb2_gtoff_20260731`.

## 5. What is still required before a readiness claim
1. Construct the missing positive path offline: an induced pre-existing-suite regression on one representative per DeepSWE class, so covering_red / recovery / submit_refusal are proven, not INELIGIBLE (L2, `run_loop_real_repos.py` on real checkouts).
2. Baseline for large suites: collect-only name universe + covering-file subset within budget (SWE-Live S2/S3, 204 instances).
3. Runner resolution for non-Python baselines (`spawn_failed` class) and lockfile-aware manager selection (wheel `_cfg_package_json`; repo-side override is the cheap owner).
4. TB2: the two host-route blockers stay open until the 24 xfails flip; class D applicability and the transport-fault distinction are the first two to fix.
5. Gate thresholds: replace `min_exercised = 0` with per-task applicable-capability sets so a silent applicable ability fails attestation.
6. Then: fresh paid 2-task SWE-Live smoke (typed approval), DeepSWE gate-one → remaining-19, cohort report against the anchors above.
