# gnx-parity build plan

**Status:** ready
**Derived from:** [03-gt-gnx-pipeline.md](03-gt-gnx-pipeline.md) — the itemized
delta and its measured baseline.
**Execution rule:** complete in order. A later item may not compensate for a
failing earlier item.
**Rule carried from [00-comparison.md](00-comparison.md):** the goal is a
GT-native version of a capability, not source or schema imitation. Code wins
over aspirational documentation.

## Measured baseline this plan is scored against

Every acceptance criterion below is stated against numbers read out of a real
graph — producer `cffca1fd2`, arktype at its own task base commit `04355e8b`,
458 files:

| Quantity | Today |
|---|---|
| Code symbols / total nodes | 3,511 / 159,548 = **2.2%** |
| Semantic edges / total edges | 8,972 / 188,264 = **4.8%** |
| CERTIFIED edges | 4,593 = **2.4%** |
| Callsites resolved | 5,933 / 16,431 = **36%** |
| Property facts per symbol | 9,233 / 3,511 = **2.6** |
| `signature` fill | 40.1% |
| `receiver_type` fill | **0.1%** — 8 of 10,820 |
| Fact nodes per callsite | **≈6.9** |
| `cochanges` | **0 rows** |
| Repos in smoke20 that publish | **18 / 19** |

## Global invariants

- SQLite remains the durable store. Schema changes are additive and versioned.
- Candidate evidence is never promoted to verified evidence by ranking,
  community membership, process membership, or retrieval score.
- Every new output carries version, provenance, freshness and stable IDs.
- **Fixture-first is mandatory for protected producer paths.** `sqlite.go`,
  `main.go`, `resolution_v2.go`, `resolution_contract.go`, `incremental.go`,
  `publication*.go` and `internal/resolver/*.go` are protected by the pre-push
  hook: a `test(red):` commit containing only tests plus a re-executable
  `gt.fixture-red.v1` receipt must precede the fix commit.
- No gnx source or schema is copied. Where this plan says "copy", it means copy
  the *design decision*, restated in GT's own model.
- An item that cannot be measured against the baseline table is not done.
- `vendor/` is read-only for unattended work.
- Nothing in this plan runs, plans, or proposes a GT-off evaluation.

---

## 0. Baseline and safety receipt

**Scope**

Record the starting state so every later claim is diffable: producer HEAD and
tree, harness HEAD, the producer test baseline, and a fresh capture of the
baseline table above from a rebuilt arktype graph.

**Files touched**

- `instinct_work/NOTES.md` only

**Acceptance criteria**

- Producer HEAD, tree and vendored binary digest recorded and agreeing with
  `config/deepswe_product_bundle_v1.json`.
- Producer suite result recorded before any change.
- The baseline table reproduced from a freshly built graph, not copied from this
  document.

**Verify**

```bash
git -C D:/gt-producer-cha rev-parse HEAD HEAD^{tree}
python scripts/verify_producer_binding.py --producer-repo D:/gt-producer-cha
(cd D:/gt-producer-cha/gt-index && go test -tags sqlite_fts5 ./...)
python -m pytest -q tests/test_product_acceptance.py
```

A missing tool is a safe stop, not permission to skip its affected work.

---

## 1. Budgeted abstention — closes delta row 14

**Scope**

Publication is uncapped, which is why boa writes past 11.5 GB of WAL and never
finishes. Introduce a per-callsite fact budget and an aggregate fact budget,
with `0` meaning disabled — the ergonomics gnx already got right.

Amplify past gnx: exceeding the budget must **produce evidence, not silence**.
`resolutionDerivation` already returns an `abstentionReason` and the vocabulary
already carries `candidate_only_flow_evidence` and
`dynamic_target_not_statically_proven`. A callsite over budget publishes as
`candidate_only` with `abstention_reason = candidate_budget_exceeded` and a
summary fact in place of its full derivation set. The budget in force is sealed
into the index receipt so a degraded graph is provably degraded.

This is the anytime-algorithm contract: an interruptible computation must expose
what it gave up. It is also what makes GT's atomic publication survivable —
today it publishes everything or nothing.

**Files touched**

- `gt-index/cmd/gt-index/main.go` *(protected — fixture-first)*
- `gt-index/internal/store/sqlite.go` *(protected — fixture-first)*
- `gt-index/cmd/gt-index/budget_test.go`
- `.githooks/tests/har83_fact_budget_red.sh` + red artifact + receipt
- `gt_engine/indexer.py` — pass and record the budget
- `tests/test_index_fact_budget.py`

