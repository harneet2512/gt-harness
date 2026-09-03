# final_hardening item 5 -- contract embeddings and fingerprint invalidation

**Stream G.** Worktree `D:/gt-fh-item5-embeddings`, branch
`final_hardening/item5-embeddings`, base `7b8d8183`. Closes delta rows 3 and 5.

---

## 1. What was built

### The idea, stated once

Embedding raw source makes a vector a function of the bytes: a formatter run
changes every vector in the repository, and a rename changes the one vector that
should have been most stable. Item 3 already projects the stored `properties`
rows into a deterministic behavioural contract. This item renders that contract
as text and embeds *that*, keyed by the producer-minted `stable_id`, and
re-embeds a symbol only when its behaviour moved.

The invalidation key is **two-part** and the reason matters. The producer's
`fingerprint` property (`complexity:N|calls:...`, 1,407 rows on arktype) is a
function of branches and calls, not of bytes, so a reformat cannot move it --
that is the half the plan names. But a changed **return shape** moves no branch
and no call, so the fingerprint alone does not see it, and a store keyed on the
fingerprint alone would go on serving a vector describing behaviour the symbol
no longer has. The plan's own acceptance criterion ("changing a guard **or a
return shape** changes exactly the affected vectors") therefore cannot be met by
the fingerprint alone. The key is `sha256(KEY_SCHEMA | fingerprint |
sha256(text))`, and the receipt reports **which half fired** for every re-embed,
so "the producer saw the change" and "only the projection saw it" never collapse
into one number. Both halves are line-free, so a reformat moves neither.

### Files

| File | Lines | Blob SHA at HEAD | What |
|---|---|---|---|
| `gt_engine/contract_text.py` | 132 | `638c19ea` | **new** -- line-free rendering of a contract, text digest, the two-part key |
| `gt_engine/contract_embeddings.py` | 721 | `d8f4c3c0` | **new** -- fingerprint read, embedding inputs, invalidation plan, the store, the retrieval-side lookup |
| `tests/test_contract_embeddings.py` | 730 | `a0f4a613` | **new** -- 26 tests |
| `scripts/measure_contract_embeddings.py` | 328 | `e0cc5556` | **new** -- the measurement harness below |
| `gt_engine/contract.py` | 575 (+54) | -- | additive `symbol_node_ids()` and `contracts_with_node_ids()` |
| `gt_engine/dense_runtime.py` | 183 (+32) | -- | additive `embed_texts()` and `model_identity()` |
| `gt_engine/retrieval.py` | 1114 (+117) | -- | `dense_rank`/`hybrid_rank` take `store_path`; `_rank_from_store` |

### Commits (oldest first; none pushed by me -- see section 6)

```
c095a48e  test: contract embeddings bound to the semantic fingerprint        (RED)
adff1a41  feat(wip): contract embedding store keyed by stable id, ...        (WIP checkpoint)
d7d76eb1  feat: contract embeddings, invalidated by the producer fingerprint (GREEN)
87225907  refactor: split contract rendering out of the embedding store
67f5b7c1  chore: keep retrieval __all__ sorted
```

### Storage -- no second vector stack

Vectors go into `gt_engine.hybrid_retrieval.SQLiteVectorIndex`, the store
`dense_runtime` already uses, in its own `gt_vector_documents` table. One
**additive** table, `gt_contract_embedding_bindings`, sits beside it in the same
file and holds what the vector table has no column for: node id, `file_path`,
`start_line`/`end_line`, the fingerprint, both key halves, the contract digest,
the contract schema, the model id, the dimension, and the source revision the
embedding was taken at. So a hit is replayable and receipt-attributable --
`stable_id` plus a line range -- which is the plan's first acceptance criterion.

