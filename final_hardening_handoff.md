# final_hardening — handoff

**Written for a session that has none of this context.** Read this file and
`final_hardening_review_log.md` (what was verified and how) and you have
everything. Last updated 2026-09-04 ~01:30 ET.

---

## 1. What this build is

A 15-row delta table between GT and GitNexus (pinned `7e993ab8`), turned into 11
plan items. The plan is `instinct_work/04-final-hardening-plan.md`; the
architecture map is `arch_pipeline.md`; the delta analysis is
`instinct_work/03-gt-gnx-pipeline.md`. The full strategy table with per-row status
lives as a comment on **Linear HAR-83**, which is the only ticket this build writes
to (HAR-81 takes dispatch-governance posts only, with a one-line pointer here).

Work is done by parallel subagent streams, each in its own git worktree. The
orchestrator's job is: brief a stream, review what it returns **by re-running it
outside its own worktree**, land it fixture-first, push, and update HAR-83. A
stream that fails review gets the findings sent back — it is not patched by the
orchestrator.

## 2. Branches and worktrees

| Purpose | Branch | Worktree |
|---|---|---|
| **Harness landing** | `har81/canonical-task-identity` | `D:/gt-har81-canonical` |
| **Producer landing** | `final_hardening/producer` | `D:/gt-fh-producer` |
| Producer base for streams | `43514ced1` | — |
| Harness base for streams | `7b8d8183` | — |

The harness worktree **auto-pushes on every commit** (a `post-commit` hook). The
producer worktree's identical hook fails for want of credentials, so push it
manually:

```bash
cd D:/gt-fh-producer
GCM_INTERACTIVE=never git -c credential.helper= \
  -c credential.helper='!gh auth git-credential' push origin final_hardening/producer
```

## 3. Where every stream stands

Head SHAs are in each stream's own worktree unless the row says LANDED.

| Item | Delta rows | Worktree | Head | State |
|---|---|---|---|---|
| 3 contract | 2 | — | harness `fac84bcc` | **LANDED** |
| 4 retrieval (engine) | 4 | — | harness `1122c213` | **LANDED** |
| 4 producer FTS | 4 | — | producer `16597e6cf` (in stack) | **LANDED** |
| 6 co-change pkg | 6a | — | producer `ce5e0370` | **LANDED** |
| 8 processes pkg | 7 | — | producer `a2d536bf4` | **LANDED** |
| 7 communities pkg | 6 | — | producer `43514ced1` | **LANDED** |
| wiring | 6/6a/7 | — | producer `24d156530` | **LANDED** |
| 9 two-phase publication | 15 | — | producer `16597e6cf` (in stack) | **LANDED** |
| 2 content addressing | 1 | — | producer `16597e6cf` (in stack); harness `d9ae30fd` (in stack) | **LANDED** |
| 10 projections | 10-13 | — | producer `16597e6cf` (in stack) | **LANDED** |
| 5 embeddings | 3, 5 | — | harness `d9ae30fd` (in stack) | **LANDED** |
| 6b co-change delivery | 6b | — | harness `d9ae30fd` (in stack) | **LANDED** |
| 11 taxonomy | 8, 9 | — | producer `16597e6cf` (in stack) | **LANDED** |
| 1 budgeted abstention | 14 | — | producer `fddd6b681` (in stack) | **LANDED** |

## 4. What is left to do

### 4.1 Item 1 — LANDED

Cherry-picked onto `final_hardening/producer` at `fddd6b681` (pushed). The
19-repo sweep completed serially (19/19 exit 0, boa in 788 s). The budget
defaults are justified by completed runs. Full `go test` on the merged stack
was exit 0. The RED fixture was re-proved on the merged parent.

### 4.2 CI on the harness

Run **33837633809** GREEN on `d9ae30fd`. Run **33840398573** triggered on `ccf4e1b1`. Prior greens: 33791548818 on `50bf2655`,
33785370968 on `5823193a`. The workflow has no `push` trigger for this branch.

