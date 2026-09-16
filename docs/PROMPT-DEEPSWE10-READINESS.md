# Prompt for the next session: make GT benchmark-ready and prove it offline on 10 DeepSWE tasks

Paste everything below this line into a new session in `D:\gt-context-plan`.

---

You are continuing the GT harness benchmark-readiness work. Read these first, in order, and do not
start editing before you have:

1. `docs/PLAN-DEEPSWE10-READINESS.md` — the plan you are executing. §0 defines "clean from GT's side"
   (C1–C12) and the certificate; §1 lists the 10 tasks; §3 the pipeline; §4 the defects that must close
   first; §5 the sequence and exit criteria.
2. `docs/BENCHMARK-CONFORMANCE-2026-09-16.md` — current verdicts per benchmark and what proves them.
3. `docs/AUDIT-ABILITY-SPEC.md` — verdict vocabulary (WORKING OFFLINE / PARTIAL / BROKEN / UNPROVEN /
   NOT APPLICABLE) and layers (L1 reproducer, L2 runtime integration, L3 installed rehearsal, L4 mutation).
4. `BENCHMARK_READINESS_STATUS.md` — the running ledger; append to it, never rewrite history.
5. `artifacts/audit/benchmarks/deepswe/deepswe_inventory.md` and `deepswe_task_table.json` — per-task
   facts for all 113 tasks (language, verifier command, discovery result, layout, risk flags).
6. `tests/test_deepswe_corpus.py`, `tests/test_command_corpus.py`, `tests/test_baseline_classification.py`
   — the pinned defects; every `xfail(strict=True)` carries a defect id that names the owner file.

## Hard rules
- **Never run a GT-off evaluation.** Frozen anchors only: `eval/deepswe_v4_flash_gtoff_trials.json`
  (deepseek-v4-flash, 113 tasks, n=4, pass@1 0.5332) and `D:\gt_runs\miniswe_tb2_gtoff_20260731`.
- **Never dispatch a paid workflow** (`deepswe_gt_harness_product_p0731.yaml`, `swelive_gt_harness_paid.yaml`)
  unless the owner types approval in this session AND every task in the cohort has a certificate
  (`artifacts/readiness/deepswe10/<task>.cert.json`) whose `source_sha` is the commit you would dispatch.
  Provider-free workflows (`deepswe_gt_harness_product.yml`, `installed_rehearsal.yml`, the new
  `deepswe_task_rehearsal.yml`) may be dispatched freely.
- **A paid-run failure is not fixed until a fixture reproduces it offline** (corpus row, replay fixture,
  or rehearsal scenario) and the fix flips that fixture. No exceptions.
- RED test first, fix at the existing owner, dense comment citing the run/evidence, `ruff check` clean
  on touched files. Do not weaken a test to make it pass; a product defect becomes a strict xfail with
  a defect id.
- `vendor/` (the certified groundtruth wheel and producer) is pinned; never edit it. Wheel-owned defects
  get a repo-side owner (e.g. an override in `gt_engine/persistent_plan/baseline.py`) and a note.
- Every `git commit` in this repo **auto-pushes to origin** (HAR-58 pre-commit hook). Run the affected
  tests before committing, never after. Never bypass hooks. Never use bare `git stash`.
- Another editor (Codex) may commit into this tree concurrently and will sweep uncommitted files into
  its commits. Before editing a file run `git status -s`; give each subagent disjoint files; check
  `git log` for its waves before assuming a defect is still open.
- Work as an orchestrator: dispatch Opus subagents with disjoint file ownership for each §3 phase and
  each §4 defect; review their diffs and test output yourself before committing; report to the owner
  at every milestone in plain language (what landed, what it proves, what is next), not status lines.
- Docker is not available on the Windows host; L3 runs only in CI. On the host, L1/L2/L4 only.

## What to do, in order
1. **Close §4 items 1–5.** For each: run its pinned test to confirm RED, fix at the owner, confirm the
   xfail flips to pass (strict xfail will fail as XPASS until you remove the marker), run the touched
   test files, commit one defect per commit. Then re-dispatch `deepswe_gt_harness_product.yml` and
   `installed_rehearsal.yml` and wait for green.
2. **Phase B replay harness.** Promote the canary replay into `scripts/replay_trajectory.py`; cut
   fixtures for the 20 smoke20 task artifacts of run `34801009507` (`gh run download`, minimal file
   set per plan §3B) and the SWE-Live runs `34996816912`, `35016130850`, `35052806242` with
   `scripts/extract_replay_fixture.py`; write `tests/test_replay_corpus.py`. Every historical failure
   class must be asserted against its fixture.
3. **Phase A per-task rehearsal.** Extend `scripts/gt_installed_rehearsal.py` with `--task-id` and the
   task-aware scripted scenario; add `.github/workflows/deepswe_task_rehearsal.yml` over the 10 task
   ids; add `scripts/issue_task_certificate.py` evaluating C1–C12. Dispatch the workflow; for every
   FAIL, file the defect with an id, fix it, re-run that task only.
4. **Phase C mutation checks** for the 21 identities.
5. **Phase D certificate gate** in the paid workflow's `plan` job and `gt_live_gate.py` expected
   capabilities; test it in `tests/test_paid_workflow_gates.py`.
6. **Report.** Append to `BENCHMARK_READINESS_STATUS.md`: the SHA, the 10 certificates with their
   evidence run ids, every remaining BROKEN/UNPROVEN row for DeepSWE, and a one-paragraph statement of
   whether the 10-task run may be requested. Do not request it yourself.

## Definition of done for this session
Ten certificates on one SHA, all C1–C12 PASS or NOT_APPLICABLE with reasons; corpus and replay tests
green with zero xfails for the §4 items; provider-free gates green on that SHA; mutation checks for all
21 identities; the paid workflow structurally unable to dispatch an uncertified task. Anything short of
that is reported as exactly what is missing, not as ready.
