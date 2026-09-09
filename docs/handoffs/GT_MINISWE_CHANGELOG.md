# GT / Mini-SWE implementation and live-proof record

Last updated: 2026-07-31

## Model reachability

The provider path is not limited to one hard-coded model. `nano.cli.build_provider`
routes names beginning with `claude` or `anthropic` to Anthropic; all other names
use the OpenAI-compatible client. With `OPENAI_BASE_URL` set, the model string is
passed to that gateway verbatim. Without a gateway, provider prefixes are
stripped by the Harbor adapters before reaching nano.

Models/configurations supported by the current routing code:

| Configuration | Route | Reachability evidence |
|---|---|---|
| `deepseek-v4-flash` + `https://api.deepseek.com/v1` | OpenAI-compatible DeepSeek gateway | Provider preflight passed in runs `30615578051` and `30642616424` |
| `deepseek-v4-pro` + `https://api.deepseek.com/v1` | OpenAI-compatible DeepSeek gateway | Officially listed and API-supported; must pass a separate preflight before use |
| `openai/gpt-*` without gateway | OpenAI client | Supported by `build_provider`; not live-tested in this project |
| `anthropic/claude-*` or `claude-*` | Anthropic client | Supported by `build_provider`; not live-tested in this project |
| Any gateway-native model ID + compatible `OPENAI_BASE_URL` | OpenAI-compatible client | Configuration-supported; requires its own preflight |

Only `deepseek-v4-flash` is experimentally proven for this GT work. A model
being accepted by CLI routing is not proof that its credentials, endpoint,
tool schema, or model ID are reachable. Every new model must pass the workflow
preflight before task containers are pulled.

DeepSeek's current official model list contains `deepseek-v4-flash` and
`deepseek-v4-pro`. The official pricing page identifies Flash's current served
version as `DeepSeek-V4-Flash-0731`, lists 1M context and tool-call support for
both, and notes that the Responses API currently supports Flash but not Pro.
Therefore Pro can be used through this repository's OpenAI Chat Completions
path, but it must be a separately labeled experiment; replacing Flash in the
frozen paired run would invalidate the delta.

## Frozen comparison arms

- Mini-SWE/SWE baseline: local DeepSeek GT-off artifact, `83/300`, 300 task IDs.
- Terminal-Bench comparator: local GT-off ten-task vector
  `1,1,1,ungraded,1,1,0,0,1,1`.
- These datasets are disjoint and must never be combined into one score.
- Model, temperature, timeout multiplier, concurrency, task IDs, Harbor version,
  GT commit, wheel, index binary, and grader must be recorded for every pair.

## Implementation commits

### `0d963fc` — Mini-SWE adapter and audit foundation

- Added typed task modes and predicates: PATCH, BUILD_INSTALL, ARTIFACT,
  SERVICE, DATA_TRANSFORM, and MIXED.
- Added `GroundtruthController`, external state storage, provider payload hashes,
  Mini-SWE runtime hooks, and attribution/audit utilities.
- Added the frozen TB2 ten-task manifest and Mini-SWE implementation plan/docs.
- Added focused controller, integration, runtime, and audit tests.

### `4edeb45` — semantic verification and bounded recovery

- Receipts now require explicit semantic evidence before GREEN.
- Added freshness-bound `VerificationPlan` and submit enforcement.
- Added bounded `RecoveryAction`; repeated failures require a materially
  different diagnostic action or terminate as STUCK.
- Added corrective obligation-delta rendering for incomplete contract delivery.
- Added `MiniSweAdapter.evaluate_observation()` using typed verification
  predicates and real command output.
- Suppressed unchanged provider control suffixes.
- Added graph-refresh fallback telemetry and gate accounting.
- Made the transcript auditor consume structured GT-index diagnostics while
  retaining graph failures as explicit events.
- Added per-task feature-opportunity terminal auditing.

### `be3734b` — real Mini-SWE executable entrypoint

- Added `scripts/miniswe_gt_run.py` for Mini-SWE-Agent 2.2.8.
- The entrypoint compiles the task contract, constructs typed predicates,
  installs GT hooks at Mini-SWE's provider/action seams, and writes external
  GT state without modifying the task workspace.
- Added a construction test proving the real Mini-SWE agent and adapter can be
  instantiated with DeepSeek V4 Flash settings.

## Verification performed

- Focused GT/miniswe/audit test families: all collected tests passed.
- Ruff checks passed for all changed implementation and test files.
- `git diff --check` passed.
- Replayed TB2 GT run `30615578051` with the current auditor.
- Replay now parses COBOL index diagnostics without false `UNPARSED` verdicts.
- The replay still correctly reports the underlying graph-refresh fault,
  timeout failures, missing semantic verification, and attribution defects.
- Real Mini-SWE entrypoint construction and `--help` execution passed.

## Live runs

### Run `30615578051`

- Commit: `0d963fc`
- DeepSeek V4 Flash, temperature 1.0, timeout multiplier 1.0, ten TB2 tasks.
- Result: 3/9 graded successes; baseline was 7/9.
- Gate failed due to semantic verification, recovery, graph refresh,
  contract completeness, and attribution issues.

### Run `30642616424`

- Commit: `be3734b`
- Same model, settings, and exact ten-task list as `30615578051`.
- At the time of this record, the Harbor job is still in progress. No score or
  improvement claim is valid until its artifacts and post-run audit finish.

## Remaining proof boundary

The code and local replay checks are implemented, but GT is not yet proven to
outperform either frozen baseline. The next valid claim requires the completed
`30642616424` artifact, a per-task reward delta, a per-task 17-feature table,
semantic predicate receipts, recovery/timeout metrics, and a clean provider-
to-action-to-verifier-to-grader causal chain.