One deliberate deviation, called out because it looks wrong at a glance: that
index is normally bound to a single `graph_revision` and refuses to serve a
different one. This store pins both revision fields to constants
(`CONTRACT_SOURCE_REVISION = "gt.symbol_contract.v1"`,
`UNBOUND_GRAPH_REVISION`). A cache whose entire purpose is to survive a rebuild
cannot be invalidated by the rebuild. Changing the contract projection *does*
still invalidate everything, because the source-revision constant is the
projection's schema.

### Two identity spaces, joined and never conflated

`nodes.stable_id` is NULL for every code symbol. `gt_engine.contract` keys on
the producer-minted id in `resolution_symbols` (3,509 of 3,511 on arktype; 2
fall back to a line-free derived `gtsym1:` id). `gt_engine.retrieval` mints its
own id via `stable_symbol_id`, which is **line-bearing** and therefore not
durable across a reformat. The store is keyed in the durable space; the ranking
is returned in retrieval's space so RRF still fuses three sources in one space.
The two meet on `nodes.id` of the graph in hand, through the new
`contract.symbol_node_ids()`. A test asserts exactly this rather than papering
over it.

### Retrieval wiring

`dense_rank(..., store_path=...)` (or the `GT_CONTRACT_EMBEDDING_INDEX`
environment variable) ranks the pool against stored vectors, embedding **only
the query** -- one forward pass per query instead of one per candidate.
`hybrid_rank` passes `store_path` through.

A populated store is **not** a licence to answer without the query encoder. The
model-asset preconditions run first and unchanged, so `dense_model_dir_unset`,
`dense_model_assets_absent` and `dense_runtime_failed:*` still fire with a
populated store. New named reasons, never a silent empty: `store_reason` is one
of `contract_embedding_store_absent`, `contract_embedding_store_empty`,
`contract_embedding_store_misses_pool`, `contract_embedding_store_pool_empty`,
`contract_embedding_store_unreadable:<Error>` -- and on the healthy path
`detail["vector_source"] == "contract_embedding_store"` with `store_hits`,
`store_misses` and `missing_stable_ids`. `dense_store_dimension_mismatch` is a
`SourceRanking` reason, not an exception.

### Promotion invariant

Retrieval ranks; it does not promote. The refresh opens the graph **read-only**
by URI, writes only to its own sidecar, and two tests assert the graph's sha256
is byte-identical after a refresh and after a `hybrid_rank`. The refresh receipt
and `HybridRanking.attribution_record()` both carry `promotes_trust: False`. No
tier, no edge, no `cochanges`, no `assertions` row is touched.

---

## 2. What was measured

Graph:
`D:/tmp/claude/D--gt-harness/d4578d92-0fad-4131-b9ed-3ade34ece4fc/scratchpad/ark-new.db`
(arktype, producer `cffca1fd2`, source revision
`04355e8b26d1ad5264ef62314a2bc46c4de58ed8`), **copied** into `.measure/scratch/`
-- the read-only original was never written to. Every number below is from one
completed run of `scripts/measure_contract_embeddings.py` against the code at
HEAD.

### The ONNX model is not present on this machine -- read this before any number

`gt_engine.dense_runtime` pins arctic-embed-m by digest (`model.onnx` sha256
`564e6c65...`, tokenizer `91f1def9...`, 768-d). `GT_DENSE_MODEL_DIR` is unset
here and no directory on this machine passes `_verified_assets`; the local ONNX
files that do exist (`D:/gt_runs/300_e51cc3f0/art_clean/gt-e5-model/model.onnx`
and several `e5-small-v2` copies) are a **different, 384-d model with no
manifest** and are correctly rejected. The measured asset check is
`ValueError: dense_manifest_invalid`, `onnx_asset_verified: false`.
**I did not download the ~428 MB asset.**

Everything below therefore measures the **pipeline** -- rendering, keying,
planning, storage, lookup, cosine -- with a deterministic 768-d stub in place of
the forward pass, plus the **named-degraded retrieval path** measured for real.
**No number here is a claim about embedding quality, recall, or arctic-embed-m
throughput, and none should be read as one.**

### Corpus

