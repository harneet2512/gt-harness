# final_hardening item 6 — engine half: co-change delivery

Stream H. Worktree `D:/gt-fh-item6-engine`, branch `final_hardening/item6-engine`,
base `7b8d8183`. Prerequisite for delta row 6.

## Commits

| SHA | Kind | Subject |
|---|---|---|
| `1ffecb93` | test | co-change delivery, from the producer's exact cochanges schema |
| `617b6fee` | feat | emit `cochange_partner` and gate `cochange_prior` on real rows |
| `fd48a642` | fix | drop whole co-change lines at the ceiling instead of cutting one |
| `437346d1` | test | pin the runtime seam's two silence guarantees |
| `69cd6d9c` | docs | this report |

## Files at `437346d1`

| SHA-256 | Lines | File |
|---|---|---|
| `62da6a967e7e6a512397745ceb3d261985c05c8de53992893fde2bb0465b9f69` | 268 | `gt_engine/cochange_evidence.py` *(new)* |
| `e96d8bcafa38812b2da017d9426f006dc07bd10a49a073af3ccebae77b159afb` | 89 | `gt_engine/graph_utilisation.py` |
| `08573b6558193174fcdb6652603d35922b67a78c7f57d2bd83ef2dbac22bde4d` | 58 | `gt_engine/role_packs.py` |
| `8807601564b0c81a7037f8b0b7b42af91694fed1eb6228e8ef5d905f960ef7fd` | 1003 | `gt_engine/miniswe_runtime.py` |
| `74b65ef7529743976b694399e3d44f51e55988df0196422077c8dc889ea11688` | 443 | `tests/test_cochange_evidence.py` *(new)* |
| `a7824f9396e4f92df3ac65918ceb5c959f64f87a5eeb0281a449d2667315a50b` | 137 | `tests/test_graph_utilisation.py` |
| `32a71cddceeb950be9b618b4506d1e74cc671674e41324946a4984ad78d2dae3` | 68 | `tests/test_gt_role_packs.py` |

Range `7b8d8183..437346d1`: 7 files, **+883 / -11**.

---

## The schema I read, not guessed

`D:/gt-fh-producer/gt-index/internal/store/sqlite.go` (producer `ce5e0370`):

```sql
CREATE TABLE IF NOT EXISTS cochanges (
    file_a TEXT NOT NULL,
    file_b TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(file_a, file_b)
);
CREATE INDEX IF NOT EXISTS idx_cochanges_a ON cochanges(file_a);
CREATE INDEX IF NOT EXISTS idx_cochanges_b ON cochanges(file_b);
```

Two consequences drive the whole design, and both come from
`internal/cochange/persist.go`, which states them itself:

1. **`count` IS the support.** `Persist` writes `Pair.Support` into `count` and
   records `ConfidenceAToB`, `ConfidenceBToA`, `CommitsA`, `CommitsB` as a
   KNOWN LOSS with nowhere to go. The task asked for `count` and `support` as
   separate emitted fields; the table has one column for both, so the emitter
   reports the one stored quantity under both names rather than inventing a
   second number. If a future additive column splits them, the emitter changes
   in one place.
2. **The window is not stored anywhere.** There is no window column and no
   co-change receipt in the harness yet. The emitter therefore reports
   `window=unrecorded` unless a caller supplies a recorded one
   (`cochange_partners(..., window=...)`, or an adapter attribute
   `cochange_window`). It does not print the producer's `DefaultMaxCommits=500`
   as though it were a measured property of the rows in front of it.

The test file copies the schema verbatim rather than importing it, so a drift
between GT's reader and the producer's writer breaks a test instead of
surfacing as a runtime `no such column`.

---

## 1. The emitter — `gt_engine/cochange_evidence.py` (new, 268 lines)

Mirrors `gt_engine/miniswe_covering.py::run_newfile_precedent`, which is the
only live graph-adjacent evidence emitter in the engine: a bounded, LLM-free,
correct-or-quiet function returning rendered bytes for the seam to dose, and a
staging call with `semantics="advisory"`.

Public surface:

- `cochange_row_count(graph_db) -> int` — rows in `cochanges`, fail-closed to 0
  for a missing graph, a missing table and an unreadable graph alike.
- `cochange_partners(graph_db, file_path, *, limit, window)` — one `SELECT`,
  both key columns searched, self-pairs excluded, ordered by support then
  partner path.
