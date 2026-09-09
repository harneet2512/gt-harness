# nano-harness — Project Memory

Running notes, decisions, and things to remember that are specific to this project.
For Troy-wide context, see `~/.claude/projects/C--Users-Troy/memory/`.

## Decision log

### 2026-05-02 — Project kicked off
- Trigger: Troy noticed agent harnesses trending up; wants to ship something Karpathy-shaped that benchmarks well.
- References inspected: nanoGPT, nanochat, Archon, DeerFlow 2.0 Enhanced, your-claude-engineer, mini-coding-agent.
- Read on the field: feature-rich frameworks dominate; nobody publishes benchmark numbers. That's the wedge.

### 2026-05-02 — Artifact decided: A (minimal coding agent)
- Considered A (vertical coding agent), B (general framework), C (educational nanoharness).
- Verdict: A is the move. C is a free byproduct of building A right (small, readable, documented).
- B (framework) is deferred — premature abstraction is the #1 way these projects fail. If A earns attention, factor a framework out later from a proven reference implementation.

### 2026-05-02 — Benchmark target: Terminal-bench (primary) + SWE-bench Verified (secondary)
- SWE-bench Verified alone: too crowded, >30% is below median in 2026 (top scores ~75-80%).
- Terminal-bench: less crowded, terminal-native (matches harness shape), attention rising. >30-50% is still meaningful here.
- Run same harness on SWE-bench Verified as cross-validation. Multi-benchmark > single-benchmark for credibility.
- Skipped: HumanEval, MBPP, SWE-bench Lite (saturated), Aider Polyglot (Aider's home turf, bad optics).

## Open questions
- Model strategy: frontier-only vs provider-agnostic vs multi-model leaderboard table?
- What does "minimal" mean concretely? LOC ceiling? Token budget? Dependency count?
- Loop shape: ReAct, plan-then-execute, or something else?
- Tool set: how minimal is too minimal? (bash + edit + read = enough?)
- Repo layout: single file vs small tree?

## Constraints to respect
- Solo builder. No team. Realistic build time matters.
- Must run on Troy's hardware (Windows, soon Beelink SER10 MAX).
- Cost ceiling per benchmark run not yet decided — relevant once model strategy is locked.
- No auto-commit; Troy decides when to commit.

## Strategic positioning notes
- Story is "elegance per score point" not absolute score. "300 lines of harness, 45% on Terminal-bench" beats "5000 lines, 60%" for headlines.
- Karpathy frame: the artifact teaches by being readable. Test = can a smart dev read the whole thing in an afternoon?
- Multi-model story (if pursued) is the most viral framing: "same minimal harness lifts every model from baseline to X."
