# final_hardening — review log

Every claim landed, returned or rejected in this build; what was **independently
re-verified** before it was believed; and the exact command a reviewer can run to
check it again.

**The rule of this log:** a stream's report is a *claim*, not evidence. Nothing is
marked verified unless it was re-run outside the worktree that produced it. Where a
claim was **not** re-verified, this log says so rather than implying it was.

| | |
|---|---|
| Producer landing branch | `final_hardening/producer` — worktree `D:/gt-fh-producer` |
| Harness landing branch | `har81/canonical-task-identity` — worktree `D:/gt-har81-canonical` |
| Reference graph | arktype @ `04355e8b`, producer `cffca1fd2` |
| Reference numbers | 458 files · 159,548 nodes · 188,264 edges · 9,233 properties · 3,511 code symbols · 16,431 callsites |

---

## 1. Landed and verified

### Item 3 — behavioural contract (delta row 2) — harness `fac84bcc`

A projection over `properties`; every claim carries its `properties.id`; the output
is byte-identical across runs and **explicitly empty** when no facts exist.

Re-verified: 17 tests re-run. Contract density **1.39 facts/symbol** — the 2.6
headline figure includes non-contract kinds — with **60% of symbols empty**;
`side_effects` on 68 symbols (1.9%), `boundaries` 197, `guards` 240. The emptiness
is reported, not generated over.

```bash
cd D:/gt-har81-canonical && python -m pytest -q tests/test_symbol_contract.py
```

### Item 4 engine half — hybrid retrieval (delta row 4) — harness `1122c213`

Lexical (`nodes_fts`) + property-value + dense (`dense_runtime`), fused by RRF
k=60 with per-source provenance and a **named** degraded reason when dense is
unavailable.

Re-verified: 33 tests re-run; both example queries < 0.1 s over the 379 MB graph; a
property-only hit demonstrated — *"validates empty input"* → `createNode` via a
`param` fact with no identifier match. `nodes_fts` indexes all 159,548 nodes, so
results are filtered to source labels.

```bash
cd D:/gt-har81-canonical && python -m pytest -q tests/test_hybrid_retrieval.py
```

### Item 6 producer half — co-change package (delta row 6a) — producer `ce5e0370`

Bounded **by construction**: 500 commits × C(50,2) = 612,500 pairs worst case; zero
means default; unlimited is unspellable. Every return path carries a `Reason`
(`ok` / `not_a_repository` / `git_unavailable` / `shallow_clone` / `no_history`),
so an answer and an abstention are no longer the same empty table.

Re-verified: depth-1 fixture → `shallow_clone`, 0 pairs. The same repo at
`--depth=500` → **23,720 pairs**, 442 commits, **58 mass commits skipped and
counted**. Reviewer verdict REV-255 GREEN, reached independently at the exact SHA.

### Item 8 — processes package (delta row 7) — producer `a2d536bf4`

Certified `CALLS` paths from an entry point, emitted **only** with a witnessing
`assertions` row — `witness_assertion_id NOT NULL` in the schema.

Re-verified against the graph directly: 105 assertions → 53 with a target (the 52
dropped score exactly 0.0) → **6 entry points, 95 processes**, depth 1:25 / 2:54 /
3:16, deterministic.

The number that matters: **only 3 of 53** test→target CALLS edges are CERTIFIED
(27 CANDIDATE, 23 SPECULATIVE). Pure certified traversal from tests finds almost
nothing — the assertion is exactly the bridge it cannot cross. Published because it
argues *against* the feature's own coverage.

**Corrected during review, before landing:** it joined `resolution_symbols` on
`(path, start_line)`; changed to `CAST(rs.native_id AS INTEGER) = n.id`, the key
every other consumer uses. Its fixture also wrote a synthetic `native_id` string
rather than the node id the producer writes. 12 tests green after both fixes.

### Item 7 — communities package (delta row 6) — producer `43514ced1`

Re-verified: 37 tests re-run, `go vet` and `gofmt` clean; CALLS tier counts
confirmed against the graph (CANDIDATE 3,435 / CERTIFIED 1,402 / SPECULATIVE
1,096) and the 789 cross-file certified rows; the probe re-run on a **fresh copy**
of the fixture returned the headline **byte-identical**.

