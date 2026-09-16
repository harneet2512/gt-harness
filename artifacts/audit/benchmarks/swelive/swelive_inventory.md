# SWE-bench-Live Lite (300) — GT capability inventory and provider-free test plan

Companion data: `swelive_tasks.json` (300 rows × 54 fields). `discover_test_command`,
`test_command_coverage` and `_protocol` were **called for real** (`real_probe.py`) against 293 synthetic
repo roots rebuilt from GitHub blobs at each `base_commit`, and against all 90 distinct `test_cmds`.
Sources: `D:\gt-context-plan` HEAD `5fa957be` (includes `7f62b891`); wheel =
`D:\still_here\groundtruth\src\groundtruth`. Recorded runs mined: **34996816912** (dynaconf, timeout),
**34907273607** / **34885373005** (gitingest), **34888711134** (dynaconf, clean), **35016130850** (re-smoke).

## (a) Shape distribution

- 300 instances / 70 repos / 293 distinct `(repo, base_commit)`; 100% Python, 100% `log_parser=pytest`;
  PRs 2024-10-01…2025-03-30. conan 30, cfn-lint 26, haystack 16, reflex 12, matplotlib 12, instructlab 11.
- **All 300 images exist** (`starryzhang/sweb.eval.x86_64.<id __→_1776_>`), tag `latest`, amd64 digest
  captured for each. Size: min 0.41 GB, median 0.68, p90 6.84, max 16.52 (`stanfordnlp__dspy-1801`);
  **total 520 GB**.
- Gold patch median 26 lines / 2 files (max 1526 / 60). Problem statement median 1268 chars; hints on 300/300.
- F2P median 2 (max 64). **P2P median 1710, p90 7292, max 18203; 84% ≥ 300.** Classes S(<300) 49, M 115,
  L 114, XL 22.
- `discover_test_command` (real): `config:pyproject.pytest/medium` 187, `config:pytest_ini/medium` 46,
  `extension_fallback/low` 41, `config:setup_cfg/medium` 21, `config:makefile/low` 5. **Command is bare
  `pytest` for 292/300**, `make test` 5, `tox` 3; `name_emitting_argv` → `pytest -v`.
- Scope parser over the dataset commands: suite 216 rows, scoped 93, unknown 28; `_protocol` unknown on 16.
- Config hazards: 52% no `testpaths`; **145 rows (48%) have test files under >1 top-level dir**, 17 have two
  test-ish roots (`tests/` + `tests_functional/`); 15% no pytest config section; 49 `filterwarnings=error`;
  43 `--strict-markers`; 27 `asyncio_mode=auto`; 26% `src/` layout.
- **85% (254/300) carry whitespace-truncated node ids.** Self-consistent today: `grade.py::parse_log_pytest`
  splits on whitespace and keys on `test_case[1]`. 0 dups, 0 F2P∩P2P in the recorded lists.

## (b) Capability × class verdicts (spec §5A vocabulary)

`GT_VERIFY_EXECUTE` is `setdefault("1")` (`miniswe_gt_run.py:678`) — the covering/syntax lanes are
reachable. Mode `advisory`: model-visible, never blocking, no live probes.