- `render_cochange_prior(partners, *, revision) -> str` — one line per partner.
- `run_cochange_prior(adapter, files) -> str` — repository-relative filtering
  and the per-file / per-dose bounds.
- `cochange_prior_dose(adapter, files) -> str` — tags, fits, stages.

One rendered line, measured against a real graph (wrapped here; it is a single
line in the dose):

```
gt-index/cmd/gt-index/main.go: co-change prior revision=5b41f0a
partner=gt-index/internal/store/sqlite.go count=14 support=14 window=unrecorded
provenance=cochanges(file_a=gt-index/cmd/gt-index/main.go,file_b=gt-index/internal/store/sqlite.go)
status=prior_not_resolution
```

`provenance` names the exact row by its primary key in **stored** order, so the
same row read from either end cites the same key — a reader can go back to it.

Bounds: `MAX_PARTNERS_PER_FILE = 3`, `MAX_FILES_PER_DOSE = 2`,
`COCHANGE_DOSE_BYTE_LIMIT = 600` (the same envelope `new_file_destination`
uses). The ceiling drops **whole lines**; it never byte-truncates one, because
a cut line leaves a row key severed mid-path and reads as a malformed fact
rather than as a dropped one. If not even the first line fits, nothing is
staged and nothing is emitted.

## 2. The allow-list — `gt_engine/role_packs.py`

`cochange_prior` added to **`code_build`** (role `code_behavior`) and
**`data_transform`**, and deliberately **not** to `repository_content`
(role `content_scan`).

Justification, not a sweep:

- Both packs that get it are editing lifecycles — they carry `pre_edit` and
  `post_edit` and behavioural predicate kinds. "The file you just touched has a
  historical companion" is actionable exactly there and nowhere else.
