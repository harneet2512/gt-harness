# Handoff — 2026-09-11 — context-compiler fixes + producer rebinding

Branch: `codex/context-plan-integrity`. HEAD: `e8881a38` (localization
fire-once fix).
Producer: `D:\gt-context-producer` at `7db2f460` (final: `4525ef38` features →
`b4d33a0c` ruff-lint → `7db2f460` ruff-format; producer CI `34650897819` all
green).
Vendored wheel sha256: `79cb3547` (rebuilt from `git archive` of `7db2f460`,
installed + identity gate green).
Producer binary: CI build `34650899610` artifact (`545bb28c` executable sha256,
build_tags `netgo,osusergo,sqlite_fts5`).
Review packet `har83-context-plan-producer-7db2f460-ci` committed to review
inbox as `e8a40965` on pinned base `48e8708e`, pushed to a new inbox branch
(the `gt-review-inbox` remote tip `8a5a5b87` had diverged and dropped packets;
do not rebase onto it).

## Why the aiomonitor run burned 638 steps / 115 minutes

Forensic extraction of run `opencode` evidence: 638 assistant turns, 765 tool
executions, ~75-86 `gt-evidence` calls, **zero write-like repository commands**,
no edit transaction, no submission, killed at the wall. The agent spent the run
reading GT's internal delivery JSONs / plan renderings / repository snapshots
through the recovery plumbing — i.e. it treated the harness as the task surface.
`gt-evidence read` is recovery plumbing; the model used it as an exploration
substrate. Baseline comparison: Muse solved the same task in 557s / 74 steps.

## Fixes landed this session (all verified)

| ID | Change | Verification |
|---|---|---|
| F1 | Python `__init__.py` re-export/relative-import chase in Go resolver | 4 Go tests; real repo: import edges 4→39, name_match 71→65; `from aiomonitor import Monitor` now `import`/1.0 |
| F4 | WARNING-tier caller fallback (`_candidate_callers`) when FACT-tier absent | envelope emits on real graph.db; 388 producer tests |
| F6-F8 | `_viewed_files` noise/cd/existence filtering; `normalize_event` no longer synthesizes `file_view` from a bare carrier | harness + producer tests |
| F12/F13 | `_seal_terminated_journals`: torn-tail truncation, orphan provider-request closure (`request-645`), chained `run_terminal`; `effective_model` recovered from journal | 3 seal tests; real run journals re-verify post-seal (640 resp + 4 fail + 1 sealed orphan = 645 req) |
| F14 | shared `normalized_model_id` for receipt comparisons | 86 receipt tests |
| F15 | `_localize` seam wired to `graph_localizer.localize` (FTS5+anchors, no ONNX needed) | real rows on extracted graph.db (`Monitor`, `start_monitor` → monitor.py/cli.py/task.py); 2 tests |
| F16 | composite `a&&b` / `a;b` decomposed into per-segment CheckSpecs at bind AND observe; sound rc attribution (all-&& rc=0→all pass; all-;→last; else abstain) | 8 tests |
| F17 | `producer.dispatch` skip rows (`gt.producer_invocation.v1`, outcome `not_entered` + `skip_reason`) | 4 tests — entered-vs-never-fired now auditable |
| F18 | native Mini-SWE audit derives `graph_available`/`published_revisions` from `graph_publication` journal events | real run: `graph_available: True` |
| F9 | incremental amend: batch `-amend-parent` verified full-fidelity (506/515 nodes retained, 9 re-parsed); `incremental_amend_in_place` stays deliberately undeclared because `-file` rolls back analysis layers (`resolution_complete=0`) — harness already routes via declared `batch_parser_node_reuse_v1` | real-repo end-to-end |

## Delivery→consumption audit (the "helpful, not just working" leg)

`scripts/gt_audit.py` now joins: `provider_delivery` (request_id +
`model_visible_sha256`) → `provider_response` (verbatim response blob via
trajectory `extra.response.id`) → post-delivery action token match
(word-boundary; bare stems excluded — `cd aiomonitor` does not count as
consuming a `monitor.py` delivery).

