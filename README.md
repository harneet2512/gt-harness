# GT-Harness — GroundTruth × nano-harness

> **[nano-harness](https://github.com/TroyJLorents-GH/nano-harness) by Troy J
> Lorents supplies the small agent loop. In GT-enabled runs, GroundTruth is the
> deterministic evidence engine inside that loop—not an after-the-fact trace
> annotator.**

This repository embeds **GroundTruth (GT)** — a deterministic, LLM-free codebase-evidence
engine — alongside the stock nano-harness. The stock `nano/` loop remains the GT-off baseline.
The active Terminal-Bench GT arm is `eval.gt_central_agent:MiniSweCentralAgent`, with the
host-owned runtime in `gt_engine/`. It builds a bounded source mirror, validates the pinned
graph substrate, selects source-backed facts, and accounts for every provider request without
asking the model to acknowledge GT.

The active GT arm follows this deterministic boundary:

1. **Model selection:** the model returns a Bash action; GT does not predict it.
2. **Preflight:** the host normalizes a typed proposal and runs bounded deterministic checks;
   paid runs currently use SHADOW mode, so the original command executes unchanged.
3. **Execution/postflight:** the host executes the command, then GT observes the result,
   source/workspace revisions, validation status, graph changes, and all 17 feature paths.
4. **Next request:** grounded evidence is delivered at the first eligible provider request;
   private engine state is accounted separately from model-visible text.
5. **Audit:** exact request hashes, lifecycle counters, source revisions, graph provenance,
   replay blobs, and outcome-first metrics make exposure and resource deltas verifiable.

The architecture and behavioral contract are documented in
[`docs/architecture.md`](docs/architecture.md) and [`AGENTS.md`](AGENTS.md). Exposure is not
mislabeled as semantic consumption or causal benefit; the frozen GT-off baseline supplies the
comparison, and a live GT-on efficiency claim requires an authorized matched smoke.
The implementation audit and remaining ten-task gate are recorded in
[`details_done/GT_FINAL_REGRESSION_REPAIR_AND_89_GATE_20260809.md`](details_done/GT_FINAL_REGRESSION_REPAIR_AND_89_GATE_20260809.md).

**With GT disabled
(no `--gt-root`), this harness is byte-identical to stock nano-harness** — that property is
enforced by tests, and it is what makes clean GT-on vs. GT-off benchmark comparisons possible.

All credit for the base harness — the loop, tools, providers, prompts, and the Terminal Bench
adapter — belongs to upstream nano-harness. Everything below this section is its original
README.

---

# nano-harness (the base harness)

![nano-harness — a coding agent in ~970 lines](docs/assets/banner.png)

> Smallest readable coding-agent loop that scores on benchmarks.

nano-harness is a minimal, single-purpose **coding agent** — the code that wraps an
LLM and turns it into something that completes real work in a repository (a loop,
a small tool set, context management, and a system prompt). It is built to score
on agentic coding benchmarks (Terminal-Bench and SWE-bench Verified) while staying
tiny enough to read end-to-end in one sitting: **~970 non-blank lines** across five
files in `nano/`.

The design philosophy is the Karpathy "nano" aesthetic (nanoGPT, nanochat) applied
to agent harnesses: small, legible, no premature abstraction. The differentiator is
**score-per-line-of-code** — most popular harnesses compete on features and never
publish benchmark numbers; nano-harness aims to be a tiny harness with reproducible,
published scores.

> ### ⚠️ Security: this runs model-generated commands with your full privileges
>
> The `bash` tool executes whatever the model emits — in your shell, as your user,
> with your environment, your filesystem, and your network. There is **no sandbox,
> no allow-list, and no workspace jail.** A prompt-injected or mistaken command can
> read your credentials, delete files, or make network calls.
>
> **Run it inside a disposable container** (the benchmark adapter already does this —
> every task runs in its own Docker container). Do **not** point it at a checkout on
> a workstation that holds secrets or anything you can't afford to lose.

## What this is

An agent harness for coding tasks. You give it a plain-English task inside a working
repository; it reads code, runs commands, edits files, runs tests, and reports back.

## What this is NOT

- **Not a general agent framework** — no plugin system, no extensibility for arbitrary
  domains.
- **Not a product with a UI** — no dashboard, no auth, no SaaS. The only surface is a CLI.
- **Not a chat assistant** — it is task-completion-focused, not conversational.

## Features

- **Reactive native-tool-use loop.** Uses the model provider's native tool-calling API
  directly (no ReAct text prompting). The model self-corrects step by step until it
  ends its turn or hits a budget limit.
- **Three tools, that's it.** `bash` (persistent shell), `read_file` (line-sliced
  reads), `edit_file` (unique-match string replace / file create). `bash` subsumes
  `ls`, `grep`, build, test, and install.