| Ability | Precondition | Verdict × class | Evidence |
|---|---|---|---|
| `persistent_plan` | plan built before the first edit | **BROKEN** S3 (116) / **PARTIAL** elsewhere. Recorded 34996816912: `initial_index_ready` at **+262 s**, `persistent_plan_built` **+299.5 s** — but the first `edit_transaction` was at **+157 s**, i.e. the agent edited **142 s before the plan existed**. gitingest (small repo): index +42 s, plan +64 s, first edit after. Plan quality also degrades: `status PARTIAL`, `symbol_basis "name_guess"`, `rows_naming_existing_symbols 0`, abstentions `plan_payload_not_an_object`, `plan_call_returned_no_tool_call:tool_calls`. | run journals. |
| baseline capture | suite finishes in **91.8 s** with ≥1 pass/fail verdict | **BROKEN** S2/S3 (204): bare `pytest -v` over ≥300 tests cannot finish in 91.8 s. **BROKEN** S7 (8): `make test`/`tox`. **PARTIAL** S1 (38): only **14/300** have `testpaths`, P2P<300 and no optional dirs. Recorded dynaconf did **not** time out — it aborted in **4.425 s**, exit 2, `errored=4, passed=0, failed=0` → `no_test_verdicts`, `detail: "…;argv_accommodated:pytest_import_file_mismatch"`. | `baseline.py`; `persistent_plans/e2e5f526….json`. |
| — *raw evidence retention* | — | **BROKEN, all classes.** The 4 collection errors survive only as counters + `output_sha256 2050689744ea…`; the blob is absent and no `ERROR collecting` / `import file mismatch` text exists anywhere in the run tree. Which modules failed is unrecoverable. | run-tree grep. |
| import-file-mismatch accommodation | duplicate test basenames in dirs lacking `__init__.py` | **WORKING OFFLINE but insufficient — 4/300, all dynaconf** (5× `app_test.py` under `tests_functional/`). My tree detector reproduces exactly that set, so it is a validated predictor. The retry fired and the baseline still produced no verdicts. | `cache/collide.json`; recorded `detail`. |
| collection-error retry | literal `Interrupted: N errors during collection` | **WORKING OFFLINE** where it fires: re-smoke **35016130850** (`c645976a`) captured a dynaconf baseline and `baseline_classification` fired **5×** on `basis=baseline`. **UNPROVEN** for the 66 rows with no `testpaths` + optional dirs, where the retry converts an abort into a *longer* run that then hits 91.8 s. Silent on non-English locales / non-pytest runners. | `checks.py` `_PYTEST_INTERRUPTED_RE`; retry shares the capture window. |
| `plan_gate` | baseline ∈ {`intact`,`regressed`} | **BROKEN** on 251/300 by starvation: `_baseline_blind` includes `timeout`, forcing `completion_proven=False`. **PARTIAL** S1: gitingest 34907273607 *did* gate — two submissions refused `unmet_plan_rows` (+303 s, +377 s, both `action_suppressed`), third accepted `no_blocking_evidence` with `baseline_status:"unknown"`, `completion_proven:false`. So the gate fires, but on a blind baseline it accepts anything. dynaconf 34996816912 emitted **no `plan_gate_decision` at all**. | gate.py:151-157; journals. |
| suite ledger / submit advisory | `scope=suite`, or `covers_known_suite` promotion | **BROKEN, all classes — worse than the code suggests.** (i) The agent's *actual* commands are compound: all 10 recovered from 34996816912 are `cd /testbed && python -m pytest … 2>&1 \| tail`, and **10/10 read `unknown`** — the dataset `test_cmds` are the *verifier's*, not the agent's. (ii) `covers_known_suite` needs a non-empty known universe, but **both** recorded baselines produced **zero names** — gitingest captured `passed=30` with `passing_names: []` because the discovered argv was `["pytest"]` with no `-v`. (iii) Green runs are `-q \| tail -N`, so `_parse_passing_test_names` recovers 0 of 375 and 0 of 129 passing ids. The advisory never fired in the replayed run; forced-suite counterfactuals would have fired at +246 s with **2815 s of headroom**. | `canary_replay/replay_report.md` findings 2-6; baseline blobs. |
| — *`covered_prefixes` defect* | — | **BROKEN (latent, unfixed).** `_record_suite_observation` calls `ledger.record_suite_run(...)` **without** `covered_prefixes`, so `_name_covered(name, ())` is `True` for everything: a zero-failure run promotes *every* observed name to `pass`. Action 45 was green only because it `--ignore`d the file holding the ghost. Any loosening of the scope classifier weaponises this. | replay finding 7. |
| scope parser shapes | single pytest segment, `cd`/`git stash` prefixes only | **BROKEN** S4 (23): `hatch run test:unit`, `poe test`, `pdm run test`, `tox -e`, `devscripts/run_tests.py --pytest-args`, `SKIP_APPLICATIONS_TESTS=True pytest keras`, `cat ./scripts/test.sh`. **BROKEN** S5 (12): `--cov-report xml:coverage.xml` → phantom path `xml:coverage.xml`; `--cov-report xml --cov-report term-missing` → `['xml','term-missing']`; `--cov-config .coveragerc`; `--cov jupyter_ai`. `_VALUE_OPTIONS` omits all four flags. | `cache/scope_real.json`. |
| `covering_red` | FACT edge ≥0.7 + **file on disk** | **BROKEN** S6 (15): every F2P file is created by `test_patch`; `_covering_file_on_disk` drops the row (SS-2). **PARTIAL** elsewhere — dynaconf did select `tests/test_base.py`, `tests/test_utils.py` and bound `pytest -v tests/test_base.py tests/test_utils.py`, observed `CHECK_FAILED` then `UNVERIFIED` (`capture_complete:false`). Direct-covering recall ≈0.019; the convention lever carries the win and is convention-biased by construction. | `covering_runner.py:114`; plan blobs. |
| `syntax_result` / `GT_EDIT_CHECK` | ≤3 changed files | **PARTIAL**: fine for 265/300; blind beyond the first 3 files on 35 rows (max 60). | `miniswe_covering.py [:3]`. |
| `localization`, `caller_contract`, `cochange_prior`, `def_partition`, `signature_delta` | graph + LSP promotion | **PARTIAL→BROKEN.** Delivered (`graph_backed_delivery: true`, `cochange_rows: 8569`) but promotion never scheduled: **both** runs report `lsp_promotion … "status":"promotion_not_scheduled"` with 5 servers detected, and per-receipt pyright answers were **empty** for 154–243 of 446–711 units (`failed_empty`, zero errors); best correction rate **62/711 = 8.7%**. dynaconf is *not* a `src/` repo, so the 78 `src/`-layout rows (no `extraPaths`/`pythonPath` anywhere in `groundtruth/lsp/`) are strictly worse. Dense retrieval spent the first **204 s** failing `RuntimeError:graph_snapshot_not_current`. | `lsp_receipts/*.json`; diagnostics. |
| `select_catalog` | current graph at offer time | **PARTIAL**: `select_catalog_abstained … "stale_or_incomplete_graph"` at +0.8 s in **both** runs; recovers later (`selection_accepted`). Cold-start abstention is universal, not task-specific. | journals. |
| `recovery` / `GT_HYPOTHESIS` | repeated failure across edits | **PARTIAL**: `7f62b891` fixed `_classify_test` (23 observed outcomes → 0 `test_result` events on compound/piped commands). Unproven on the 28 `unknown`-scope shapes. | commit message. |
| submission / receipts | valid submission + surviving artifacts | **WORKING OFFLINE post-`7f62b891`**: 35016130850 submitted before deadline, verifier **SOLVED**; the leg still typed ERROR via `receipt_issuance_failed` (enrichment base pruned) and gitingest via `gt_degraded_fail_open` (shared `/tmp` spool). Both repaired with regression tests — not open. | commit `7f62b891`. |
| grading replication | `git apply` → verbatim `test_cmds` → `parse_log_pytest` | **WORKING OFFLINE** for the 2 adapted tasks; **UNPROVEN** for 298 — notably the 8 rows with non-`.py` P2P items and the 254 truncated-id rows. Systemic oddity: **both** cohort runs graded 1.0 with `committed_patch_empty: true`, `head == baseline`, `uncommitted_tracked: true` — the reward comes entirely from the collect hook's worktree diff. | verifier artifacts. |