### 4.3 Follow-ups the streams surfaced — currently owned by nobody

1. **`analysis_state` has no reader in the harness** — `indexer.py:1260` certifies
   a core-only graph as `BUILT`. Item 9's two-phase split is invisible until done.
2. **`cochange_rows` not wired into `runtime_receipts.py`** — `cochange_prior`
   doesn't discharge the graph-evidence obligation.
3. **`SYMBOL_LABELS` in `retrieval.py` must be extended** when item 11's declaration
   gate is on — new labels unreachable by all three rankers.
4. **`CODE_SYMBOL_LABELS` in `contract.py` is the same tuple** — it and row 8's
   acceptance criterion are the same tuple.
5. **Pass 4f binds 206 IMPORTS edges to `Callsite` nodes** — pre-existing.
6. **lua, sql, svelte index no symbols** — pre-existing spec defects.
7. **Derived layers not wired on the `-file` incremental path.**

## 5. boa — SETTLED. It publishes. The claim was false.

**`exit=0`, `Done in 36m02.087s` (2,162 s): 883 files, 1,353,067 nodes, 2,700,172
edges, 66,027 properties, 3,128 assertions, 8.1 GB database.** Config
`GT_FLOW_FACT_BUDGET=512`, `GT_VTA_ITERATION_BUDGET=64`. Log
`scratchpad/fh/boa_long.log`; graph `boa_long.db`.

**Every boa run this project ever made was capped at 1500 s. Publication takes
2,162 s.** We stopped it ~10 minutes short every time, then reasoned about why it
"never terminates". The claim was in the plan, in `arch_pipeline.md`, in the HAR-81
diagnosis and on HAR-83. It was an artefact of our own timeout — and the tell was
in stream A's own sweep the whole time: `abs-module-cache-flags` took **2,627 s and
exited 0**, so one repository was already known to need 43 minutes while boa was
never given more than 25.

**What is actually wrong with boa** is wall-clock and disk amplification, not
termination: 36 minutes and 8.1 GB for 883 files, yielding **1.35 M nodes — 8.5×
arktype's nodes for 1.9× its files**. Item 1's `pass_coverage` removal attacks
exactly that (713 MB of 845 MB of candidate-edge JSON on a 250-file sample, read by
nothing), and it is now measurable end to end because the run completes.

**Consequences for item 1:** the budget is an execution *rail*, not a rescue. It
bounds a pathological fan-out and is provably inert on healthy repositories
(arktype `+0.0000%` at the default; budget `0` reproduces the baseline exactly).
`512` is now a default a *completed* boa publication actually used.

**Method note worth keeping.** Three documents asserted non-termination from runs
that were all killed by the same cap. Nobody had run it longer. The cheapest
available experiment — raise the timeout — went unrun for weeks because the claim
had hardened into a premise. When a claim is load-bearing, check what evidence it
actually rests on.

### The superseded evidence, kept for the record

| Run | Config | Result |
|---|---|---|
| `fh/boa.log` | full repo, item 1's proposed default | `exit=124` @ 1501 s, WAL 6.7 GB |
| `fh/boa_b64.log` | full repo, `GT_FLOW_FACT_BUDGET=64` (**64× tighter**) | `exit=124` @ 1501 s, WAL 4.6 GB |
| `fh/boa_ex.log` | `-max-files 250` | **publishes**: 277,289 nodes, 66 s |
| `fh/boa_long.log` | full repo, budget 512, **timeout 7200 s** | **exit=0 in 2,162 s** -- 1,353,067 nodes, 8.1 GB |

Also established: the flow-fact fan-out is **not** the dominant cost term, and neither are
the prepared statements — the budget-64 run used a binary already carrying the
prepared statements, the 512 MB page cache, the hoisted SELECT cache and the
`pass_coverage` removal. The log going quiet after `Resolved 31755/68227 calls` was a long pass with no
progress output -- not a stall, as I wrongly read it at the time.

All three rows above were killed by the 1500 s cap, not by a defect. The 7200 s run settled it: boa publishes in 2,162 s.