Measured: 81 communities, 982 files, held-out cohesion 0.4585 [0.4277, 0.4897]
n=988 against 0.0452 chance — ~10× lift.

**The finding that outranks the headline**, and the reason cohesion had to be
falsifiable:

- **848 of the 988** scored pairs sit in a single 154-file test community.
- The **unweighted mean** over the 20 measurable communities is **0.0611**.
- **61 of 81** communities were unmeasurable on a 29-commit holdout — stored
  NULL-with-reason, never 0.0.
- `ark/docs/components/playground/` scores a **perfect structural 1.000** against a
  **held-out 0.000** over 35 pairs. Two different claims; only one can be right.

Trust weighting, measured: certified structure alone clusters 165 files at 0.1007;
adding co-change clusters 982 at 0.4585 (4.6× rate, 6× coverage); amplifying
structure (`w_call=30`) gives 0.5566. The algorithm string is
`cpm_leiden_deterministic_v1`, not `leiden`, because randomised merge selection was
traded away for byte-identical re-runs.

---

## 2. Returned, under review, not yet landed

### Item 2 — content addressing (delta row 1)

| Half | Commits |
|---|---|
| Producer | `4c039dd6b` (RED) → `bd90b6f34` (fix) → `64d8cc23a` (report) |
| Harness | `09586be9` (RED) → `0b159773` (fix) → `6909e3de` (report) |

**Re-verified by me, outside the worktree that produced it**, against
`scratchpad/item2_measure/ark_item2.db`:

| Check | Result |
|---|---|
| Function / Class / Method / File carrying all of `file_hash`+`byte_start`+`byte_end` | 2,747 / 350 / 314 / 100 — **100.0% each** |
| Acceptance query: code symbols with `byte_start IS NULL` | **0** |
| nodes / edges / properties vs baseline | 159,548 / 188,264 / 9,233 — **identical** |
| `Callsite` byte ranges | 16,431, unchanged (untouched by design) |
| Graph size delta | +479,232 B = **+0.1246%** (criterion ≤5%) |

```bash
python - <<'PY'
import sqlite3
c = sqlite3.connect("file:.../item2_measure/ark_item2.db?mode=ro", uri=True)
print(c.execute("SELECT COUNT(*) FROM nodes "
                "WHERE label IN ('Function','Class','Method') "
                "AND byte_start IS NULL").fetchone())
PY
```

Two design choices a reviewer should weigh:

- **Columns, not `signature` JSON** — the acceptance query is `byte_start is null`,
  and only a column answers it. `nodes` already carried `byte_start`/`byte_end` and
  already versions its schema via an additive `ALTER TABLE` migration.
- **NULL, not 0, when unaddressed** — zero is a legal byte offset, so a zero
  address would make an old graph indistinguishable from a symbol at byte 0, and
  the harness half depends on telling those apart.

Harness behaviour: `content_address.py` re-reads and re-hashes the workspace file
and names exactly one state — `resolved`, `stale_symbol`, `unaddressed`,
`unknown_symbol`, `missing_file`, `outside_workspace`, `address_out_of_range`,
`unreadable_file`. `DELIVERABLE_STATES` is a frozenset **of one**, so only
`resolved` can carry text, and `to_receipt()` stamps `promotes_trust: False`.
Verified end to end against a real graph: `await1K` resolved **byte-identical** to
a direct read of `raw[23:13077]`; appending one line flipped it to `stale_symbol`
with both hashes recorded.

**Stated gaps** (its own, not hidden): index wall time rose 242 s → 405 s but the
runs were **not load-isolated** (a pytest suite ran concurrently), so it is
reported and not attributed; one repository measured; only the task-start
orientation lane is wired, because the other delivery lanes carry no graph node
identity on their envelopes.

### Item 6 engine half — co-change delivery (delta row 6b)

Commits `1ffecb93` (test) → `617b6fee` (feat) → `fd48a642` (fix) → `437346d1`
(test) → `69cd6d9c`, `24df0956` (docs). +883 / −11 across 7 files.

**The schema was read, not guessed** — and that changed the design:
`cochanges(file_a, file_b, count, PRIMARY KEY(file_a,file_b))`.

1. **`count` IS the support.** `persist.go` writes `Pair.Support` there and records
   the four confidence/commit fields as a KNOWN LOSS. The brief asked for `count`
   *and* `support`; the table has one column for both, so the emitter reports the
   one stored quantity under both names rather than inventing a second number.
