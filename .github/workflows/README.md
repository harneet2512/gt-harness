# CI workflows

## `tb2_baseline.yml` — Terminal-Bench 2.0 baseline (stock nano, no GT)

Runs the Harbor + Terminal-Bench 2.0 evaluation of the stock nano-harness
(`eval.tb_agent:NanoAgent`) on `ubuntu-latest` (Docker available), so benchmark
runs happen in CI instead of a local machine. Manual dispatch only — every run
spends API credits.

Pinned: `harbor==0.20.0` (the flag set in the workflow was validated against
this version; bump deliberately, not casually).

### 1. Set secrets (once)

Set whichever your chosen model needs:

```bash
gh secret set OPENAI_API_KEY        # OpenAI models, DeepSeek, or any OpenAI-compatible gateway
gh secret set OPENAI_BASE_URL       # only if you route through a gateway (e.g. https://api.deepseek.com/v1)
gh secret set ANTHROPIC_API_KEY     # Anthropic (claude-*) models
```

Per-provider cheat sheet (nano routes `claude*`/`anthropic*` names to the
Anthropic provider, everything else to the OpenAI provider — see
`nano/providers.py` and the README):

| Model choice | Required secrets |
|---|---|
| `deepseek-v4-flash` (default) | `OPENAI_API_KEY` (=DeepSeek key) + `OPENAI_BASE_URL` (DeepSeek's OpenAI-compatible endpoint) |
| `openai/gpt-*` | `OPENAI_API_KEY` |
| `anthropic/claude-*` | `ANTHROPIC_API_KEY` |
| Gemini or anything else | via an OpenAI-compatible gateway: `OPENAI_API_KEY` (gateway token) + `OPENAI_BASE_URL` |

The `base_url` dispatch input overrides the `OPENAI_BASE_URL` secret for a
single run.

**Model-id gotcha (read before changing `model`).** `eval/tb_agent.py` strips a
`provider/` prefix *only when `OPENAI_BASE_URL` is unset*. With a gateway
configured, the `model` string reaches nano — and therefore the gateway's
`/chat/completions` — **verbatim**. So `model` must be the gateway's own native
model id:

| Endpoint | Correct `model` |
|---|---|
| `https://api.deepseek.com/v1` | `deepseek-v4-flash` (bare) |
| OpenRouter | `deepseek/deepseek-v4-flash` (prefixed — OpenRouter's id *is* prefixed) |

Passing DeepSeek the OpenRouter spelling makes every request 400, which
surfaces as "the model can't drive the harness" rather than as a config error.
The **preflight step** exists to catch exactly this: before any task image is
pulled it makes one ~1k-token call through `nano.cli.build_provider` — nano's
real routing, real `max_completion_tokens`, real translated tool schemas — and
fails the job with a clear message if the id, key, gateway, or request shape is
wrong. It costs well under a cent.

### 2. Dispatch

```bash
# 5-task slice (the default)
gh workflow run tb2_baseline.yml

# explicit
gh workflow run tb2_baseline.yml -f model=deepseek/deepseek-v4-flash -f n_tasks=5

# full 89-task baseline
gh workflow run tb2_baseline.yml -f n_tasks=all -f concurrency=4

# a manual shard (task_ids overrides n_tasks; names support globs)
gh workflow run tb2_baseline.yml -f task_ids="hello-world,regex-*"

# watch it
gh run watch "$(gh run list -w tb2_baseline.yml -L1 --json databaseId -q '.[0].databaseId')"
```

Runs dispatch on whatever ref you pass with `--ref` (default: repo default
branch); use `--ref gt-integration` while that is the working branch.

### 3. Results

- **Job summary**: solved/total per reward plus a per-task table, parsed from
  harbor's `result.json`, on the run's page in the Actions tab.
- **Artifact** `tb2-baseline-<run id>` (uploaded even on failure/timeout):
  the whole `results/terminal-bench/` tree —
  `<job-name>/result.json` and per-task dirs with the agent transcript
  (`<task>/agent/nano.txt`), verifier output, and config.

```bash
gh run download <run-id> -n tb2-baseline-<run-id>
```

### Timeouts and sharding

Job timeout is 350 minutes. A full 89-task run at concurrency 4 may exceed it
(budget roughly: 89 tasks x TB2 per-task clock x 2.0 agent-timeout multiplier
/ 4 concurrent, worst case). If it times out, shard: dispatch several runs
with disjoint `task_ids` lists (glob patterns work), then merge the
`result.json` files offline. The artifact uploads on timeout too, so a partial
run is never lost. Disk: the workflow prints `df -h` before/after; for
`n_tasks=all` it first reclaims ~25-30 GB of preinstalled runner toolchains,
since 89 distinct task images can exhaust the runner disk.

### The ladder

1. **5-task slice** (now): prove the CI plumbing + model routing end to end.
2. **Full 89-task baseline** (next): the frozen no-GT reference number.
   Freeze the commit + model + job artifact; all GT comparisons point at it.
3. **GT arm** (`tb2_gt.yml`, live — GT delivered in containers on run
   30501483446): same workflow shape with `eval.tb_agent:GTNanoAgent`, which
   uploads `gt_engine/` + the vendored `groundtruth` wheel + a CI-built
   `gt-index` binary into every task container and runs nano with
   `--gt-root "$PWD"`. Separate workflow so the baseline stays
   byte-for-byte reproducible.

## `swe_gt.yml` — SWE-bench Verified GT arm (nano + GroundTruth)

Same hardened shape as `tb2_gt.yml` (sanitize ALL provider secrets, retrying
provider preflight with cause chain, gt-index build + FTS5 smoke, GT artifact
preflight, artifact-always, score summary), pointed at
`swebench-verified@1.0` with `eval.swe_agent:GTNanoSweAgent`. Secrets, the
dispatch pattern, and the results layout match the sections above
(`-o results/swebench`, artifact `swe-gt-<run id>`).

SWE-specific deltas:

- **`--gt-root /testbed`** by default (the repo location is baked into the
  SWE task images; override with `--ak gt_root=...` / `NANO_GT_ROOT` only for
  debugging). GT's `.gt/` index dir is self-gitignored and removed before the
  model patch is staged, so it can never reach `model_patch.diff` or grading;
  the GT delivery ledger lands at `<task>/agent/gt_ledger.jsonl` in the
  artifact.
- **Disk**: swebench eval images are 1–3 GB EACH, so the free-disk step runs
  unconditionally and defaults are small: `n_tasks=5`, `concurrency=2`
  (task.toml wants 1 cpu / 4G per task). The full 500-task dataset does NOT
  fit one hosted runner — shard with `task_ids` across dispatches.
- **Model id**: default is the **bare** `deepseek-v4-flash`. The model-id
  gotcha above applies with extra force here: with `OPENAI_BASE_URL` set the
  string reaches the gateway verbatim, and DeepSeek 400s the
  `deepseek/`-prefixed spelling — five dead 3-GB containers instead of one
  failed preflight if you skip reading this.

```bash
gh workflow run swe_gt.yml --ref gt-integration                 # 5-task slice
gh workflow run swe_gt.yml --ref gt-integration -f task_ids="astropy__astropy-7606"
```
