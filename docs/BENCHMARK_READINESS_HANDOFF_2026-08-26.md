# Benchmark-Readiness Handoff — 2026-08-26

Status: **NOT YET BENCHMARK-READY** — one deterministic blocker remains
(recall floor). All repairs are provider-free and committed; no paid
benchmark was run.

## What the product is

GT Harness 0.9.0: a host-owned repository-intelligence layer for
Mini-SWE-Agent 2.4.6. It converts an exact repo checkout + issue into a
bounded `gt.agent_context.v6` packet (exact edit targets, inspection
roles, certified relations, bounded processes/impact, affected tests)
delivered at the provider boundary with receipts. DeepSWE smoke20 cohort
is the current frozen benchmark set (`eval/deepswe_smoke20_v1.json`,
baseline 15/20 from run 32615305543).

## Working tree / branch

- Worktree: `D:\gt-harness-prerelease-3df01d2`
- Branch: `final/gt-harness-benchmark-update`
- Run SHA: `f8fc94d` (certified 14/14, smoke 32964236004 = GT 11/20 vs baseline 15/20)
- HEAD after this batch: uncommitted (see next section)

## What this batch did (latent-regression sweep)

| # | Fix | File | Regression prevented |
|---|---|---|---|
| 1 | Exact-key obligation dedup (no substring collapse) | `gt_engine/task_contract.py` | `Create foo.txt.bak` silently dropped behind `Create foo.txt` → lost facet |
| 2 | `_DIRECTIVE_RE` covers `fix/update/patch/refactor/bug` family | `gt_engine/task_contract.py` | non-bullet prose obligations never became facets |
| 3 | `_task_cites_path` requires word boundary for bare filename | `gt_engine/repository_context_compiler.py` | extensionless script `config` hijacked edit authority from prose word `config` |
| 4 | Dense-only file anchors excluded from graph-expansion `file_anchors` | `gt_engine/repository_context_compiler.py` | semantically-similar irrelevant file promoted spurious certified public/integration rows |
| 5 | Repository branch/expansion truncation propagates to packet `truncated` | `gt_engine/repository_context_compiler.py` | high-fan-out graph claimed `truncated=false` with partial process/impact |
| 6 | Ambiguity demotion now per-row on unscoped matches (was inverted) | `gt_engine/repository_context_compiler.py` | globally-unscoped cross-file symbol collisions kept as edit targets |
| 7 | All localization tests non-vacuous (positive controls) | `tests/test_localization_regressions.py` | empty/broken compiler passes suite |
| 8 | `mean_edit_target_recall` added to truth report + gate (`--min-recall 0.5`) | `scripts/replay_smoke20_localization.py`, `scripts/localization_truth_gate.py` | abstaining localizer no longer scores 1.0 |

## Scorecard (current, fingerprint-bound truth report)

| Gate | Floor | Measured | Status |
|---|---|---:|---|
| Mean edit-target precision | ≥ 0.7 | 1.00 | ✅ |
| **Mean edit-target recall** | **≥ 0.5** | **0.0845** | ❌ **blocker** |
| Wrong edit targets | 0 | 0 | ✅ |
| Zero-target tasks | ≤ 10 | 12 | ❌ |
| Treatment failures (replay) | 0 | 0 | ✅ |
| Timeout terminalization (boa class) | terminal receipt | boa `RUNNING`/empty patch (smoke 32964236004) | ❌ open |
| Infra-censor typing (katex class) | censor ≠ missing | mush → task-set mismatch | ❌ open |
| Uptake/follow gate | floored | unwired | ❌ open |
| Efficiency vs baseline | Akon caps | unwired | ❌ open |
| Truth report in `hybrid_required` | prod mode | `sparse_only` only | ❌ open |

## Remaining work to reach benchmark-ready (ordered)

1. **Recall lift to ≥ 0.5** (the gate blocker). Plan: GitNexus-style
   decision-point delivery (bounded same-observation process/impact/test
   answers on file-read observations, replacing inspection-only update
   suppression); typed `AMBIGUOUS_IDENTITY` rows with candidate file:line
   lists instead of silent demotion; inspection-candidate structural
   relevance filter (kills `markupsafe`/`my-invalid-action` diversion).
2. **Regenerate the truth report under `hybrid_required`** (provision pinned
   ONNX locally; certification must regenerate, not just check the JSON).
3. **Runtime integrity**: timeout terminalization (boa) + typed
   `infrastructure_censored{reason}` attestation (katex).
4. **Full-distribution proof**: resolve 2 short-SHA tasks (`eicrud`,
   `langchain` — resolve to 40-char via their remotes), build the 113-task
   manifest, staged replay 20 → 113.
5. **Utility measurement** (free, data local): uptake adapter over runs
   `32928374228` + `32964236004`; wire `scripts/deepswe_release_gate.py`
   + baseline per-task `agent_result` join for Akon-style efficiency caps.
6. **Certification**: full provider-free suite + hosted certification 14/14
   at a new exact SHA; update `arch_type.md`/V6 same-change. **No paid
   dispatch** — readiness is declared by the gates, per user directive.

## How to verify / re-run gates

```powershell
# full provider-free suite
.venv\Scripts\python.exe -m pytest -q -m "not external_evidence" --ignore=tests/test_gt_finalstand.py

# regenerate the fingerprint-bound truth report (needs Go + smoke20 repos at D:\tmp\opencode\smoke20_repos)
.venv\Scripts\python.exe scripts/replay_smoke20_localization.py `
  --manifest eval/deepswe_smoke20_manifest.json `
  --state-root D:\tmp\opencode\smoke20_state_v2 `
  --out-json docs/deepswe_smoke20_localization_truth.json

# gate (currently fails on recall — that is the expected blocker)
.venv\Scripts\python.exe scripts/localization_truth_gate.py `
  --report docs/deepswe_smoke20_localization_truth.json `
  --min-precision 0.7 --min-recall 0.5
```

## Key file map

- Compiler: `gt_engine/repository_context_compiler.py`
- Task contract: `gt_engine/task_contract.py`
- Treatment/delivery: `gt_harness/treatments.py`
- Replay + gate: `scripts/replay_smoke20_localization.py`, `scripts/localization_truth_gate.py`
- Flip ledger: `scripts/deepswe_smoke_flip_ledger.py`, `docs/deepswe_smoke20_flip_ledger.{json,md}`
- Cohort: `eval/deepswe_smoke20_v1.json`, `eval/deepswe_smoke20_manifest.json`, `eval/deepswe_smoke20_tasks/`
- Architecture: `arch_type.md` (authoritative), `docs/GT_CONTEXT_V6_IMPLEMENTATION_2026-08-25.md`
- Frozen baselines (do not rerun): local copies per CLAUDE.md (GT-off DeepSWE 4/10 control, TB2 89-task, DeepSWE v1.1 leaderboard pass@1 0.5332)

## Artifacts / data on disk

- GT smoke 32964236004 receipts: `D:\tmp\opencode\run32964236004\`
- GT-off baseline (frozen, 20 cohort tasks): `D:\tmp\opencode\gtoff32615305543\`
- Cohort repos at exact SHAs: `D:\tmp\opencode\smoke20_repos\`
- Replay state (warm graphs): `D:\tmp\opencode\smoke20_state_v2\`
- Full task source (instructions/solutions): `D:\tmp\opencode\deep-swe\tasks\` (113 tasks)
