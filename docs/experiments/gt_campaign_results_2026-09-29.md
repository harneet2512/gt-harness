# GT attached: replicate-campaign results (2026-09-29)

Model for every run below: `stealth/space-bunny-alpha` (OpenRouter). All comparisons are
same-model. Data: campaign runs 36632431695 / 36632435682 / 36632440261 plus earlier stopped
runs (per-trial `result.json`, fetched without `gt-state`), and the same-model GT-off runs in
`D:\gt_baselines\space_bunny_alpha\` (tb2 run1-5, deepswe run5).

## Headline

| Benchmark | GT (per run) | Same-model GT-off (per run) | Net per-task effect |
|---|---|---|---|
| Terminal-Bench 2.0 (89) | 58, 60, 53, 59, 55 (mean 57.0; 285/444 = 64.2%) | 56, 61, 53, 55, 59 (mean 56.8; 284/438 = 64.8%) | at parity (pass@1 65.7 vs 65.8, pass@3 79.5 vs 77.5, 87 common tasks) |
| DeepSWE (113), partial | 26/56 scored GT trials so far | campaign reps 2, 4 + run5 | +2.0 tasks over the 34 tasks GT has finished |

GT does not lose TB2. The earlier "-7 to -11 vs 66/89" came from comparing against a different
model (the frozen 66/89 is `deepseek-v4-flash`, mini-swe-agent 2.2.8, DeepSeek API).

## Where GT wins: tasks the baseline rarely or never solves

TB2 (GT solved; same-model baseline solved at most 1 of 5 runs):

| Task | GT | Baseline |
|---|---|---|
| polyglot-c-py | 1/2 | 0/5 |
| regex-chess | 1/2 | 0/5 |
| train-fasttext | 1/2 | 0/5 |
| sparql-university | 2/2 | 1/5 |
| kv-store-grpc | 1/2 | 1/5 |
| schemelike-metacircular-eval | 1/2 | 1/5 |

DeepSWE (GT solved; baseline solved 0 of its runs):

| Task | GT | Baseline |
|---|---|---|
| dynamodb-toolbox-lazy-recursive-schemas | 2/2 | 0/2 |
| numba-stencil-boundary-modes | 2/2 | 0/2 |
| helm-array-merge-strategies | 1/1 | 0/2 |
| quill-shared-toolbar-focus | 1/1 | 0/2 |
| scriggo-method-declarations | 1/2 | 0/2 |
| tengo-destructuring-bindings | 1/2 | 0/2 |

Losses in the same sense (baseline solved every or nearly every run, GT solved none): TB2
chess-best-move (0/2 vs 5/5), model-extraction-relu-logits (0/2 vs 4/5); DeepSWE
prometheus-transactional-reload-status (0/2 vs 2/2), arktype-json-schema-refs-dependencies (0/1 vs 2/2).

Reading these tables honestly: they are selected by outcome, so each list overstates its side.
The unbiased number is the net per-task effect in the headline. Per-task counts are 1-5 runs;
no single row is statistically significant.

## Where GT blocks

| Cost | GT | Baseline |
|---|---|---|
| TB2 agent timeouts | 52/178 trials (29.2%) | 79/445 (17.8%) |
| DeepSWE agent setup (median / p90) | 145 s / 909 s | 9 s / 9 s |
| DeepSWE steps (median) | 170 | 128 |
| DeepSWE tokens (median) | 20.6M | 13.6M |
| DeepSWE seconds per step (median) | 15.5 | 13.6 |

Mechanisms found in the code (`D:\gt-attached`) and, where marked, in a trajectory:

1. Submit review diffs against `git diff`; with no git repo (TB2 tasks without a repository; how many
   is measured by the gated run's `gt_workspace_gate` journal events) every task literal is
   reported "[not in your changes]" and the agent is told to fix it. Seen in
   `qemu-startup` rep4: `[not in your changes: telnet 127.0.0.1 6665, alpine.iso]`.
2. The stock "Recommended Workflow" is replaced by a code-repo workflow (`gt-query` first,
   `gt-impact`, write tests) on every task, including QEMU/ops/data tasks. Seen in `qemu-startup`.
3. Blocking prebuild during setup (up to 900 s) and a 60 s graph wait on the agent's clock.
4. Index gating is by file extension only (`.c .sh .md .yml` ...), so tiny or non-code workspaces
   still build graphs and pay per-action probes and background rebuilds.

## Improvement plan

1. Gate per workspace: no git repo, or too few source files, means stock workflow, no submit
   review, no auto-injection, no prebuild; `gt-*` tools stay available.
2. Submit review compares against a start-of-run snapshot when there is no git repo, and never
   reports "not in your changes" without a baseline to compare to.
3. Pull-first on real repos: keep the stock workflow plus one line naming the `gt-*` tools.
4. Index in the background; tools report "index building" until ready.

Target: keep the hard-task wins above while bringing TB2 timeouts back to the baseline rate.

Delivered in PR #50 (fix/gt-task-gating): the workflow part of item 1 (a code workspace = git
repository with >= 20 source files, tracked or untracked); the submit review runs only when the
task's starting commit exists (no repository, no git, a refused repository or an empty `git init`
all mean no review, since the diff would be empty); gt-index at nice 19 against the CPU
contention, with a `gt_thin_refresh` receipt per post-turn amend (seconds, graph_fresh,
low_priority) to catch starvation. Not done by design: auto-injection stays on everywhere, and so
does the setup-time prebuild: skipping it on no-repo workspaces only moved the build onto the
agent's clock (the run's own build and 60 s wait), because the `[GT]` blocks still need the graph.
Items 3 and 4 are not started.
Validation: gated TB2 GT replicate r6, run 36663466222 (hbali-stack, commit 3cb94aa1).
