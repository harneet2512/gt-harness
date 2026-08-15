# GT regression repair implementation — 2026-08-10

## Scope

This pass implements the repair plan for the rejected 89-task treatment
`31355487270`. It is provider-free implementation work. It does not claim a
new solve rate, causal uplift, or efficiency win, and it does not start a paid
smoke.

## Root-cause corrections

### Guidance accounting

The reported `2,264 suppressed` value was a counter-definition bug. The old
counter incremented for private engine effects, so it could not answer how many
grounded candidates failed to reach the model. The new receipt ledger separates
private engine state from provider candidates and records exactly one delivery
disposition per candidate.

The archived treatment is therefore reinterpreted as:

| Quantity | Count | Meaning |
|---|---:|---|
| effects | 2,365 | all deterministic feature effects |
| private engine effects | 2,337 | controller state/actuation, not provider text |
| guidance candidates | 36 | effects eligible for grounded model delivery |
| candidate receipts | 28 | candidates with a recorded delivery decision |
| coalesced provider frames | 26 | provider-visible frames after history coalescing |
| represented in history | 6 | already present; no duplicate insertion needed |
| candidates not delivered | 8 | the real withheld/unselected/stale set |

This is the only valid way to discuss “GT worked”: receipts prove deterministic
observation and controller consumption; a provider payload proves model-facing
delivery; a matched counterfactual is required for causal model influence.

### Graph and source substrate

Validation source revision and graph source revision are now independent. Code
deliverables can be both task outputs and graph-indexable source; data,
serialized artifacts, and report files remain task outputs without becoming
graph nodes. Workspace scanning hashes every bounded batch instead of dropping
files after a fixed 100-file prefix. Large source files are transferred only
when needed, verified against the sensor digest, and then incrementally indexed.
Index subprocess stderr is stored as a bounded diagnostic, so an invalid graph
has an actionable failure reason rather than only `index_unavailable`.

Graph transfers use a unique private temporary directory and verify its cleanup.
The hard-coded `/app` assumption is removed from normal execution: the host cwd
is probed, a configured cwd is validated independently, and fallback is
explicitly receipted. This addresses the `prove-plus-comm` pre-shell failure
without changing provider prompts.

### Resource and context controls

New output hashes no longer count as information gain unless they represent an
actual read anchor or diagnostic. Deadline risk is tracked separately from
step progress. Provider compaction measures current request pressure, may
bound an oversized newest tool observation, and preserves distinct assistant
reasoning. Below the configured trigger, provider history remains byte-for-byte
unchanged apart from exact duplicate turns.

## Verification performed

- Focused changed suites: pass (central runtime, agent loop, repository mirror
  and intelligence, provider view, progress, and semantic engine).
- All-17 census: all producer, consumer, timing, grounded-payload, concrete
  payload, context-accounting, and non-blocking lines pass.
- Readiness audit: `READY`.
- Archived 89-task policy replay: `REPLAY_OK`.
- Archived regression-preservation replay: `ARCHIVED_REGRESSION_REPLAY_OK`.
- Strict lifecycle tests in the pre-smoke gate: pass.
- Full pytest reached 100% with no test failure in the output, but the Windows
  runner timed out during process shutdown; the focused suites exit 0 and are
  the currently reliable completion signal.

## Release status and next step

The repair is committed and pushed as `e38fa06`. The exact
`python scripts/central_pre_smoke_gate.py` now prints `SMOKE_APPROVED`, including
the exact pushed-commit, strict lifecycle, census, repository, language, and
readiness checks. This authorizes only a separately requested ten-task paid
smoke. The 89-task benchmark remains blocked until that smoke preserves the
frozen baseline outcome and passes outcome-first efficiency gates (common
uncensored solves must not regress in reward, calls, steps, actions, or tokens).
