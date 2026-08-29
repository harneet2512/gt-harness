---
title: I built a coding agent in ~970 lines of Python and benchmarked it honestly
published: false
description: A 970-line harness scored 59.6% on Terminal-Bench 2.0 — and got roasted 4/10 by another frontier model on the way. Here's the whole arc, bugs and all.
tags: ai, python, llm, opensource
cover_image: # upload docs/assets/banner (or hero-social-square.png) after repo is public
---

**TL;DR:** I built [mini-gt-swe](https://github.com/harneet2512/gt-harness) — a coding agent in **~970 non-blank lines of Python** (5 files, 3 tools, 2 providers, MIT). It scored **59.6% (53/89) on the full Terminal-Bench 2.0 suite** with DeepSeek V4 Flash. Along the way an independent frontier model reviewed the code and gave it a **4/10**, which turned out to be the most useful thing that happened to the project. This is the whole arc — the score, the bugs, and the parts that went wrong.

## Why build a harness at all?

Most agent projects I was following led with features: plugins, orchestration graphs, UI dashboards, memory systems. I had a harder time finding small, readable harnesses paired with a reproducible full-suite benchmark score. That struck me as backwards — the harness is the part of an agent you can actually engineer, so it's the part you should be able to measure.

So the thesis of mini-gt-swe is **score-per-line-of-code**: the smallest readable harness that still puts up a real number on a real benchmark. Think nanoGPT, but for agent harnesses — small enough to read end-to-end in one sitting, honest enough that the number means something.

## What ~970 lines buys you

The whole harness is one loop and three tools:

- **`bash`** — a persistent shell (cwd, env, and state survive between calls)
- **`read_file`** — line-numbered reads with slicing
- **`edit_file`** — exact unique-match string replacement

That's it. No embeddings, no planner, no sub-agents. `bash` subsumes ls, grep, build, test, and install. The model does the coding; the harness has exactly one job: **keep the run alive and keep it honest.**

"Alive" means: retry transient API failures, truncate history before the context window dies, nudge a continuation when output gets cut mid-tool-call, kill and respawn the shell on a hang, and turn every exception into a structured result instead of a crash.

"Honest" means: a failing command must *read* as a failure, and "done" must be earned. The load-bearing piece is a **verify gate**. Once a run has used tools, the first "done" is challenged: *re-read the task, run the relevant checks, prove it.* A later completion is accepted only when successful tool evidence has appeared since that challenge. If the pushback or iteration budget runs out without that evidence, the result is returned as `unverified` — not dressed up as success. Tool-free tasks (a pure question) are allowed to finish normally; there's nothing to verify.

## The benchmark arc: 20% → 80% → 53.9% → 59.6%

I benchmarked on **Terminal-Bench 2.0** via Harbor — 89 tasks, each in its own Docker container, with the exact shipping harness installed per container. My progression, in order:

| Run | Config | Score |
|-----|--------|-------|
| 10-task slice | Haiku 4.5, 50 iterations | 20% |
| 10-task slice | Opus 4.8, matched budgets | 70% |
| 10-task slice | Opus 4.8 + evidence-gated "done" | 80% |
| **Full 89** | DeepSeek V4 Flash, pre-hardening | **53.9% (48/89)** |
| **Full 89** | DeepSeek V4 Flash, post-hardening | **59.6% (53/89)** |

The final run: 16.5 hours, two tasks in parallel, errored trials counted as failures (10 wall-clock timeouts on the heaviest tasks plus one container OOM — all counted against me).

Public Terminal-Bench results are agent-model *pairs*, so model quality and harness quality are entangled in every number. The official 2.0 board is topped by things like Codex CLI + GPT-5.5 (82.2%) and WOZCODE + Opus 4.7 (80.2%). I couldn't find a verified DeepSeek V4 Flash entry on that table, so I'm not going to dress 59.6% up as a clean measurement of "the harness gap." It's my self-run result under the conditions disclosed here — a ~970-line harness against a suite where the tuned, much larger harnesses live in the low 80s — with the code and the task-level record in the repo for anyone who wants to check.

## The part where another model gave my code a 4/10

Here's the part I actually want to tell you about.

After the first full benchmark run, I pasted the five core files into an independent frontier model — a competitor's, no shared context — and asked for an adversarial code review. It scored the code **4/10** and produced a list of findings. The most consequential one was also the best catch:

> **A shell command that failed was reported to the model as success.**

My bash tool captured output but not exit status. `false`, a failing test suite, `grep` with no match — all of it came back looking clean. Which means my verify gate, the "keep it honest" centerpiece, could be satisfied by a *failing* test run. The harness's whole reason to exist had a hole in the middle of it.

So I worked through the findings test-first — every fix landed with a failing regression test before the fix. (Worth stating plainly, since this is an article about honesty: mini-gt-swe was built by modifying mini-swe-agent with heavy AI-assisted coding — I directed the work, made the calls, and ran every benchmark and review, but I did not hand-type all 967 lines. The review gauntlet below used three independent review passes with no shared conversational context, which helped surface different classes of failures.) Then I had a second independent review pass over the result. It scored **6/10** and caught two of my fixes as only *half*-fixes:

- I'd stored tool calls in two places, and my context-truncation pass only shrank one of them — so the OpenAI serialization path silently re-inflated giant tool arguments I thought I'd truncated.
- My CRLF fix preserved line endings for uniform files but homogenized mixed-ending files.

Fixed those too. Then a third pass — a multi-agent cloud review fleet — found exactly **two nits** (a missed retry case for client-side API timeouts, and an explicit JSON `null` argument bypassing a default). Fixed both.

Total damage across the gauntlet: **~20 real bugs, test suite grown from 52 to 87 tests.** Among the fixes:

- Nonzero shell status now raises a tool error, so a plain failing test command no longer counts as successful verification evidence
- The shell's sentinel protocol survives `set -x` (a substring match used to latch onto the trace line and mis-frame every subsequent command)
- `edit_file` preserves file permissions (it used to silently strip the executable bit off any script it edited — on Linux benchmark containers, that's a task-killer), edits *through* symlinks instead of replacing them, and leaves untouched lines' endings byte-identical in mixed CRLF/LF files
- Process-tree kills are verified and reaped — no zombie shells accumulating across timeouts
- The verify gate fails *closed*: out of pushbacks means `unverified`, not fake success

And here's the punchline the benchmark handed me: the pre-hardening harness scored 53.9%. The post-hardening run — same model, same suite — passed five more tasks for **59.6%**, a 5.7-point gain. These are stochastic single runs, so I can't prove every point came from a specific fix. What I *can* say: the intervening changes were correctness and safety fixes, not task-specific benchmark patches, and the next full run scored higher. The biggest single fix was making failure *look like failure* to the model — and it turns out models get further when their harness stops lying to them.

## What this cost

More than I can quote to the exact dollar, and the honest reason is a gap in my own adapter: nano logs token usage to the agent's stdout instead of reporting it back into Harbor's result schema, so the run's summary JSON has null token fields. Adding up the surviving per-task agent logs gives about **3.0M tokens** (≈1.54M in, ≈1.43M out) — and since a later retry pass overwrote a few of those logs, treat it as an estimate, not a receipt. The dollar estimate has been removed pending the correct DeepSeek V4 Flash rates.

## Honesty footnotes

Things a benchmark writeup usually omits:

1. **Errors count as failures.** 11 of my 36 non-passes were infrastructure (agent wall-clock timeouts on the heaviest tasks — in-container QEMU VMs, compiling Doom for MIPS, CIFAR training — plus one container OOM). I counted all of them against the score.
2. **Run-to-run variance is real.** A retry pass on the flaky heavy tasks re-ran them non-deterministically and would have landed a couple of tasks lower. The published 59.6% is one clean, complete, untouched 89/89 pass — the run of record, screenshot and task-by-task breakdown [in the repo](https://github.com/harneet2512/gt-harness/blob/main/docs/benchmarks/2026-07-18-tb2-89.md).
3. **At least one TB2 task is gameable.** `gpt2-codegolf`'s published verifier checks that a file exists, compiles, runs on one fixed prompt, and prints one expected substring — a hardcoded solution could plausibly pass it without implementing GPT-2 at all. I failed that task legitimately and left it failed. If your harness thesis is honesty, you don't get to farm weak verifiers.
4. **It's unsandboxed.** mini-gt-swe executes model-generated shell commands with your full privileges. That's fine inside the benchmark's disposable containers and *only* there. The README says this loudly. Don't point it at a machine you care about.

## Steal this code

The repo is [github.com/harneet2512/gt-harness](https://github.com/harneet2512/gt-harness); mini-gt-swe modifies mini-swe-agent to use GroundTruth deterministic context — MIT, ~970 non-blank lines across `agent.py`, `tools.py`, `providers.py`, `prompts.py`, `cli.py`, with the 87-test suite, the Terminal-Bench adapter, the full benchmark breakdown, and the re-review prompt and resulting fix history in the tree.

If you take one thing from this: **the harness's job is not to be smart — the model is smart. The harness's job is to keep the run alive and refuse to let anyone lie, including the model, and including you.** The 4/10 review was the moment that thesis got real. Publishing the whole arc — the bad score, the half-fixes, the variance — is the point.

*If you review the code and find bug #21, open an issue. That's the game.*
