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
| 1 | Run completes | **Open.** Four dispatches, none reached a verdict. Latest cause was a 25-minute budget; raised to 90. |
| 2 | GT on and self-reporting | Code done. **Invisible in dispatch** until the export fix lands (see below). |
| 3 | Gates enforce what they claim | Four defects found and fixed today. |
| 4 | Comparable to baseline | Wired: `deepseek-v4-flash`, official verifier, official leaderboard row. |
| 5 | Attestable | Pinned producer and wheels; receipts bind run to code. |
| — | Anti-repetition | Run-scoped prompt-lane uniqueness now **enforced**, not just implemented. |

## The single most important open item

**The journal is written but never reaches the host, so every dispatch reports
`capabilities: []` regardless of outcome.**

- `ExternalStateStore` and `DiagnosticJournal` both write to
  `<state_dir>/<task_id>/` (`miniswe_integration.py:225-226`).
- That directory **does** reach the artifact — `recovery/` is in it — but
  `events.jsonl`, `diagnostics.json` and `output_evidence/` are not.
- `/logs/agent` is a **bind mount** (`harbor .../docker.py:202` `mounted=True`),
  so Harbor copies nothing and no allowlist is dropping it. The gate image has
  **no symlink** on that path either. **The mechanism is still unexplained.**
- Consequence: `diagnostics.json` lands in the same unexported directory, so a
  *clean, solved* run also reports `capabilities: []`. The capability rows are
  journal-derived by design, so they cannot even be recomputed offline.
- `gt-audit.json` corroborates: `gt_deliveries 0`, `ledger_present false`,
  `graph_refresh_count 0` for a run that built a 912MB graph and ran 25 minutes.

**Mitigated in `b2216e69`** by copying the journal, diagnostics and incident
replay to the receipt directory, which is demonstrably exported. That is a
workaround, not the root cause. **Finding why the bind mount loses those files
is still open.**

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

1. **Journal export root cause** — above. Workaround shipped; mechanism unknown.
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