**Acceptance criteria**

- boa (`70409a50`) publishes a graph within the task time budget, with
  abstentions recorded rather than facts silently dropped.
- Every other smoke20 repository still publishes, with node and edge counts
  within ±1% of the baseline at the default budget.
- `abstention_reason = candidate_budget_exceeded` appears on exactly the
  callsites that exceeded budget, and on no others.
- The effective budget is present in the index receipt.
- Budget `0` reproduces today's counts exactly.

**Verify**

```bash
sh .githooks/tests/har83_fact_budget_red.sh          # RED before the fix
(cd gt-index && go test -tags sqlite_fts5 ./...)
gt-index -root <boa> -output boa.db -closure=true    # completes
sqlite3 boa.db "select count(*) from resolution_callsites
                where abstention_reason='candidate_budget_exceeded'"
```

---

## 2. Content addressing — closes delta row 1

**Scope**

gnx stores the source text on the node, which duplicates the repository and goes
stale silently. Store an *address* instead: `(file_hash, byte_start, byte_end)`
on every code symbol, resolved at delivery time against a hash-verified file.
Symbols already carry line ranges; callsites already carry byte ranges.

The win over gnx is that staleness becomes a **detectable mismatch** rather than
silent rot, and the store does not grow by the size of the repository.

**Files touched**

- `gt-index/cmd/gt-index/main.go` *(protected — fixture-first)*
- `gt-index/internal/store/sqlite.go` *(protected — fixture-first)*
- `gt_engine/bridge.py` — resolve an address at delivery
- `tests/test_content_address.py`

**Acceptance criteria**

- Every `Function`, `Class`, `Method` node carries a resolvable address.
- Delivering a symbol whose file hash no longer matches raises a named error;
  it never silently delivers stale text.
- Graph size grows by less than 5% against the baseline.

**Verify**

```bash
sqlite3 ark.db "select count(*) from nodes
  where label in ('Function','Class','Method') and byte_start is null"   # 0
python -m pytest -q tests/test_content_address.py
```

---

## 3. Structured behavioural contract — closes delta row 2

**Scope**

gnx stores an LLM-written `description`. GT should not paraphrase; it should
project the facts it already stores into a contract:
`{params, returns, guards[], side_effects[], boundaries[]}` assembled from the
existing `param`, `return_shape`, `guard_clause`, `boundary_condition` and
`side_effect` property kinds.

Deterministic, needs no model, and — unlike prose — **diffable**, which matters
because `signature_delta` already exists to diff it across a commit.

**Files touched**

- `gt_engine/contract.py` *(new)*
- `gt_engine/bridge.py`
- `tests/test_symbol_contract.py`

**Acceptance criteria**

- Contract generation is pure and deterministic: same graph, byte-identical
  contract.
- A symbol with no property facts yields an explicitly empty contract, never a
  fabricated one.
- Contract coverage is reported as a measured percentage of symbols; the current
  property density (2.6 facts/symbol) is the floor to improve on, not a target
  to declare met.

**Verify**

```bash
python -m pytest -q tests/test_symbol_contract.py
python -c "from gt_engine.contract import coverage; print(coverage('ark.db'))"
```

---

## 4. Hybrid retrieval over the contract — closes delta row 4

**Scope**

`nodes_fts` already exists and is populated, and `sqlite_fts5` is already in the
build tags, so the lexical half is nearly free. Index contract fields and
property values so a query like "validates empty input" reaches a `guard_clause`
row directly. Fuse lexical and dense rankings rather than replacing one with the
other — dense retrievers are known to degrade out of domain, and rank fusion is
the robust combiner.

**Files touched**

- `gt-index/internal/store/sqlite.go` *(protected — fixture-first)*
- `gt_engine/retrieval.py`
- `tests/test_hybrid_retrieval.py`

**Acceptance criteria**

- FTS covers contract fields and property values, not only symbol names.
- Fusion is deterministic and its inputs are recorded, so a delivery can be
  attributed to the ranking that produced it.
- Lexical-only and dense-only remain runnable for comparison.

**Verify**

```bash
python -m pytest -q tests/test_hybrid_retrieval.py
```

---