## (c) Equivalence classes

| Class | n | Representative | Shape |
|---|---|---|---|
| S1 small suite | 38 | `projectmesa__mesa-2394` | P2P<300; only class where a baseline can plausibly capture |
| S2 medium | 88 | `aws-cloudformation__cfn-lint-3798` | P2P 300–2000, `pytest -rA` |
| S3 huge/XL | 116 | `reflex-dev__reflex-4129`, `matplotlib__matplotlib-29431` | P2P ≥2000 (→18203), ≤1739 test files |
| S4 unparseable cmd | 23 | `deepset-ai__haystack-8489` | scope/protocol `unknown` |
| S5 phantom path | 12 | `deepset-ai__haystack-8609` | `--cov-report xml:…` parsed as a path |
| S6 F2P new files | 15 | `run-llama__llama_deploy-330` | covering mapping structurally unreachable |
| S7 non-pytest discovery | 8 | `kubernetes-client__python-2303`, `patroni__patroni-3198` | `make test` / `tox` |

## (d) Provider-free test plan

**L1 — inspect and reproduce (corpus; no fixtures).**
1. `test_swelive_discovery_corpus` — `discover_test_command` over all 293 rebuilt roots must equal the
   recorded `(command, basis, confidence)`. Covers S1–S7.
2. `test_swelive_scope_corpus` — `test_command_coverage` over the 90 dataset commands **and** over the
   10 commands recovered from run 34996816912. RED cases: *"partial test inventory mistaken for complete
   suite coverage"* (S5 phantom paths) and *"`uv --project` wrapper losing the test boundary"* (S4 +
   `cd … && python -m pytest … | tail`).
3. `test_swelive_grade_parity` — synthetic `-rA` logs with space-bearing parametrized ids through
   `grade.py`'s parser and the GT verdict extractor; assert identical truncation. Adversarial: a test in
   neither list whose truncated key equals an F2P key, appearing later as `FAILED`.

