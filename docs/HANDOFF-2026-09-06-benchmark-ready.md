# Handoff — GT benchmark readiness, 2026-09-06

Branch `codex/product-completion`. Ticket **HAR-87**. Written for a session
that picks this up cold.

## The goal, stated the way the owner states it

GT is a **context provider** inside Mini-SWE-Agent's loop. The agent owns
reasoning and actions; the **official verifier alone** decides pass/fail.
"Benchmark ready" is not "the code is nice" — it is:

> we can run the benchmark and the number that comes out is a true measurement
> of GT's contribution.

That requires five things: the run completes; GT is genuinely on and says so if
it isn't; the gates enforce exactly what they claim; the result is comparable to
the official baseline; and it is attestable. Underneath all of it, GT must
supply context **without repeating itself**.

Do not add tests. The suite is large and largely not load-bearing, and the owner
has said so directly. Fix code, run it, prove it.

## Where it actually stands

| # | Requirement | State |
|---|---|---|
| 1 | Run completes | **Cause found and fixed** (`29ca364e`): an unbounded ONNX cache refresh consumed the whole budget before the agent loop. |
| 2 | GT on and self-reporting | Code done. Invisible so far only because no run ever reached the loop. |
| 3 | Gates enforce what they claim | Four defects found and fixed today. |
| 4 | Comparable to baseline | Wired: `deepseek-v4-flash`, official verifier, official leaderboard row. |
| 5 | Attestable | Pinned producer and wheels; receipts bind run to code. |
| — | Anti-repetition | Run-scoped prompt-lane uniqueness now **enforced**, not just implemented. |

## The single most important item — SOLVED, and it was not what it looked like

**A cache refresh was eating the entire run budget before the session existed.**

For most of this ticket the symptom was read as "the journal is written but
never reaches the host". That was wrong. Nothing is lost in transit; the state
directory arrives complete. `events.jsonl` was never *created*, because the
process that creates it was never reached.

The artifact of run `34062325608` says it outright:

| Evidence | Value |
|---|---|
| `index-resource.json` | `elapsed_ms 37404`, `exit_code 0`, `build_attempt_count 1` |
| `graph.manifest.json` | `analysis_state complete`, `core_phase_state committed` |
| `gt-state/<task>/` | `recovery/` only — no `events.jsonl`, no `diagnostics.json` |
| `graph.contract-*.sqlite` | 38MB of pages, `-journal` present, **zero rows** |
| `gt-run.json` | `provider_calls null`, `effective_model null`, `stop_reason timeout` |
| `miniswe_report.json` | `child_returncode -15`, `elapsed 1500.07s`, `deadline_exceeded` |

The graph built **once**, in 36 seconds — so the repeated-rebuild hypothesis is
dead, and so is "killed during indexing". The ~24 minutes between the build and
the SIGTERM went into `ContractEmbeddingStore.refresh`, which embeds every moved
contract through a 110M-parameter ONNX model on two vCPU (~3.5k texts on
arktype). It runs inside `build_agent` at `scripts/miniswe_gt_run.py`, *ahead of*
`MiniSweAdapter` constructing `ExternalStateStore`. No journal exists yet, so the
run cannot even report where it died. The empty sidecar with a live rollback
journal is the only witness it leaves.

Everything the "export gap" framing rested on is explained by this and needs no
second mechanism: `gt_deliveries 0`, `ledger_present false`,
`graph_refresh_count 0`, `capabilities: []` — all literally true, because
nothing had happened yet.

**Fixed in `29ca364e`.** `_embed_plan` now takes a deadline and checks it
*between* batches (never inside one — a batch is a single opaque ONNX call), and
raises `EmbeddingBudgetExhausted` carrying `embedded/planned`. `build_agent`
derives the bound from the run's own budget: `min(300s, 10%)`. Partial work is
discarded rather than published, because the store commits vectors and their
graph bindings in one transaction; publishing a truncated plan would trade a
bounded delay for that invariant. The receipt reports
`embedding_state="budget_exhausted"` with the count.

Executed, not asserted: 320 planned vectors in 10 batches of 32 at 0.2s/batch.
Unbounded → 320 vectors, 2.01s. Bounded at 0.5s → raised at 96/320 after
3 batches, 0.59s. Deadline already past → raised at 0/320, zero batches.

`b2216e69` (copying the journal to the receipt directory) remains harmless and
still worth keeping as a hedge, but it was solving a problem that did not exist.
It is **not** what makes capability rows visible; reaching the agent loop is.

## What is fixed and proven