| Quantity | Measured |
|---|---|
| Code symbols projected | **3,511** |
| Unique `stable_id` | **3,511** (0 collisions) |
| Producer-minted ids | **3,509**; derived `gtsym1:` fallback **2** |
| Symbols with a `fingerprint` property | **1,407** (40.1%) |
| Contract text, median chars | **78** |
| Contract text, max chars | **1,696** |
| Contract text, total chars | **496,113** |
| Render 3,511 contracts to text | **4.92 s** |

### Cold build

| Quantity | Measured |
|---|---|
| Symbols embedded | **3,511 / 3,511** |
| Vector dimension | **768** |
| Wall time (render + stub embed + publish + bindings) | **26.01 s** |
| Documents in store afterwards | **3,511** |
| Store file size | **61,923,328 bytes (59.1 MiB)** |
| Store sha256 | `99385b5bb62d796570033b945f198d8e421dc43b639dab8c24c579e51f8ee278` |

### Invalidation -- the acceptance criteria

Each row is a second `refresh()` of the same store against a *mutated copy* of
the graph, counting forward passes that actually reached the embedder.

| Change applied to the graph copy | Symbols changed | **Forward passes** | Embedded | To fingerprint | To contract text | Unchanged | Time |
|---|---|---|---|---|---|---|---|
| **Reformat only** -- every `nodes.start_line`/`end_line` and every `properties.line` shifted +7; not one stored value altered | 0 | **0** | 0 | 0 | 0 | **3,511** | 6.46 s |
| **Fingerprint changed** on 25 symbols | 25 | **25** | 25 | **25** | 0 | 3,486 | 7.89 s |
| **Return shape changed** on 12 symbols, fingerprints untouched | 12 | **12** | 12 | 0 | **12** | -- | 6.31 s |

- Reformat -> **0 re-embeds, 0 deletions, 3,511 unchanged.** Criterion met.
- Semantic change -> **exactly the changed symbols**, and the receipt attributes
  all 25 to the fingerprint half.
- Return-shape change -> **exactly the changed symbols**, and the receipt
  attributes all 12 to the contract-text half, with the fingerprint half
  reporting 0. This is the case the fingerprint alone cannot see; without the
  second half these 12 vectors would have gone stale silently.

A note on that last row, because an earlier run of the script reported "12
changed / 10 embedded" and it was **not** a defect: `SELECT node_id ... kind =
'return_shape' LIMIT 12` returns *rows*, and two of those node ids appear twice
(85 and 91 each carry two return-shape rows), so 12 rows covered 10 distinct
symbols. The script now selects `DISTINCT node_id` and the numbers agree. The
earlier figure is recorded here rather than quietly dropped.

### Retrieval-side cost and the degraded path

| Probe | Measured |
|---|---|
| `dense_rank` with `GT_DENSE_MODEL_DIR` unset, store populated | `available=False`, reason **`dense_model_dir_unset`**, 0 results, 0.00 s |
| `dense_rank` with an empty model dir, store populated | `available=False`, reason **`dense_model_assets_absent`**, 0 results |
| `dense_rank` with a fake-manifest model dir, store populated | `available=False`, reason **`dense_runtime_failed:ValueError:dense_manifest_invalid`**, 5.73 s |
| `lookup_vectors` over a 256-symbol pool | 256 hits / 0 misses, dim 768, **0.332 s**; cosine over 256 x 768-d: **0.109 s** |
| `lookup_vectors` over all 3,511 symbols | 3,511 hits / 0 misses, **4.854 s** |

The three degraded rows are the point: with 3,511 vectors sitting in the store,
a missing or unverifiable model still yields a **named reason and an empty
ranking**, never a cached order dressed up as a live one.

The last two rows measure the retrieval-side cost **with the forward pass
removed**, deliberately not through `dense_rank` (which cannot complete without
the query encoder here) so the number cannot be mistaken for an end-to-end one.

---

## 3. Test results

```
tests/test_contract_embeddings.py   26 passed   (new)
tests/test_symbol_contract.py       17 passed   (item 3, unchanged)
tests/test_hybrid_retrieval.py      33 passed   (item 4, unchanged)
tests/test_dense_runtime.py          1 passed
                                    -- 77 passed, 0 failed
