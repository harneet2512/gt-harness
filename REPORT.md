# final_hardening item 2 - content addressing (harness half)

Stream J. Worktree `D:/gt-fh-item2-engine`, branch `final_hardening/item2-engine`,
base `7b8d81831c9972e1de7a9e61b33c52ea72980a0e`.

**Push notice: I ran no push command. The repo-installed `post-commit` hook at
`D:/gt-harness/.githooks/post-commit` (`gnx auto-push`) pushed both commits to
`origin/final_hardening/item2-engine` automatically on commit.** The producer
worktree's identical hook failed and logged a blocker; this one succeeded. I did
not use `--no-verify` and did not disable the hook.

## Commits

| SHA | Subject |
|---|---|
| `09586be9` | `test(red): pin delivery-time verification of a symbol's content address` |
| `0b159773` | `feat(final_hardening): verify a symbol's content address at delivery` |

RED commit (tests + red artifact only, mirroring the `tests/red_artifacts/`
convention already in this repo):

- `tests/test_content_address.py`
- `tests/red_artifacts/run_content_address_red.py`
- `tests/red_artifacts/content_address.out` - `RED: gt_engine.content_address is absent`
- `tests/red_artifacts/content_address.receipt` - `gt.fixture-red.v1`,
  `base_sha=7b8d81831c9972e1de7a9e61b33c52ea72980a0e`, `exit_code=1`,
  `command=python tests/red_artifacts/run_content_address_red.py`

Fix commit:

- `gt_engine/content_address.py` (new, 297 lines)
- `gt_engine/bridge.py` (delivery boundary; +66 / -5)

## How the delivery path was found

`gt_engine/contract.py` and `gt_engine/retrieval.py` were the stated starting
points. Neither reaches the bridge: no module under `gt_engine/` imports
`gt_engine.retrieval`, and `contract.py` is a pure graph-to-dict projection with
no caller in `bridge.py`. The path graph symbols actually reach the model
through is:

```
GTBridge.task_start()
  -> graph_context.build_graph_projection(graph_db, contract)
  -> GTBridge._rerank_graph_evidence(boundary)
       -> graph_evidence.build_evidence_need / rank_graph_evidence
  -> GTBridge._render_task_start_orientation()      <-- the delivery seam
```

`_render_task_start_orientation` is where a symbol's `file_path`/`symbol` becomes
model-visible text, so that is where the address is resolved. `repo_root` is
already a `GTBridge` field and already used for on-disk reads
(`check_edit_syntax`, `capture_bash_preimage`), so no new plumbing was needed.

## What was built

`gt_engine/content_address.py` re-reads the workspace file, re-hashes it, and
names exactly one outcome:

| state | meaning | delivers bytes |
|---|---|---|
| `resolved` | hashes agree; the byte range IS the snippet | yes |
| `stale_symbol` | hashes disagree; both hashes reported | no |
| `unaddressed` | graph holds the symbol but no address | no |
| `unknown_symbol` | graph does not hold the symbol | no |
| `missing_file` | the workspace file is gone | no |
| `outside_workspace` | the stored path escapes `repo_root` | no |
| `address_out_of_range` | range past EOF of a hash-matching file | no |
| `unreadable_file` | present, unreadable | no |

`DELIVERABLE_STATES` is a frozenset of one, so `resolved` is the only state whose
`text` can be shown. `ResolvedSymbol.to_receipt()` drops the bytes and stamps
`promotes_trust: False` - a matching hash confirms the bytes the claim was
already built on and is not new evidence.

Two decisions worth naming:

- **An old graph is queried, not errored on.** When `nodes` has no `file_hash`
  column the module switches to a SELECT that supplies empty address columns, so
  a symbol the graph holds reads back as `unaddressed`. An exception is not a
  verdict, and `unaddressed` must not look like `unknown_symbol`.
- **The stored path is confined.** `_confined_abs` mirrors
  `GTBridge._confined_abs` byte for byte in intent: producer data must not be
  able to name a file outside the workspace, and the refusal gets its own state
  rather than being folded into "missing".

**Delivery wiring.** In `_render_task_start_orientation`, every rendered symbol
is resolved (one shared `hash_cache` per render, so a file is hashed once). A
stale symbol is DOWNGRADED in place: its claim text is replaced by the named
marker carrying both short hashes, its action becomes `re-read the file; this
claim predates it`, and a `graph.stale_symbol` trace record is written. Every
other symbol gets ` | address=<state>` appended, so an unverified line says so
on its face. A fault inside the check annotates nothing and delivers nothing -
`_resolve_symbol_address` returns `None` and traces
`graph.address_resolution_failed`.

Example downgraded line (from the real arktype graph):

```
[stale_symbol] ark/attest/bench/await1k.ts:await1K stored=5e14807de6d7 actual=266223e3869c -- the file changed after indexing; re-read it
```

## Tests

`tests/test_content_address.py` - **18 passed**, run as
`python -m pytest -q tests/test_content_address.py`.

The graph fixture mirrors `tests/test_symbol_contract.py`: a real sqlite file
under `tmp_path` built from literal rows (the resolver opens graphs read-only
through a `file:...?mode=ro` URI, which needs a real path), plus a small literal
source file written into a `tmp_path` workspace.