2. **The window is not stored at all.** The emitter reports `window=unrecorded`
   rather than printing `DefaultMaxCommits=500` as if it were a property of the
   rows in front of it.

The test file copies the schema verbatim instead of importing it, so
producer/reader drift breaks a test rather than surfacing as a runtime
`no such column`.

**A prior is never a resolution** — four independent, tested mechanisms: the module
issues exactly two SQL statements, both against `cochanges` (a `sqlite3`
trace-callback test asserts the executed SQL names no `edges`/`nodes`/`properties`/
`closure`/`assertions`, so it *cannot see* a candidate edge, let alone promote
one); every line ends `status=prior_not_resolution`; it is staged `advisory`; and
it is reached last.

**The `miniswe_runtime.py` edit — the one I said I would scrutinise hardest.** The
entire diff is one line replaced by four, plus a 13-line module-scope helper:

```diff
-    return cap_evidence(result.rendered)
+    rendered = cap_evidence(result.rendered)
+    if rendered:
+        return rendered
+    return _cochange_prior(adapter, command, changed_files)
```

The hook is reachable **only** where the old code returned `cap_evidence("")` — it
can convert *silence* into a prior and nothing else. It cannot displace the
sealed-envelope branch, the `new_file_destination` or `syntax_result` early
returns, or a `covering_verdict`/`recovery` envelope. With 0 rows the emitter
returns `""`, so the runtime is **byte-identical to its pre-item-6 behaviour**, and
a test asserts exactly that. That is an acceptable edit to the live delivery path.

Allow-listing: `cochange_prior` added to `code_build` and `data_transform` **only**
— editing lifecycles where "the file you just touched has a historical companion"
is actionable — and deliberately **not** `repository_content`, whose allowed set
already excludes stronger signals.

Measured on completed runs only. `cochanges` across every local graph: 0 rows in
every depth-1 clone (`ark-new.db`, `ad.db`, `x.db`), **54 rows** in `g-new.db`,
3 in `fx.db`. End-to-end on the 54-row graph: top pair `(main.go, sqlite.go, 14)`,
dose **588 bytes**, **2 complete lines**, third partner dropped by the ceiling.
Before `fd48a642` the same input produced 631 bytes with the third line **cut
mid-provenance** — that measurement is what motivated the fix, because a half-line
is a half-claim.

Tests: 59 targeted passed. Full suite 1,088 passed / 85 skipped / **12 failed** —
all 12 **proven** environment-class by re-running with the four engine files
reverted to base, where they fail identically. Zero regressions.

**The gap that matters, and the conservative call:** `cochange_rows` is not wired
into `gt_harness/runtime_receipts.py`, because that would mean editing the
certification manifest in `gt_engine/indexer.py` — a file other streams are
editing. The consequence is stated plainly: **until that field exists, a delivered
`cochange_prior` does not discharge the graph-evidence obligation.** The
fail-closed direction was chosen over the convenient one, and `cochange_prior` is
dropped from the enforcement set when rows are 0 **or unstated**.

### Item 10 — projections and cheap wins (delta rows 10–13)

`4867eb922` (RED) → `ab0ec4248` (fix) → `8ae5694ef` (report). New logic lives in
four new files (1,224 lines); the diff into files stream A is editing is **75
inserted lines across five files**, hunk-by-hunk disjoint from stream A's.

Re-verified by me: `go test -tags sqlite_fts5 ./internal/resolver/ ./internal/store/`
→ **exit 0**.

Its measurements: `reason` coverage **100%** on HAS_CALLSITE in both fixtures
(3,512/3,512 bandit, 6,747/6,747 arktype-250); narrowing by argument arity on 17
and 9 callsites; **no promotion measured** — `CALLS` 777→777, `SELECTED_TARGET`
208→208 and 727→727, the resolver line bit-identical. Five callsites moved
`ambiguous`→`incomplete`: narrowed to exactly one candidate and published as
`candidate_only`, with no selected target and no tier change.

**Its own limits, which are the most useful part of the report:**

1. **MRO ordering has zero measured effect** on either fixture — neither produces a
   single `inherited`-mechanism callsite. C3, the inconsistent-hierarchy refusal
   and the cycle bound are covered by **unit tests only**. Nobody may quote a
   measured MRO number from this stream; there isn't one.