```

Full suite: FULL_SUITE_PLACEHOLDER

`python -m ruff check gt_engine/ tests/test_contract_embeddings.py scripts/` --
clean.

---

## 4. Exact commands to verify

```bash
cd D:/gt-fh-item5-embeddings

# the item's own gate, plus the two suites it must not disturb
python -m pytest -q tests/test_symbol_contract.py tests/test_hybrid_retrieval.py \
                    tests/test_contract_embeddings.py tests/test_dense_runtime.py

# the whole suite
python -m pytest -q tests/

# reproduce every number in section 2 (writes only to .measure/, gitignored)
python scripts/measure_contract_embeddings.py \
  "D:/tmp/claude/D--gt-harness/d4578d92-0fad-4131-b9ed-3ade34ece4fc/scratchpad/ark-new.db" \
  .measure/scratch

python -m ruff check gt_engine/ tests/test_contract_embeddings.py scripts/
```

---

## 5. Deviations from the plan's "files touched", and why

- **`gt_engine/indexer.py` -- not touched, on purpose.** The plan lists it. Its
  `refresh_index_files()` is a five-line function whose body says the producer
  *has no incremental command boundary* and rebuilds the whole graph. There is
  no incremental hook to hang invalidation on, and the file is already 1,516
  lines, well past the 800-line ceiling. The contract-embedding store **is** the
  incremental layer: a full rebuild produces a new graph and the store re-embeds
  only what moved. Wiring `refresh()` into a build pipeline is a one-line call
  whenever a caller wants it.
- **`gt_engine/contract.py` -- touched, though the plan does not list it.** Two
  additive, behaviour-preserving functions (`contracts_with_node_ids()`,
  `symbol_node_ids()`). The alternative was to copy item 3's identity SQL into
  this module, where it would drift. `contracts()` is now a two-line wrapper and
  all 17 item-3 tests pass untouched.
- **`gt_engine/contract_text.py` -- a fifth file the plan does not name.**
  `contract_embeddings.py` crossed 800 lines; the rendering and the store answer
  different questions, so the seam was already there.
- **`gt_engine/retrieval.py` is 1,114 lines**, over the ceiling. It was 997
  before this item; my addition is +117. Splitting a file item 4 has only just
  landed felt like the wrong trade for this stream to make unilaterally --
  flagging it rather than doing it.

---

## 6. What I could not do, and one thing that happened anyway

- **The pinned ONNX asset is absent locally**, so no arctic-embed-m forward pass
  ran, no real vector was produced, and there is no measurement of retrieval
  quality, recall, or embedding throughput in this report. Section 2 says so at
  the top. Everything measurable without it was measured.
- **The branch was pushed to `origin` by the repository's own hook, not by me.**
  `core.hooksPath` is `D:/gt-harness/.githooks` and a `gnx auto-push` hook runs
  on every commit; each of my five commits printed
  `gnx auto-push: <sha> final_hardening/item5-embeddings -> origin/...`. I
  issued no `git push`, and I did not use `--no-verify` (blocked in this
  environment anyway) or override `core.hooksPath` (also blocked). If "do not
  push" has to hold for the sibling streams, that hook needs dealing with at the
  repo level.
- **Commit attribution changed mid-stream.** Commit `c095a48e` carries
  `Co-Authored-By: Claude Fable 5.1` per the original stream brief; the harness
  then issued a standing instruction replacing attribution with
  `Co-Authored-By: Claude Opus 5 (1M context)`, which the four later commits
  carry. Flagging the inconsistency rather than rewriting history.
- **No GT-off evaluation was run, planned, or proposed**, and no paid dispatch
  of any kind was made.