The three required cases, plus the states that would otherwise be conflated:

| case | test |
|---|---|
| matching hash -> exact bytes | `test_a_matching_hash_resolves_the_exact_declaration_bytes` |
| changed file -> `stale_symbol` with both hashes | `test_a_changed_file_is_stale_and_names_both_hashes`, `test_the_stale_marker_carries_both_hashes` |
| no addressing (old graph) -> `unaddressed`, no crash | `test_a_graph_without_address_columns_reads_as_unaddressed`, `test_a_symbol_with_null_address_columns_reads_as_unaddressed` |
| stale delivers nothing | `test_a_stale_symbol_delivers_no_bytes` |
| whitespace-only edit is still stale | `test_a_whitespace_only_edit_is_still_stale` |
| receipt has no bytes, promotes nothing | `test_a_receipt_carries_no_bytes_and_promotes_nothing` |
| deleted file / path escape / range past EOF / unknown symbol | four separate named-state tests |
| delivery annotates a verified symbol | `test_delivery_annotates_a_verified_symbol_with_its_state` |
| delivery downgrades a stale symbol instead of shipping its claim | `test_delivery_downgrades_a_stale_symbol_instead_of_shipping_its_claim` |
| delivery survives an old graph | `test_delivery_survives_an_old_graph_without_addresses` |

### Full suite

`python -m pytest -q tests/` -> **1,067 passed, 85 skipped, 12 failed**
(1,164 collected; `PYTEST_RC=1`).

All 12 failures are environment-class and were re-run at the same base with this
change stashed out: **the same 12 fail identically without the change.**

| count | tests | cause |
|---|---|---|
| 5 | `tests/test_gt_repository_intelligence.py::test_frozen_questions_execute_through_production_graph_path_and_replay`, `::test_frozen_question_proof_abstains_on_source_mutation`, `::test_persisted_question_mutation_is_rejected`, `::test_persisted_prompt_mutation_is_rejected_even_with_recomputed_digest`, `::test_persisted_unverified_archive_head_is_rejected` | `IndexBuildStatus.BUILD_FAILED`, `error_type=GT_INDEX_RESOURCE_GUARD_UNAVAILABLE`, `error_diagnostic=resource_guard_unavailable` - the cgroup resource guard |
| 2 | `tests/test_gt_engine.py::test_l6_wake_from_dormant_on_source_edit`, `::test_l6_wake_rebuilds_task_projection_and_router` | same guard: the in-test index never builds, so `GTBridge.graph_db` stays `None` (`assert None is not None`) |
| 5 | `tests/test_miniswe_runtime.py::test_git_based_edit_detection_catches_heredoc_write`, `::test_failing_test_attributed_to_edited_surface`, `::test_syntax_probe_catches_broken_edit`, `::test_newfile_precedent_delivered_on_file_create`, `::test_advisory_mode_never_runs_hidden_covering_or_syntax_commands` | sandbox git: `fatal: unable to auto-detect email address (got 'Lenovo@LAPTOP-DD2C4250.(none)')` |

Baseline re-run command (change stashed):

```
git stash push -- gt_engine/bridge.py
python -m pytest -q <the 12 node ids>     # BASELINE_RC=1, same 12 FAILED
git stash pop
```

85 skips are the usual `gt-index binary unavailable`, `set GT_GITNEXUS_ROOT`,
POSIX-only and symlink-privilege skips.

### Real-graph verification

Against the item-2 arktype graph built by the producer half
(`ark_item2.db`, 458 files, 3,511/3,511 code symbols addressed) and the arktype
checkout:

- `await1K` in `ark/attest/bench/await1k.ts` resolved to `resolved`, and the
  returned text was **byte-identical to a direct read of `raw[23:13077]`**;
- appending one line to a copy of that file flipped the same symbol to
  `stale_symbol`, `text == ""`, with `stored=5e14807de6d7 actual=266223e3869c`.

## Gaps and honest limits

- **Only one delivery lane is wired.** `_render_task_start_orientation` is the
  task-start orientation block. The other delivery lanes in `bridge.py`
  (`_deliver`, `_deliver_covering`, `_deliver_post_edit_syntax`,
  `_render_context_checkpoint`) go through `gateway.augment` envelopes and do not
  currently carry a symbol identity that maps to a graph node, so nothing there
  delivers symbol bytes to verify. Extending them needs the envelope to carry a
  node id; that is not done here.
- **Symbols are looked up by `(file_path, name)`**, because `GraphEvidence` drops
  the `node_id` that `GraphSemanticFact` carries. Overloads share a file hash so
  the staleness verdict is unaffected, but the returned snippet is the lowest
  `nodes.id` match. Carrying `node_id` onto `GraphEvidence` would remove the
  ambiguity and was left out to keep the diff off that dataclass and its receipt.
- **`graph_context.graph_revision()` still stats the db** (path + size + mtime_ns)
  rather than hashing it. That is a different freshness token and was not changed.
- **No end-to-end agent run.** The delivery seam is verified by unit tests and by
  one real-graph resolution, not by an agent episode. No GT-off evaluation and no
  paid dispatch was run, planned, or proposed.