- **Provider-agnostic.** Ships with an Anthropic provider and an OpenAI provider behind
  one `Provider` protocol. The OpenAI provider also targets any OpenAI-compatible server
  (vLLM, Ollama, llama.cpp, Together) via `--base-url`.
- **Prompt caching.** The Anthropic provider marks the system prompt and the most recent
  user turn with `cache_control: ephemeral` — a meaningful cost win on long benchmark runs.
- **Context management.** Hard caps on iterations and input tokens, plus automatic
  truncation of the oldest `tool_result` blocks when the message history exceeds a
  character budget (keeps the message + tool-call id, drops the bulky content).
- **Persistent shell.** A single long-lived shell process preserves cwd, env, and shell
  state across `bash` calls; commands run with a per-call timeout that kills the whole
  process tree and respawns the shell on hang. A nonzero exit status is surfaced to the
  model as an error (a failing test can't be mistaken for a passing one). Uses `bash`
  everywhere it exists — including Windows via Git Bash; `cmd.exe` is a last resort only.
- **Benchmark eval harness.** A Terminal-Bench 2.0 adapter (`eval/tb_agent.py`) installs
  the exact local harness into each task container — no benchmark-specific forks — plus a
  structured run/transcript logger (`eval/log.py`).
- **Rich CLI output.** Streamed assistant turns and tool results render as panels.

## Benchmarks

Terminal-Bench 2.0, full 89-task suite, self-run through Harbor (each task in its own
Docker container, the exact shipping harness installed per container):

| Harness | Model | Tasks | Score |
|---------|-------|-------|-------|
| nano-harness | Claude Opus 4.8 | 89 (full) | **59.6% (53/89)** |

The table above is the historical stock nano-harness result, not a GT-on result. The current
GT implementation has passed its provider-free and exact pre-smoke gates, but no new paid GT-on
score is claimed yet. The next authorized measurement is a ten-task matched smoke against the
frozen GT-off baseline; the 89-task GT run remains blocked until that outcome and efficiency
audit passes.

Self-run through Harbor, every task in its own Docker container, the exact shipping
harness installed per container. Errored trials (agent wall-clock timeouts on the
heaviest tasks plus one container OOM-kill) are counted as failures — the conservative
scoring. Measured on commit `0903552`; 16 h 25 m total runtime. Full task-by-task
breakdown and reproduce steps: [`docs/benchmarks/2026-07-18-tb2-89.md`](docs/benchmarks/2026-07-18-tb2-89.md).

An earlier build scored **53.9% (48/89)**. That run predates the correctness hardening
below — the same harness, same model, same suite went from 53.9% to 59.6% while ~20
correctness/safety bugs were fixed and the test suite grew from 52 to 87. The point
isn't the 5.7-point gain; it's that the gain came from making the harness *correct*
(a failing command now actually reads as a failure), not from benchmark-chasing.

### How it got here (honest version)

The first hardened build was sent to an independent frontier model for adversarial
review and scored **4/10** — one finding was a real correctness bug (a shell command
that failed was still reported to the model as success). Every finding was triaged and
fixed test-first. A second independent review scored **6/10** and caught defects the
first pass introduced; those were fixed too. A third pass (a multi-agent cloud review)
found only two nits. The test suite grew from 52 to 87 tests across the process. The
point of publishing this isn't that the harness is flawless — it's that the path from
4/10 to here is visible, tested, and in the commit history.

## Architecture

![The loop at a glance — bash, read_file, edit_file around a tight agent loop](docs/assets/hero-whiteboard.png)

The harness is a small file tree under `nano/`, orchestrated by a single loop.

```mermaid
flowchart TD
    T["task (plain English)"] --> R["Agent.run()"]
    R --> ITER{"iteration ≤<br/>max_iterations?"}
    ITER -- no --> SITER(["stop: max_iterations"])
    ITER -- yes --> TR["truncate history<br/>if over char budget"]
    TR --> STEP["provider.step()<br/>Anthropic | OpenAI-compatible"]
    STEP --> CAP{"input tokens this step ≥<br/>max_input_tokens?"}
    CAP -- yes --> SMAX(["stop: max_tokens"])
    CAP -- no --> KIND{"response?"}

    KIND -- "cut off mid-output,<br/>no tool calls" --> NUDGE["nudge: continue"] --> ITER
    KIND -- "end_turn" --> VER{"verify gate<br/>(skipped if no tool<br/>was ever used)"}
    KIND -- "tool calls" --> EXEC["dispatch tools"]
    KIND -- "no tool calls, other reason<br/>(refusal / filter)" --> SREASON(["stop: that reason"])

    EXEC --> BASH["bash<br/>persistent shell"]
    EXEC --> READ["read_file"]
    EXEC --> EDIT["edit_file"]
    BASH --> APP["append tool_results"]
    READ --> APP
    EDIT --> APP
    APP --> ITER

    VER -- "evidence-backed<br/>(or toolless run)" --> DONE(["stop: end_turn ✔"])
    VER -- "no evidence,<br/>pushbacks + room left" --> PUSH["push back:<br/>prove it with tools"] --> ITER
    VER -- "no evidence,<br/>out of pushbacks or room" --> UNV(["stop: unverified"])

    EX["any uncaught exception<br/>(provider, tools)"] -.-> SERR(["stop: error"])

    DONE --> RES["AgentResult<br/>final_text · stop_reason · iterations · token totals · transcript"]
    SITER --> RES
    SMAX --> RES
    UNV --> RES
    SREASON --> RES
    SERR --> RES
```

The load-bearing detail is the **verify gate**: when the model has used tools, a
"done" is only accepted with a successful tool call behind it since the last
challenge — otherwise it's pushed back to prove the work with real commands, and
if it can't before pushbacks or iterations run out, the result is returned as
`unverified` rather than reported as success. A run that never touched a tool
(a pure question) finishes normally; nothing to verify.

Key design choices:

- **Canonical internal message shape.** The agent keeps one provider-neutral message
  format (assistant messages carry `text` + structured `tool_calls`; user turns carry
  `tool_result` blocks). Each provider re-serializes from this shape into its own wire
  format — the Anthropic provider builds content blocks; the OpenAI provider splits
  `tool_result` blocks into `role="tool"` messages.
- **Provider is a `Protocol`.** Any object with a `model` attribute and a `step()` method
  is a valid provider, which keeps providers injectable for tests.
- **Token cap takes priority over natural completion** — if a step breaches the input-token
  budget, the run stops with `max_tokens` even if the model said `end_turn`.
- **Tools fail loudly.** `edit_file` refuses non-unique matches and refuses to overwrite an
  existing file on create; tool errors are returned to the model as `ERROR:` text so it can
  diagnose and adjust rather than crash the loop.
- **"Done" is earned, not claimed.** A completion is only accepted when backed by a
  successful tool call since the last challenge; an evidence-less "done" is pushed back, and
  if the loop runs out of room the result is kept but labelled `unverified` rather than
  reported as success.

### Module layout

| Path | Responsibility |
|------|----------------|
| `nano/agent.py` | The `Agent` loop, `AgentResult`, history truncation, tool dispatch wiring. |
| `nano/providers.py` | `Provider` protocol, `AnthropicProvider`, `OpenAIProvider`, message normalization, `StepResult`/`Usage`/`ToolCall` models. |
| `nano/tools.py` | `BashTool` (persistent shell), `read_file`, `edit_file`, the `TOOLS` schema list, and `dispatch`. |
| `nano/prompts.py` | The system prompt and an approximate token counter. |
| `nano/cli.py` | `nano run` CLI: argument parsing, provider selection, Rich event rendering. |
| `eval/tb_agent.py` | Terminal-Bench 2.0 (Harbor) installed-agent adapter. |
| `eval/gt_central_agent.py` | Active host-owned Terminal-Bench GT arm and Mini-SWE model loop. |
| `gt_engine/` | Deterministic preflight, postflight, graph, context, replay, and deep-metrics runtime. |
| `scripts/central_pre_smoke_gate.py` | Exact pushed-commit gate required before a paid GT smoke. |
| `eval/log.py` | `RunLog` / `TaskRecord` — structured run manifests and per-task transcript JSONL. |

## Setup / Install

Requires **Python ≥ 3.12**.

```bash
# clone, then from the project root:
pip install -e .            # installs the `nano` CLI entry point

# optional extras
pip install -e ".[dev]"     # pytest, pytest-asyncio, ruff
pip install -e ".[eval]"    # harbor, swebench (for benchmark runs)
```

The project uses a `hatchling` build backend and exposes a console script `nano`
(see `pyproject.toml`). `uv` works as a drop-in (`uv tool install .`), and the
Terminal-Bench adapter installs the harness with `uv` inside task containers.

## Usage

Run the agent on a task in the current repository:

```bash
# default model
nano run "Add a --verbose flag to the CLI and a test for it"

# pick a model (Anthropic auto-detected when the name starts with claude/anthropic)
nano run "Fix the failing test in tests/test_log.py" --model claude-opus-4-8
nano run "..." --model gpt-4.1

# point at any OpenAI-compatible server (vLLM, Ollama, llama.cpp, Together, ...)
nano run "..." --base-url http://localhost:8000/v1 --model my-local-model

# cap the loop length
nano run "..." --max-iterations 50
```

The CLI streams each assistant turn and tool result as a panel, then prints a final
summary line: `stop reason`, iteration count, and input/output/cache-read token totals.
Exit code is `0` when the run ended naturally (`end_turn`), `1` otherwise
(`max_iterations` / `max_tokens` / `error`).

### Benchmark evaluation (Terminal-Bench 2.0)

The adapter uploads the local checkout into each task container, installs it with `uv`,
and runs `nano run "<instruction>"` inside — so the benchmarked harness is byte-for-byte
the harness that ships locally. Requires Docker running on the host.

```bash
pip install harbor
export ANTHROPIC_API_KEY=...
harbor run -d terminal-bench@2.0 \
    -a eval.tb_agent:NanoAgent \
    -m anthropic/claude-opus-4-8 \
    -l 10 -n 2 -o results/terminal-bench \
    --job-name my-run --agent-timeout-multiplier 2.0 -y
# smoke-test a single task first with: -l 1
```

On Windows, `scripts/tb2_slice.ps1` wraps this: it loads the gateway token from
`.env`, forces UTF-8 (Harbor writes result JSON with the platform default encoding
and crashes on unicode otherwise), stamps a unique job name, and supports
`-Resume`/`-RetryErrors`. Results land under
`results/terminal-bench/<job-name>/result.json`.

## Configuration / Environment

Configuration is via CLI flags and environment variables (no config file).

| Env var | Used by | Purpose |
|---------|---------|---------|
| `ANTHROPIC_API_KEY` | Anthropic provider | API key for `claude*` models. |
| `OPENAI_API_KEY` | OpenAI provider | API key for OpenAI models. For local OpenAI-compatible servers a placeholder (`sk-local`) is supplied automatically when unset. |
| `OPENAI_BASE_URL` | eval adapter | Forwarded into task containers alongside the API keys. |

CLI flags for `nano run`:

| Flag | Default | Meaning |
|------|---------|---------|
| `task` (positional) | — | Plain-English task description. |
| `--model` | `claude-opus-4-8` | Model name. Names starting with `claude`/`anthropic` route to the Anthropic provider; otherwise OpenAI. |
| `--base-url` | none | OpenAI-compatible base URL (forces the OpenAI provider). |
| `--max-iterations` | `30` | Hard cap on loop iterations. |

Tunable `Agent` defaults (in code): `max_iterations=30`, `max_input_tokens=200_000`,
`truncation_char_budget=120_000` (≈30k tokens of tool-result content). Tool output is
truncated at 16,000 chars per call.

## Project structure

```
nano-harness/
├── nano/                 # the harness (the part that ships)
│   ├── agent.py          # loop + AgentResult + truncation
│   ├── providers.py      # Anthropic / OpenAI providers behind a Protocol
│   ├── tools.py          # bash / read_file / edit_file + dispatch
│   ├── prompts.py        # system prompt + token approximation
│   ├── cli.py            # `nano run`
│   └── __init__.py       # __version__
├── eval/                 # benchmark glue (not part of the agent itself)
│   ├── tb_agent.py       # Terminal-Bench 2.0 Harbor adapter
│   └── log.py            # run manifests + transcript logging
├── tests/                # pytest suite (agent, cli, providers, tools, prompts, log)
├── docs/                 # design spec, architecture, external review artifacts
├── scripts/tb2_slice.ps1 # Windows Terminal-Bench runner (gateway, UTF-8, resume)
├── pyproject.toml        # deps, scripts, ruff/pytest config (hatchling build)
└── CLAUDE.md             # project context / working norms
```

## Testing

The stock `nano/` tests and the central GT tests are separate surfaces. The central runtime
scope includes `eval/gt_central_agent.py`, `gt_engine/`, and the provider-free certification
scripts; it is not represented by the historical five-file line count above.

```bash
pytest          # configured via [tool.pytest.ini_options]; testpaths = ["tests"]
ruff check .    # lint (line length 100, py312 target)

# provider-free GT certification before any paid smoke
python -m scripts.central_feature_census
python scripts/central_readiness_audit.py
python scripts/central_pre_smoke_gate.py
```

## Notes

- **Status.** The project began in a brainstorm phase (`CLAUDE.md`/`memory.md` reflect that
  early state), but the harness, providers, tools, CLI, eval adapter, and a full test suite
  are now implemented. The benchmark target is **Terminal-Bench primary, SWE-bench Verified
  secondary**, both aiming for a published, reproducible >30% score from the same harness.
- **Minimalism is a constraint, not a vibe.** Per the working norms, any file passing ~500
  lines "without a damn good reason" is treated as a design smell.
- **Cross-platform shell.** `BashTool` prefers `bash` everywhere — on Windows it resolves
  Git Bash and deliberately skips the `System32\bash.exe` WSL launcher (different filesystem
  namespace); `cmd.exe` is a last resort, with its prompt artifacts stripped from output.
- Pass `--model` to target whatever model you have access to; the default is
  `claude-opus-4-8`.
- `docs/superpowers/` holds the formal design spec and implementation plan; `docs/reviews/`
  keeps the external review artifacts referenced above.

## License

MIT — see [LICENSE](LICENSE).