## 5. Contract embeddings and fingerprint invalidation — closes delta rows 3 and 5

**Scope**

Embed the contract from item 3 rather than raw source, keyed by `stable_id`, so
retrieval matches behaviour instead of vocabulary and survives renaming and
reformatting. GT already ships ONNX arctic-embed-m, a larger model than gnx's
xs.

Invalidate on the semantic `fingerprint` property that GT already computes, not
on a byte hash: reformatting must not trigger re-embedding; a behaviour change
must.

**Files touched**

- `gt_engine/dense_runtime.py`
- `gt_engine/indexer.py`
- `tests/test_contract_embeddings.py`

**Acceptance criteria**

- Vectors are bound to `stable_id` with a line range, so a hit is replayable and
  receipt-attributable.
- Reformatting a file changes no vectors.
- Changing a guard or a return shape changes exactly the affected vectors.

**Verify**

```bash
python -m pytest -q tests/test_contract_embeddings.py
```

---

## 6. Co-change extraction — the prerequisite for delta row 6

**Scope**

`cochanges` exists in the schema and is empty in every graph built. Populate it
from git history: file and symbol pairs that change together, with support and
confidence, over a bounded window.

This is the one signal gnx **structurally cannot hold** — its store is a
snapshot with no history. Evolutionary coupling is also known not to be
congruent with structural coupling, which is precisely why both are worth having.

**Files touched**

- `gt-index/cmd/gt-index/main.go` *(protected — fixture-first)*
- `gt-index/internal/cochange/` *(new)*
- `tests/test_cochange_extraction.py`

**Acceptance criteria**

- `cochanges` is non-empty for every repository with more than one commit in the
  window, and the window is recorded in the receipt.
- Extraction is bounded and honours the item 1 budget.
- A shallow clone with no history yields zero rows and an explicit reason, not a
  silent empty table.
- `cochange_partner` evidence becomes emittable, which is the precondition for
  `cochange_prior` ever firing — it is currently dead at two layers
  (no emitter, and absent from every capability pack).

**Verify**

```bash
sqlite3 ark.db "select count(*) from cochanges"     # > 0
python -m pytest -q tests/test_cochange_extraction.py
```

---

## 7. Communities on trust-weighted coupling — closes delta row 6

**Scope**

Copy gnx's node shape including the discipline worth taking verbatim:
`heuristicLabel` kept alongside `label`, and `enrichedBy` recorded, so the graph
degrades to heuristics without a model and a reader can always tell which they
got.

Amplify on three axes gnx cannot follow: cluster a **trust-weighted multigraph**
of certified `CALLS` plus co-change edges rather than a confidence-flat
structural graph; use a clustering objective with an explicit resolution
parameter, since modularity optimisation has a known resolution limit that hides
small communities and the naive algorithm can emit internally disconnected
communities; and store a **falsifiable** cohesion — the measured rate at which
the community predicted co-modification on held-out commits, with a Wilson
interval, matching the evaluation discipline
[02-trust-calibration.md](02-trust-calibration.md) already sets.

**Files touched**

- `gt-index/internal/community/` *(new)*
- `gt-index/cmd/gt-index/main.go` *(protected — fixture-first)*
- `tests/test_communities.py`

**Acceptance criteria**

- Communities are internally connected. No community spans a tier it was not
  weighted for.
- Every community stores the evidence set behind its label.
- Cohesion is the measured held-out prediction rate with an interval, not a
  structural score.
- Community membership never promotes a candidate edge to verified.

**Verify**

```bash
python -m pytest -q tests/test_communities.py
python -m scripts.community_holdout --db ark.db --holdout 50
```

---

## 8. Test-witnessed processes — closes delta row 7

**Scope**

A process is an interprocedural slice. gnx's are heuristic — a guessed entry
point and terminal. GT already stores `assertions` mapping tests to targets, so
a GT process can be **witnessed**: a certified closure path from an entry point
to a terminal, together with the assertion that exercises it.

That makes a flow executable-verified rather than inferred, and gnx has no
test↔code linkage at all.

**Files touched**

- `gt-index/internal/process/` *(new)*
- `tests/test_processes.py`

**Acceptance criteria**

- Every published process names its witnessing assertion, or is not published.
- Paths are drawn from certified edges only.
- `assertions` coverage is reported: it is 105 rows on arktype today, so the
  honest first result is that few processes qualify. Report the number; do not
  lower the bar to raise it.