Real-run verdict: **212/212 deliveries SENT+VISIBLE+SERVED; only 3
`consumed_fair`.** Nearly every delivery confirmed files the agent had already
touched — GT confirmed rather than led. That is now measurable per-delivery
instead of inferred.

## Rebinding (all identity-bound)

Producer `4525ef38` → pinned Docker Linux build (FTS5) → vendored source via
blob-canonical `git archive` (LF; worktree autocrlf mismatch fixed) → wheel
`59571623` → `config/deepswe_product_bundle_v1.json` updated (source_commit,
source_tree, wheel_sha256, producer_sha256, producer_build). Local identity
gate + 308 harness tests green after `pip install --force-reinstall` of the
vendored wheel.

## Acceptance progression (run `34655576496` in flight on `e8881a38`)

| Run | Result |
|---|---|
| `34649782844` | failed: producer commit `4525ef38` was local-only; pushed |
| `34649962502` | failed lineage: stale ancestry path + review packets; repaired via `e8a40965` + manifest `f1ba9c61` |
| `34652173802` | failed reachability: paid workflow had literal `\|\| true`; `df3eb442` replaced with visible non-fatal warnings / `\|\| :` |
| `34653052488` | all gates passed except full Python suite: `test_task_start_localization_delivered_with_graph` saw 2 localization deliveries |
| `34655576496` | dispatched on `e8881a38` (fire-once fix) — in flight |

## Localization fire-once fix (`e8881a38`)

Root cause of the `34653052488` failure: the compiled task-start path admits
localization via `run_evidence_pipeline(commit=False)`, so `seal_delivery`
stamped the dedup key into a discarded chain copy; the `GTDecisionCandidate`
carried no chain heads so `stage_exposure` skipped it — the delivered key
never entered `_dedup_chain`. A later reactive `ranked_localization` fire
(the scripted grep) reproduced the fact and delivered it a second time.
Pre-F15 the producer was a dead seam so the gap was unreachable.

Fix: admission-level fire-once — `kind="localization"` is refused with
`localization_fire_once` once a localization delivery is visibility-committed
or already pending (`_localization_delivered` set at provider-request join,
never at admit — a refused/discarded request does not consume the episode's
one localization). The candidate now forwards `next_chain_head` so the
exposure chain records the delivered fact at join. Refusals are journaled,
so suppression stays auditable. `DELIVERY_REFUSAL_REASONS` is census-derived;
the new reason is registered there. Regression test:
`test_localization_delivery_is_fire_once_per_episode`.

## In flight / remaining

- Provider-free acceptance run `34655576496` on `e8881a38` — must succeed
  before any paid dispatch.
- Trajectory-eval layer (offline, zero spend) under construction: ingests
  `miniswe_trajectory.json` + `events.jsonl` from `D:\gt_runs\33646776586`,
  anchors relevance to gold patch + graph closure (never baseline-trajectory
  votes), computes TTFC / pre-edit recall / revisit rate / first-edit
  accuracy / recovery rate / delivery→consumption funnel with led-vs-confirmed
  split, per-task causal timelines, explicit tuning-vs-eval task split.
- Local Windows suite has known environmental failures unrelated to product
  code: `test_feature_matrix_outcomes` + `test_persistent_plan_baseline`
  (nested pytest `spawn_failed` on Windows) — pass on Linux CI.
- Paid smoke: workflow `347688665` now accepts `cohort_stage=single` +
  `single_task_id`. Next smoke: `bandit-interprocedural-taint-checks`
  (Python, 64-step baseline, 0.75 pass — short + call-graph-shaped).
  REQUIRES explicit `approve_paid_run=true` — user approval still needed.
- Pre-existing suite pollution: `test_generate_v1r_brief_surfaces_importer_top_with_witness`
  fails inside full producer suite but passes alone AND on the pre-change tree —
  environmental ordering issue, not a regression. Left documented, not fixed.
- Linear record (HAR-83 continuing doc) needs updating with this session's
  results per AGENTS.md.