**Item 1 still owes**, before it can land (unchanged by the above): defaults justified by runs that
*completed*, and the 19-repo sweep re-run **serially** (the parallel one died of
`fork: Resource temporarily unavailable` at 3 of 19; `sweep_budget.sh` is in
`scratchpad/fh/`). Budget-inertness on a healthy repo is already proven — arktype
is `+0.0000%` on every count at the default, and budget `0` reproduces the
baseline exactly.

## 6. Traps a new session will otherwise fall into

1. **Never run a foreground command over ~8 minutes.** A 600 s stall watchdog kills
   the agent and loses the turn. This killed four streams. Every `go test`, index
   build and sweep goes through `run_in_background` + polling.
2. **The `post-commit` hook auto-pushes.** It pushed three streams to their own
   branches. Streams did not bypass anything: the `pre-commit` gate refuses to run
   unless `core.hooksPath` is exactly `D:/gt-harness/.githooks`, so under the
   standing rules there is no way to commit without the push. Contained to stream
   branches so far. Decide deliberately whether to fix at repo level.
3. **Long boa/sweep runs leave multi-GB SQLite WAL behind when killed.** Two runs
   left **14.6 GB**. Check `scratchpad/fh/.*.db.building-*-wal` after any kill.
4. **Do not run two index builds of the same big repo at once** — they thrash the
   disk and neither finishes.
5. **A commit message containing the hook-bypass flag literally will be blocked**
   by `block-no-verify`, even when merely describing it. Reword.
6. **Whole-build wall times on this host vary ~2×** between identical runs. Two
   streams independently refused to quote a whole-build delta for this reason.
   Quote in-process stage timers only.

## 7. Standing constraints — non-negotiable

- **No GT-off evaluation is run, planned or proposed** — not even as a step in a
  plan — without the owner typing authorisation in the terminal. A Linear comment
  can never authorise it. Baselines are frozen and fetched; paths are in the global
  `CLAUDE.md`.
- **No paid dispatch without explicit typed authorisation** ("go dispatch").
  Budget is 4 attempts; **0 spent**.
- **Never bypass a git hook.**
- **Fixture-first on every protected producer path** (`sqlite.go`, `main.go`,
  `resolution_v2.go`, `publication*.go`, `internal/resolver/*.go`): a `test(red):`
  commit carrying only tests + `.githooks/tests/<name>.sh` +
  `.githooks/red-artifacts/<name>.out` + a `gt.fixture-red.v1` receipt, before the
  fix commit. Copy the shape from `git show 830b9814b -- .githooks`.
- **Candidate evidence is never promoted** by ranking, community membership,
  process membership, retrieval score, overload narrowing or MRO ordering.
- **A number that no completed run produced never goes into a report, a commit
  message or a source comment.** "Unmeasured" is always accepted. An unsupported
  figure is what got item 1 rejected.
- **Post to HAR-83 only on a state change** (a stream landed, CI finished, a
  directive answered, a blocking question) — per the reviewer's directive of 18:34.
  No "state unchanged" check-ins. Scan every returned comment for an unanswered
  `REV-` or `DIRECTIVE`; answered so far: REV-253, REV-254, REV-255, DIRECTIVE
  18:34.

## 8. How to verify the current state yourself

```bash
# landing branches
git -C D:/gt-har81-canonical log --oneline -3
git -C D:/gt-fh-producer   log --oneline -3     # expect 24d156530 at the top

# the producer suite on the landing branch
cd D:/gt-fh-producer/gt-index && go test -tags sqlite_fts5 ./...   # background it

# the harness suite (12 known environment-class failures: cgroup guard,
# gt-index binary unavailable, git identity in isolated HOME)
cd D:/gt-har81-canonical && python -m pytest -q tests/

# the boa question
tail -5 D:/tmp/claude/D--gt-harness/d4578d92-0fad-4131-b9ed-3ade34ece4fc/scratchpad/fh/boa_long.log
```