**Verify**

```bash
python -m pytest -q tests/test_processes.py
sqlite3 ark.db "select count(*) from processes where witness_assertion_id is null"  # 0
```

---

## 9. Two-phase publication with separate receipts — closes delta row 15

**Scope**

Publication is one all-or-nothing transaction, so an analysis failure costs the
core graph. Split it: core graph commits first with its own receipt, analysis
(communities, processes, closure) commits second with its own. gnx's
`skipGraphPhases` gives degradation; the receipts give degradation *plus*
knowing which layer you got.

**Files touched**

- `gt-index/cmd/gt-index/publication.go` *(protected — fixture-first)*
- `gt_engine/indexer.py`
- `tests/test_two_phase_publication.py`

**Acceptance criteria**

- An analysis failure leaves a valid core graph and a receipt that says analysis
  is absent.
- A core failure publishes nothing, as today.
- Receipts distinguish "analysis not run" from "analysis ran and found nothing".

**Verify**

```bash
python -m pytest -q tests/test_two_phase_publication.py
```

---

## 10. Projections and cheap resolution wins — closes delta rows 10–13

**Scope**

Four items that need no new analysis:

- **`reason` per edge** rendered from the 10,820 `DerivationFact` rows, so a
  justification cannot drift from what produced the edge (gnx's is written by
  hand alongside it).
- **`step` per edge** projected from the `pass_kind` already on 124,515 nodes.
- **Overload narrowing** as a query over `param` facts and signatures.
- **MRO** by C3 linearisation over inheritance chains already extracted,
  published with a trust tier so an inconsistent hierarchy stays visibly
  ambiguous.

**Files touched**

- `gt-index/internal/store/resolution_v2.go` *(protected — fixture-first)*
- `gt-index/internal/resolver/` *(protected — fixture-first)*
- `tests/test_edge_projections.py`

**Acceptance criteria**

- `reason` and `step` are derived, never hand-written.
- Overload narrowing raises resolution rate above the 36% baseline; the delta is
  reported per language.
- MRO resolution is published with a tier and never silently orders an
  inconsistent hierarchy.

**Verify**

```bash
(cd gt-index && go test -tags sqlite_fts5 ./...)
python -m pytest -q tests/test_edge_projections.py
```

---

## 11. Symbol taxonomy and edge kinds — closes delta rows 8 and 9

**Scope**

gnx has 23 node tables; GT emits 4 labels in practice. Do not add 19 tables —
extend symbol emission and keep one node model, with `kind` plus typed property
rows, so the taxonomy stays queryable as it grows. The same work lands across
**30 languages to gnx's 10**.

Then add edge kinds — `ACCESSES`, `INJECTS`, `HANDLES_ROUTE`,
`METHOD_OVERRIDES` — each carrying the mechanism that produced it and a trust
tier. gnx's one-row-one-target schema forces a collapse at write time; GT
retains candidate sets, so two analyses that disagree stay representable.

**Files touched**

- `gt-index/internal/parser/` *(protected — fixture-first)*
- `gt-index/internal/resolver/` *(protected — fixture-first)*
- `tests/test_symbol_taxonomy.py`

**Acceptance criteria**

- Struct, Enum, Trait, Impl, TypeAlias and Namespace are emitted where the
  language has them, measured per language.
- Code symbols as a share of nodes rises materially above the 2.2% baseline.
- Every new edge kind carries a mechanism and a tier; none defaults to CERTIFIED.

**Verify**

```bash
(cd gt-index && go test -tags sqlite_fts5 ./...)
python -m scripts.graph_composition --db ark.db
```

---

## Standing items outside this plan

These are open on HAR-81 and are not superseded by this plan:

1. **Review packet for producer `cffca1fd2`** on the `gt-review-inbox` branch —
   blocks dispatch. The provenance check reads the packet from that branch, so
   no edit to the bundle can substitute for it.
2. **Closure `MaxDepth` 3 → 6 was requested and is not done.** This plan does
   not schedule it, and records why: only 2.4% of edges are CERTIFIED, so
   raising depth widens traversal over an already-thin set. It makes the closure
   larger without making it know more. Items 1–5 above raise what a symbol is
   worth once reached; item 10 raises how many are reachable at all. Depth
   should be revisited after resolution rate moves.