2. **The full arktype repo is unmeasured** — its publication WAL passed 188 MB and
   was still growing after six minutes. Every arktype figure is a `-max-files 250`
   subset.
3. **Only 2 of 30 grammars** are measured by built graphs.
4. **The plan's "raise resolution rate above 36%" was not met and cannot be as
   specified** — it contradicts the no-promotion rule. The stream implemented the
   rule, measured precision instead (54 and 5 fewer wrong candidate edges), and
   left the one-line reversal unmade because it is an evidence-policy decision.
   That is the right call.

### Wiring — the three landed packages, actually published (delta rows 6, 6a, 7) — **LANDED**

**Landed on `final_hardening/producer` as `95ff5a3c2` (RED) + `24d156530` (fix)**,
pushed. Re-verified by me before landing: `go test -tags sqlite_fts5` over
`cmd/gt-index`, `internal/community`, `internal/process`, `internal/cochange` →
**exit 0**; the RED commit confirmed to contain only the four dedicated files the
`fixture_files_are_dedicated` check admits; receipt validated
(`gt.fixture-red.v1`, `base_sha=43514ced1…`, `exit_code=1`, both blob digests).

Originally authored as `8b8cca3dd` (RED) → `31b661bf7` (fix). All logic in a new
`cmd/gt-index/derived.go` (445 lines); `main.go` gained one contiguous 22-line
block and **lost** Pass 5c plus the 57-line `mineCochanges`.

**This is the item that turns three reviewed packages from dead code into
published rows.** Before it: `communities`, `community_members`, `processes` and
`process_steps` **did not exist at all** — their DDL lives in the packages and
nothing called `EnsureSchema`. `cochanges` was written by `mineCochanges`, a
second, weaker emitter with a hard-coded support floor of 3, no window record, no
shallow-clone detection and a silent `return 0` on failure. And `project_meta`
said nothing, so "found nothing" and "never ran" were the same observation.

Measured on three repositories, layers off vs on:

| | arktype (TS, 458 files) | actionlint (Go, 544 files) |
|---|---|---|
| nodes | 159,548 → **159,548** | 61,833 → **61,833** |
| edges | 188,264 → **188,264** | 74,058 → **74,058** |
| edges CERTIFIED | 4,593 → **4,593** | 2,425 → **2,425** |
| CALLS CERTIFIED | 1,402 → **1,402** | 1,330 → **1,330** |
| resolution_candidates | 10,820 → **10,820** | 5,514 → **5,514** |
| `cochanges` | 0 → **23,720** | 0 → **4,643** |
| `communities` | absent → **81** | absent → **72** |
| `community_members` | absent → **982** | absent → **553** |
| `processes` | absent → **95** | absent → **155** |
| `process_steps` | absent → **276** | absent → **357** |
| Pass 4g wall time | **10.087 s** | **3.6–4.9 s** |

Every core population is **identical** across the two arms. That is the
no-promotion proof, measured rather than asserted — and it is structural too:
`community.Queryer` exposes `QueryContext` only (the package defines no `Exec`),
`process` writes only tables it owns, and the coupling transaction touches only
`cochanges`/`communities`/`community_members`.

Cross-check: arktype's `assertions_scanned=105` matches the figure item 8 reported
independently, and its 23,720 co-change pairs match item 6's `--depth=500`
measurement exactly.

**The named-abstention case, on a real repository.** bandit is a depth-1 shallow
clone; the graph still published, exit 0:

```
derived_layers_state=degraded   derived_layers_degraded=cochange=shallow_clone
derived_cochange_state=shallow_clone  pairs=0  commits_scanned=0  shallow=1
derived_community_state=ok  count=3  certified_call_rows=5  excluded_call_rows=482
derived_community_cohesion=absent:history_unavailable
derived_process_state=ok  count=298  assertions_scanned=507  targets_without_path=48
```

Co-change abstains **and says why**; communities still form from certified CALLS
alone; cohesion renders `absent:history_unavailable` and never `0.0`. 23
`derived_*` keys are written on **every** path including the disabled one, so
"did not run" and "found nothing" stay distinguishable. A layer's state is the
producing package's own `Reason` constant verbatim; the wiring adds only five
states of its own, for failures the packages cannot name. **No failure aborts the
index.**