**L2 — runtime integration (real GTSession/MiniSweAdapter, `ScriptedModel`/`ScriptedEnv`).**
4. **Baseline/deadline** (S2/S3, 204 rows): scripted suite exceeding 91.8 s → assert `timeout`,
   `captured=False`, `_baseline_blind`, no `completion_proven`, and an advisory that *says* "baseline
   unavailable". Sibling: reproduce dynaconf's 4.4 s `no_test_verdicts` with 5× `app_test.py` in a
   non-package dir and assert the import-mismatch accommodation fires **and** that the raw pytest output
   blob is retained (currently it is not — that is defect #3 below).
5. **Suite promotion** — RED case *"fully named green suite failing to open the advisory"*: run
   `pytest tests/` with (i) empty known universe → `covers_known_suite` False **and** the advisory must
   explain why; (ii) a `captured` baseline whose argv lacked `-v` so `passing_names == []` → assert this
   is treated as an empty universe, not a populated one (the gitingest case); (iii) `-v` baseline →
   promotion fires. Then assert `record_suite_run` is called **with** `covered_prefixes`, using the
   action-45 shape (`--ignore` the ghost file) — it must not promote the excluded name.
6. **Plan-before-edit** (S3): delay `initial_index_ready` past a scripted first edit and assert GT either
   defers the edit-dependent rows or types the plan as post-edit; today it silently builds at +299 s.
7. **Covering mapping on new-file F2P** (S6): graph edge → nonexistent `tests/test_new.py`; assert the
   row is dropped, a lower-ranked real file is still selected, and GT says "no covering test". RED case
   *"`vendor/src/a.py` failure attributed to edited `src/a.py`"*; duplicate-basename variant from the
   4 confirmed collision rows.
8. **LSP starvation** (78 `src/` rows + the 8.7% empty-answer rate): assert `promotion_not_scheduled`
   and empty definitions yield *unavailable*, never "no callers".
9. **Submission cases**: unresolved-RED refusal; SOLVED-with-receipt-failure (35016130850) must stay a
   distinct terminal from engine-disabled; and the `committed_patch_empty` + `uncommitted_tracked`
   path must be an asserted supported outcome, since both recorded solves used it.

**L3 — installed rehearsal.** Extend `scripts/run_loop_real_repos.py` (whose `DEFAULT_REPOSITORIES`
already hold S1–S4 representatives: `dynaconf__dynaconf-1238`, `kedro-org__kedro-4580`,
`keras-team__keras-20396`, `conan-io__conan-17132`, `deepset-ai__haystack-8489`,
`matplotlib__matplotlib-29431`) with a `--swelive-shapes` mode replaying each class's real command shapes
over synthetic transport, provider access denied, evidence labelled synthetic. Vendor replay fixtures via
`scripts/extract_replay_fixture.py` for 34996816912, 34907273607 and 35016130850. Missing checkouts ⇒
UNPROVEN, never skip-pass.

**L4 — mutation.** Each must turn a named test red: remove `--continue-on-collection-errors`;
make `covers_known_suite` true on an empty universe; delete `_covering_file_on_disk`; add
`--cov-report` to `_VALUE_OPTIONS` (the S5 corpus test must notice the *fix*); raise
`BASELINE_MAX_SECONDS` to 1200; pass `covered_prefixes` correctly (the ghost test must flip).

**CI matrix.** All 300 tags resolve with amd64 digests — pin `image@sha256:` for all 300 as the two
adapted tasks do. Verify by `docker buildx imagetools inspect --raw` (300 calls, free). Never bulk-pull:
520 GB; exclude the 30 images >5 GB from per-PR jobs.

## (e) Ranked failure classes nothing currently tests

1. **The suite ledger is dead on arrival — effectively 300/300.** The agent writes
   `cd /testbed && python -m pytest … | tail`; 10/10 recovered commands read `unknown`. Even a *captured*
   baseline yields zero names (both recorded runs). `covers_known_suite` therefore cannot fire, and the
   submit-window advisory never opened despite 2815 s of headroom.
2. **`record_suite_run` without `covered_prefixes` — latent, unfixed.** A green run that `--ignore`d the
   failing file would promote that file's tests to `pass`. Any scope fix detonates this.
3. **Baseline blind → `plan_gate` degenerate — 251/300 (84%).** `timeout` is a blind status; the gate then
   accepts on `no_blocking_evidence`. Raw collection output is not even retained for diagnosis.
4. **Plan built after the first edit — recorded on dynaconf (+157 s edit vs +299.5 s plan);** worst on the
   116 S3 rows where the index cold start is longest. No test asserts ordering.
5. **LSP promotion never scheduled + 8.7% correction rate**, degrading every FACT-tier ability; strictly
   worse on the 78 `src/`-layout rows. Entirely untested and silent.
6. **Scope `unknown`/phantom on 35 rows (12%)** of *verifier* command shapes — a lower bound, since agent
   shapes are worse.
7. **Bare-`pytest`-at-root over-collection — 145 rows (48%)** have tests under >1 top dir; 4 rows have
   confirmed basename collisions (all dynaconf, the recorded failure).
8. **Truncated node ids — 254 rows (85%)**: benign only while both parsers truncate identically; nothing
   pins that invariant. Plus 2 `--maxfail` rows and 8 rows with non-Python P2P items.