- `repository_content` is a completeness sweep over content ("find and remove
  all X"). A historical companion neither widens nor closes its scope, and that
  pack's allowed set is already deliberately the minimum a scan needs — it
  excludes `caller_contract`, `def_partition`, `newfile_precedent`,
  `signature_delta` and `covering_red` for the same reason. Adding a signal
  weaker than any of those would be indefensible.

`EvidenceRouter.admit` already canonicalises `cochange_partner` to
`cochange_prior` via `feature_for_evidence` and refuses anything outside the
pack, so the allow-list is the whole enforcement mechanism. A test asserts the
router admits it under `code_behavior` and returns
`role_pack_evidence_mismatch` under `content_scan`.

## 3. The enforcement gate — `gt_engine/graph_utilisation.py`

`cochange_prior` was already listed in `GRAPH_BACKED_FEATURES` while being
unemittable, which was harmless only because nothing could deliver it. Now that
it *is* emittable it needs a gate, or it becomes a free discharge of the
graph-evidence obligation that `gt_harness/runtime_receipts.py` enforces
(`treatment_graph_evidence_absent`).

`graph_utilisation(deliveries, *, cochange_rows=None)` now returns two new keys:

- `cochange_rows` — the stated count, or `None`.
- `enforcement_features` — `graph_backed_features` minus `cochange_prior`
  whenever `cochange_rows` is 0 **or unstated**.

`graph_backed_delivery` now keys on `enforcement_features`. The delivery is
still fully reported in `graph_backed_features`; only the obligation-discharging
set is gated.

The gate is **fail-closed** on `None` on purpose. This module exists to detect
a certified-but-unused graph; an unproven row must be treated exactly like an
absent one, and every graph built from a depth-1 clone has an absent one. The
practical exposure is nil: for `graph_backed_delivery` to hinge on co-change
alone, a run must have delivered zero `caller_contract`, `def_partition` and
`signature_delta` — a run that already had almost no graph use.

Existing behaviour is unchanged for every other feature: a `caller_contract`
delivery still discharges the obligation with `cochange_rows=0` (asserted).

## 4. A prior is never a resolution

Four independent mechanisms, each with a test:

1. **It reads no resolution table.** The module issues exactly two SQL
   statements, both against `cochanges`. `test_only_the_cochanges_table_is_read`
   installs a `sqlite3` trace callback and asserts the executed SQL mentions no
   `edges`, `nodes`, `properties`, `closure` or `assertions`. It is structurally
   incapable of promoting a candidate edge because it never sees one.
2. **Every line says so.** Each rendered line ends
   `status=prior_not_resolution`; a test asserts the words `resolved` and
   `verified` never appear.
3. **It is staged `advisory`**, the same semantics as `new_file_destination`,
   never authoritative.
4. **It cannot outrank anything.** In the runtime it is reached only after the
   arbitrated pipeline produced no bytes at all (below).

---

## The `gt_engine/miniswe_runtime.py` change, line by line

This is the live delivery runtime, so here is the entire diff — 1 line replaced
by 4, plus a 13-line helper. Nothing else in the file is touched.

```diff
-    return cap_evidence(result.rendered)
+    rendered = cap_evidence(result.rendered)
+    if rendered:
+        return rendered
+    # Last, and only into silence. A co-change prior is the weakest signal GT
+    # delivers and must never displace a covering RED, a syntax error or any
+    # arbitrated envelope; it speaks only when nothing stronger did.
+    return _cochange_prior(adapter, command, changed_files)
+
+
+def _cochange_prior(
+    adapter: MiniSweAdapter, command: str, changed_files: tuple[str, ...]
+) -> str:
+    """Advisory co-change dose for the files this action edited or viewed."""
+    from .cochange_evidence import cochange_prior_dose
+
+    files = tuple(dict.fromkeys((*changed_files, *_viewed_files(command))))
+    if not files:
+        return ""
+    try:
+        return cochange_prior_dose(adapter, files)
+    except Exception:  # noqa: BLE001 - a prior is correct-or-quiet, never fatal
+        return ""
```

Line by line:

- `rendered = cap_evidence(result.rendered)` and `if rendered: return rendered`
  are the old expression, split so its value can be tested. When the pipeline
  produced bytes, the returned value is **identical** to before.
- `return _cochange_prior(...)` is reached only where the old code returned
  `cap_evidence("")`, i.e. `""`. The hook can therefore only ever convert
  *silence* into a prior. It cannot displace the sealed-envelope branch above it
  (which returns earlier), nor the `new_file_destination` and `syntax_result`
  early returns, nor a `covering_verdict` / `recovery` envelope — all of those
  arrive through `run_evidence_pipeline` and produce bytes.
- The helper sits at module scope rather than inline so it is directly testable
  without constructing a Mini-SWE agent — four of the tests call it.
- `from .cochange_evidence import cochange_prior_dose` is a **function-local**
  import, matching every other lane import in this file
  (`run_newfile_precedent`, `run_syntax_probe`, `run_covering_lane`). Module
  import time and the import graph are unchanged when the lane never fires.
- `files = tuple(dict.fromkeys((*changed_files, *_viewed_files(command))))` is
  exactly the two boundaries `attribution.DIRECT_FEATURES["cochange_prior"]`
  already declares for this feature — `edit_result` and `file_view` —
  deduplicated with edits first. `_viewed_files` is the file's own existing
  helper; no new classification logic is introduced.
- `if not files: return ""` — an action that touched no file emits nothing.
- `try/except` returning `""` is correct-or-quiet, the same posture as
  `run_syntax_probe` and the recovery-fingerprint block above it. A prior may
  not be the thing that kills a turn. `_viewed_files` imports
  `groundtruth.runtime.gateway` lazily and `getattr(adapter, "graph_db")` can
  raise on a degraded adapter; both are covered, and a test drives the guard
  with an adapter that raises.

**No-op proof for the shipping state.** Every fixture graph has `cochanges` = 0
rows (measured below). With zero rows the emitter returns `""`, so
`_run_evidence` returns `""` — byte-identical to the pre-item-6 runtime.
`test_the_seam_hook_changes_nothing_on_a_graph_with_no_rows` asserts exactly
this. The only behavioural delta is: previously-empty **and** the graph has
co-change rows for a touched file → an advisory dose.

---

## Measurements

All figures below were produced by runs that completed in this worktree.

**`cochanges` population across every `graph.db` reachable locally**
(read-only, `mode=ro&immutable=1`; `reader` is `cochange_row_count` on the same
file):

| graph | `cochanges` table | rows | nodes | `cochange_row_count` |
|---|---|---|---|---|
| `ad.db` | present | 0 | 110,677 | 0 |
| `ad3.db` | present | 0 | 110,677 | 0 |
| `ark-community-check.db` | present | 0 | 159,548 | 0 |
| `ark-new.db` | present | 0 | 159,548 | 0 |
| `x.db` | present | 0 | 110,677 | 0 |
| `fx.db` | present | **3** | 35 | 3 |
| `g-old.db` | present | **54** | 75,090 | 54 |
| `g-new.db` | present | **54** | 75,090 | 54 |

This confirms the premise from both sides: the depth-1 clones are 0-row, and
the emitter reads the real number where one exists.

**End-to-end emit against `g-new.db` (54 rows, real producer history).** Top
rows by support:

```
('gt-index/cmd/gt-index/main.go', 'gt-index/internal/store/sqlite.go', 14)
('gt-index/cmd/gt-index/main.go', 'gt-index/internal/store/resolution_v2.go', 12)
('gt-index/internal/store/resolution_v2.go', 'gt-index/internal/store/sqlite.go', 12)
```

Dose for `gt-index/cmd/gt-index/main.go`: **588 bytes** total, body **557
bytes**, **2 complete lines** (the third of three partners dropped by the
600-byte ceiling), every line ending `status=prior_not_resolution`, staged as
`{'kind': 'cochange_partner', 'semantics': 'advisory', 'target':
'gt-index/cmd/gt-index/main.go'}`.

Before the `fd48a642` fix the same input produced 631 bytes with the third line
cut mid-provenance; that measurement is what motivated the fix.

**`graph_utilisation` gate, executed:**

```
graph_utilisation([{'evidence_type': 'cochange_partner'}])
  -> enforcement_features=[], cochange_rows=None, graph_backed_delivery=False
graph_utilisation([{'evidence_type': 'cochange_partner'}], cochange_rows=54)
  -> enforcement_features=['cochange_prior'], cochange_rows=54,
     graph_backed_delivery=True
```

## Tests

Tests were written and committed before the implementation: `1ffecb93`
(`test:`) precedes `617b6fee` (`feat:`).

| File | Tests | Result |
|---|---|---|
| `tests/test_cochange_evidence.py` *(new)* | 30 | pass |
| `tests/test_graph_utilisation.py` (+7 new) | 14 | pass |
| `tests/test_graph_evidence_enforcement.py` (unchanged) | 9 | pass |
| `tests/test_gt_role_packs.py` (+3 new) | 6 | pass |
| **targeted total** | **59** | **59 passed, 0 failed** |

**Full suite** — `python -m pytest -q tests/` at `437346d1`, one completed run:

```
1185 collected = 1088 passed, 85 skipped, 12 failed   (EXIT=1)
```

All 12 failures are pre-existing and environment-class. Proof, not assertion:
each was re-run with this stream's four engine files reverted to base
`7b8d8183` (`gt_engine/cochange_evidence.py` removed,
`graph_utilisation.py` / `role_packs.py` / `miniswe_runtime.py` checked out at
base) and **all 12 fail identically there**. Zero regressions from this change.

| Class | Diagnostic | Count | Tests |
|---|---|---|---|
| Index resource guard (cgroup) | `GT_INDEX_RESOURCE_GUARD_UNAVAILABLE` / `resource_guard_unavailable` | 5 | `test_gt_repository_intelligence.py::test_frozen_questions_execute_through_production_graph_path_and_replay`, `::test_frozen_question_proof_abstains_on_source_mutation`, `::test_persisted_question_mutation_is_rejected`, `::test_persisted_prompt_mutation_is_rejected_even_with_recomputed_digest`, `::test_persisted_unverified_archive_head_is_rejected` |
| `gt-index` binary unavailable (dormant graph never wakes; the same condition that `SKIPPED … gt-index binary unavailable` covers elsewhere) | `graph_db is None` after a source edit | 2 | `test_gt_engine.py::test_l6_wake_from_dormant_on_source_edit`, `::test_l6_wake_rebuilds_task_projection_and_router` |
| No git identity in the isolated test `HOME` | `fatal: unable to auto-detect email address (got 'Lenovo@LAPTOP-DD2C4250.(none)')` | 5 | `test_miniswe_runtime.py::test_git_based_edit_detection_catches_heredoc_write`, `::test_failing_test_attributed_to_edited_surface`, `::test_syntax_probe_catches_broken_edit`, `::test_newfile_precedent_delivered_on_file_create`, `::test_advisory_mode_never_runs_hidden_covering_or_syntax_commands` |

The `test_miniswe_runtime.py` group is the one worth naming explicitly, because
it is the file whose *runtime* this change edits: those five tests fail at base
for the same git-identity reason and are not reachable in this environment, so
the seam change is covered by the four new tests in
`tests/test_cochange_evidence.py` that call `_cochange_prior` directly rather
than by that file. The pre-push construction gate (`core.hooksPath`) passed on
all four commits.

Reproduce the base comparison:

```bash
cd D:/gt-fh-item6-engine
git checkout 7b8d8183 -- gt_engine/graph_utilisation.py gt_engine/role_packs.py gt_engine/miniswe_runtime.py
mv gt_engine/cochange_evidence.py /tmp/hold.py
python -m pytest -q tests/test_miniswe_runtime.py tests/test_gt_repository_intelligence.py
mv /tmp/hold.py gt_engine/cochange_evidence.py
git checkout HEAD -- gt_engine/graph_utilisation.py gt_engine/role_packs.py gt_engine/miniswe_runtime.py
```

`ruff check` is clean on all four changed engine files and all three test files.

## Verify

```bash
cd D:/gt-fh-item6-engine
python -m pytest -q tests/test_cochange_evidence.py
python -m pytest -q tests/test_graph_utilisation.py tests/test_graph_evidence_enforcement.py
python -m pytest -q tests/test_gt_role_packs.py
python -m pytest -q tests/
python -m ruff check gt_engine/cochange_evidence.py gt_engine/graph_utilisation.py gt_engine/role_packs.py gt_engine/miniswe_runtime.py
```

## Gaps and things I did not do

1. **The window stays `unrecorded` in production.** The producer computes a
   bounded window (`DefaultMaxCommits = 500`) and a `Result.Reason`, but
   `persist.go` drops both — the table has no column for them and no co-change
   receipt is published alongside the graph. The emitter has the seam
   (`cochange_partners(..., window=...)`, adapter attribute `cochange_window`)
   and will report a real window the moment one is published. Filling it needs a
   producer-side receipt, which is item 6's producer half, not this stream.
2. **`cochange_rows` is not wired into `gt_harness/runtime_receipts.py`.** That
   module calls `graph_utilisation(deliveries)` with no count and works from a
   report dict, not a live graph handle; the row count is not in
   `gt.graph_certification.v1`. Supplying it means adding a `cochange_row_count`
   field to the certification manifest in `gt_engine/indexer.py` — a shared file
   other final_hardening streams are editing, and an additive schema change that
   belongs with the producer wiring. I chose the fail-closed default over a
   cross-stream edit. Consequence, stated plainly: until that field exists, a
   delivered `cochange_prior` does **not** discharge the graph-evidence
   obligation. That is the conservative direction, not the convenient one.
3. **`gt_engine/utility.py` has no `_SEVERITY` entry for `cochange_partner`.**
   The dose bypasses the gateway arbiter (as `new_file_destination` does), so
   the entry would be dead code today. The default severity is 0.5, already
   below `def_partition` (0.55) and `new_file_destination` (0.60), so if it ever
   does reach the arbiter it ranks last among facts — correct for a prior.
4. **`arch_pipeline.md` is not updated.** Lines 306-307 and 397-399 still
   describe `cochange_partner` as having "no emitter anywhere in the codebase".
   That file is edited by sibling streams and owned by the landing agent;
   editing it here would guarantee a conflict.
5. **No checked-in fixture graph gained co-change rows.** Each test builds its
   own DB from the verbatim schema, which is stronger — a binary fixture would
   hide a schema drift rather than break on it.
6. **Acceptance on a `--depth=500` fixture was not run by this stream.** The
   plan's acceptance (`sqlite3 ark.db "select count(*) from cochanges"` > 0) is
   a producer-side build. The nearest completed evidence I have is the 54-row
   `g-new.db` above, which the emitter handled end to end.

## Operational notes

- **This branch is pushed, and I did not push it.** The repository's shared
  `post-commit` hook (`core.hooksPath = D:/gt-harness/.githooks`) auto-pushes
  every commit. Suppressing it would need either `--no-verify` or a change to
  `core.hooksPath`, and the `pre-commit` gate refuses to run unless
  `core.hooksPath` is exactly that shared directory — so under the rules as
  given there is no way to commit without the push. Five commits reached
  `origin/final_hardening/item6-engine`. No merge, no PR.
- **Attribution trailer.** The stream brief asked for
  `Co-Authored-By: Claude Fable 5.1`. A harness directive issued mid-session
  replaced it with `Co-Authored-By: Claude Opus 5 (1M context)` plus a
  `Claude-Session:` line, stating explicitly that it replaces earlier
  attribution guidance. All commits carry the harness trailer. Flagged
  rather than changed silently.
- No GT-off run, plan or proposal. No paid dispatch. No `--no-verify`.
- Nothing outside `D:/gt-fh-item6-engine` was modified;
  `D:/gt-fh-producer/gt-index/` was read only.