Gate: `GT_REQUIRE_DERIVED=1` aborts the staged build on any non-`ok` state;
`GT_DERIVED_LAYERS=off` skips and records `disabled_by_operator`. An unrecognised
value of either — or both set contradictorily — is an error, not a silent default.

**Honesty worth noting in its own report:** it refuses to quote a whole-build wall
time, because whole-build time varies ~2× between identical runs on this host and
the two arms disagreed in sign. It reports only the in-process Pass 4g timer,
which brackets exactly the added code.

Its stated gaps: switches are parsed at Pass 4g rather than startup (deliberate,
to keep the `main.go` footprint to one block while five streams edit `main()`);
`GT_DERIVED_LAYERS=off` now means **no** `cochanges` at all, since keeping both
writers was impossible — same primary key, different support floors, and the
weaker emitter ran later so it would have silently won every row; not wired on the
`-file` incremental path, so derived tables go stale after an incremental reindex;
`cochange.Persist` still drops the computed confidence fields because recovering
them needs additive columns in the protected `sqlite.go`; and only three fixtures
were run, with no claim made about the other sixteen.

---

## 3. Rejected

### Item 1 — budgeted abstention (delta row 14) — **REJECTED, reworking**

`0a428051a` (RED) → `4a8aa2f6f` (fix) on `final_hardening/item1-budget`. Not landed.

Rejected on two grounds, both established from the stream's **own** artefacts:

1. **boa still does not publish.** `scratchpad/fh/boa.log` — full 883-file repo at
   the proposed default — ends `exit=124 elapsed=1501s` with a **6.7 GB** WAL. The
   only boa run that ever completed was `-max-files 250`, a quarter of the repo.
2. **An unsupported number was in the source.** `budget.go` justified its defaults
   with "across the 19-repository smoke set… the largest per-callsite flow-fact
   fan-out is 800". That sweep completed **3 of 19** repositories and died with
   `fork: Resource temporarily unavailable`.

**What I then established myself**, which the stream had not stated plainly — the
budget is genuinely **inert on a healthy repository**:

| arktype | nodes | edges | properties | candidates |
|---|---|---|---|---|
| baseline (unbudgeted binary) | 159,548 | 188,264 | 9,233 | 10,820 |
| budget **disabled (0)** | 159,548 | 188,264 | 9,233 | 10,820 |
| budget at **default** | 159,548 | 188,264 | 9,233 | 10,820 |

`+0.0000%` on every count, with an identical abstention distribution (`zero` 6,252
/ `external_unresolved` 1,366 / `parser_incomplete` 675 /
`dynamic_target_not_statically_proven` 10). Budget `0` reproduces the baseline
exactly. **What is unproven is the half that matters**: that it rescues an
unhealthy repository.

**The finding worth more than the budget it shipped beside.** `pass_coverage` is a
*callsite* fact that was being copied verbatim onto **every candidate edge** —
**713 MB of the publication's 845 MB** of candidate-edge JSON on a 250-file boa
sample. Nothing reads it: `queryAttachedCandidates` selects the literal `'[]'` for
that column and `loadResolutionV2Evidence` rebuilds coverage from the
`CompletenessFact` nodes. Pure redundancy removal, no semantic change.

Alongside it: eleven statements in the publication loop prepared once per
transaction instead of re-parsed per callsite/candidate/fact; the pre-insert
`SELECT` in `persistOneVTAFlowFactTx` hoisted into a transaction-scoped cache that
still rejects a conflicting rebinding; SQLite page cache raised from the 2 MB
default to 512 MB.

To its credit, the commit message now states outright: *"boa remains UNRESOLVED…
`defaultFlowFactBudget` is not yet justified by the repository the budget exists
for."* The correction is in the artefact, not in a summary.

---

## 4. The boa investigation — open, and the plan may be wrong about it

`arch_pipeline.md` and the plan both say boa's *"publication never terminates"*.
**That may be an artefact of our own timeout.**

| Run | Config | Result |
|---|---|---|
| `boa.log` | full repo, proposed default budget | `exit=124` @ 1501 s, WAL 6.7 GB |
| `boa_b64.log` | full repo, `GT_FLOW_FACT_BUDGET=64` — **64× tighter** | `exit=124` @ 1501 s, WAL 4.6 GB |
| `boa_ex.log` | `-max-files 250` | **publishes**: 277,289 nodes, 433,437 edges, 66 s |