- **LSP staging** — the manifest generator had a verifier, a test and a consumer
  but no producer (killed run `34046802932`); npm symlinks then broke the
  generator; `pyright-langserver` has no `--version` and exits 1, which would
  have failed the install on every dispatch. All three fixed and **executed in
  the gate task's own image**.
- **The install string** (`eval/miniswe_agent.py:320-372`) now runs outside a
  paid dispatch: root user, `HOME=/root`, root-owned inputs, files-only exec
  strip, `--network none`. `TRUE_EXIT=0`. This is the path that killed three
  dispatches.
- **Acceptance gates**: a refusal allow-list rejecting a value the runtime
  legitimately emits (`cochange_task_ceiling`); a per-decision byte budget
  accidentally made unreachable; a receipt reader requiring global uniqueness of
  a per-revision seal; the same reader failing when promotion *succeeded*.
- **Anti-repetition**: the prompt lane's run-scoped guarantee had no gate.

## Open items, ranked

1. **Confirm the fix under dispatch.** The bound is proven by execution locally; it has not yet been observed on a paid run.
2. **500-edge cap is binding.** `_get_ambiguous_edges` omits `limit`, taking the
   default 500. The gate task has **6,621** ambiguous callsites — 7.5% attempted.
   Worse, the terminal will read `...:N_edges` with positive N and render
   **WORKING** on a 92.5%-unattempted tier. The receipt already carries
   `selection_complete` and `selection_limitation`; nothing reads them.
   Producer change → needs a re-freeze.
3. **FTS over completeness facts.** The 912MB graph is explained: completeness
   facts are per callsite × pass over a closed set (138,194 / 19,639 = **7.037**),
   bounded and intended. But all 181,200 nodes are mirrored into `nodes_fts` —
   a full-text index over rows nobody will ever search. Narrow producer change,
   large effect.
4. **`vendor/gt-index-src/` is not the source of the pinned binary.** No
   `resolution_v2.go`, no `node_type`/`candidate_state`; real source is
   `gt-product-source/gt-index/`. It produced **two** confidently wrong
   conclusions in this ticket. Refresh from `193b9d93b` or delete it.
5. **Sealed lane repeats across decisions**, unbounded, and the gate explicitly
   requires no task-level cap. Product decision, not an engineering one.
6. **`agent.user` hardening** — the install creates `/installed-agent/python` as
   whatever user the agent runs as. All 20 cohort tasks leave `agent.user`
   unset so it is root today; a future task pinning a non-root user breaks it.
   One line: `exec_as_root(mkdir -p {_REMOTE_PYTHON_DIR})`.

## How to run it

```bash
# 1. free gate, always first — it has caught real defects three times
gh workflow run 344675939 --repo harneet2512/gt-harness --ref codex/product-completion
# 2. only on green:
gh workflow run 347688665 --repo harneet2512/gt-harness --ref codex/product-completion \
  -f approve_paid_run=true -f cohort_stage=gate-one -f readiness_run_id=<READINESS_ID>
```

Never dispatch on a red gate. **Never run GT-off** — fetch the baseline from the
official leaderboard instead; that is a standing owner rule.

Read results in this order: official verifier `reward`/`solved`; capability rows;
`lsp_promotion_terminal`; then rebuild count — **a clean solve exercises less
machinery than a messy one**, because the rebuild path only runs if the agent
edits.

## What a single task can and cannot say

It **can** show the machinery works end to end. It **cannot** be compared to the
baseline: `deepseek-v4-flash` is pass@1 **0.5332** over 113 tasks at n_runs=4.
One task is not a rate. There is no per-task baseline in the leaderboard
artifacts, so a solve or a miss stays a coin flip.

## Working method that actually paid off

The failures were all in paths that **only exist when money is being spent**, and
reviewing could not catch any of them. What worked:

- **Execute the path outside dispatch**, in the task's own image, before spending.
- **Reproduce faithfully or not at all.** The install reproduction needed three
  calibrations — too permissive, too strict, then correct. A green run that does
  not reproduce dispatch is worse than no run, because it licenses a spend.
- **Establish the fact before asking what a result permits.** "Step 17 has not
  failed" is equally consistent with "still uploading 10,762 files."
- **State the scope of what was checked inside the claim.** Every count published
  this session moved at least once; each correction came from an objection, never
  from the scan agreeing with itself.

Recurring defect shapes worth watching: a **proxy standing in for the property**
(seven times); a **derivation stopping one level short** (five); **imported is not
reachable** (four); and **a value normalised at one layer and not the one that
decides** (three).