The budget-64 run used the binary that already carries the prepared statements, the
512 MB page cache, the hoisted SELECT cache and the `pass_coverage` removal — I
checked the store sources were last written 14:51 and the binary built 15:23. So
**the flow-fact fan-out is not the term, and neither are the prepared statements.**

Where it hangs is pinned: the log prints `Resolved 31755/68227 calls in 42 s` and
then **nothing** — no Pass 4a, where the 250-file run prints Pass 4d / 4f / 4e. It
is inside the resolution-graph attach.

**The hypothesis now under test.** boa may simply be *slow*, not non-terminating.
Stream A's own partial sweep shows `abs-module-cache-flags` took **2,627 s — 43
minutes — for 67 files, and exited 0**. Every boa run ever made here was capped at
1500 s. A run at a **7200 s** timeout is in flight (`scratchpad/fh/boa_long.log`),
and stream A is instrumenting `AttachResolutionGraphTx` with per-N-callsite
progress, which distinguishes slow-but-linear from genuinely stuck.

If boa terminates at 45–90 minutes, then this was never a termination defect: it is
a **wall-clock and disk-amplification** problem, the levers are the `pass_coverage`
removal and two-phase publication (item 9), and the budget is a rail rather than a
rescue. Item 15's priority rises accordingly, and both affected streams have been
told to assume so.

---

## 5. Process incidents, recorded

**Cohort-wide stream failure.** All nine parallel streams stopped within minutes of
each other — five hit the account session limit, four were killed by a 600 s stall
watchdog after blocking on long foreground builds. No work was lost; every worktree
retained its diff. All were resumed from their own transcripts with two added
rules: nothing over ~8 minutes in the foreground (use background + poll), and
commit before continuing. Two killed runs had left **14.6 GB** of orphaned SQLite
WAL on `D:`, since removed.

**Unreviewed auto-pushes.** The repo-installed `post-commit` hook (`gnx auto-push`,
via `core.hooksPath`) pushes on every commit. It pushed item 2's harness half (3
commits) and item 6's engine half (6 commits) to their **stream** branches. No
stream used the hook-bypass flag and none disabled the hook — and the `pre-commit`
gate refuses to run unless `core.hooksPath` points at that directory, so under the
standing rules there was no way to commit without the push. Item 10's identical
hook *failed* for lack of credentials, so nothing was pushed there — meaning "do
not push" is currently honoured by an environment accident rather than by
configuration.

**Contained**: every push went to a stream branch. Both landing branches are
untouched — `har81/canonical-task-identity` at `7b8d8183`,
`final_hardening/producer` at `43514ced1` — so no unreviewed code sits on a branch
anything builds from.

**Attribution trailer changed mid-build.** A harness directive replaced the
`Claude Fable 5.1` co-author trailer with `Claude Opus 5 (1M context)` plus a
session line. Streams flagged it rather than silently rewriting history. Commits
before and after the switch carry different trailers; this is cosmetic and no
history was rewritten to hide it.

**Documentation debt.** `arch_pipeline.md` still states that `cochange_partner` has
"no emitter anywhere" (lines ~306-307 and ~397-399). Item 6's engine half makes
that false. The streams correctly did not edit a contended, landing-agent-owned
file; correcting it is mine.

---

## 6. Standing invariants, enforced on every stream

- Candidate evidence is **never** promoted — not by ranking, community membership,
  process membership, retrieval score, overload narrowing or MRO ordering.
- **Fixture-first** on every protected producer path (`sqlite.go`, `main.go`,
  `resolution_v2.go`, `publication*.go`, `internal/resolver/*.go`): a `test(red):`
  commit carrying only tests + `.githooks/tests/<name>.sh` +
  `.githooks/red-artifacts/<name>.out` + a `gt.fixture-red.v1` receipt, *before*
  the fix commit. The pre-push hook re-executes the RED in a clean checkout.
- Git hooks are never bypassed.
- **No GT-off evaluation is run, planned or proposed.** Baselines are frozen and
  fetched.
- **No paid dispatch without explicit typed authorisation.** 0 of 4 attempts spent.
- **A number that no completed run produced does not go into a report, a commit
  message or a source comment.** "Unmeasured" is always accepted; an unsupported
  figure is what got item 1 rejected.
