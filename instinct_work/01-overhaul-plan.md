# GT major-overhaul build plan

**Status:** ready  
**Target state:** clean `eb8714e8b739e37f39e2a6a3e95fe41c7a1db739` plus the central-agent work from `2bf3f4954b123c222b7f6c2b98761654ef2ef007`.  
**Execution rule:** complete in order. A later item may not compensate for a failing earlier item.

## Global invariants

- SQLite remains the durable store.
- Existing schemas get additive, versioned changes and deterministic migrations.
- Existing behavior remains available when `sqlite-vec`, Leiden, an LSP, or a compiler oracle is unavailable.
- Candidate evidence is never promoted to verified evidence by ranking, community membership, or process membership.
- New outputs carry version, provenance, freshness, and stable IDs.
- No points-to analysis, full taint analysis, copied node tables, UI/wiki/Cypher work, or store migration.
- `vendor/` is read-only for the unattended run. Any change that truly requires it must stop with a note instead of editing it.

## 0. Baseline and safety receipt

**Scope**

Confirm the expected commit ancestry, a clean worktree, Python and Go tool availability, and the existing test baseline. Write exact commands and results to `instinct_work/NOTES.md`.

**Files touched**

- `instinct_work/NOTES.md` only

**Acceptance criteria**

- `HEAD` includes the clean base and the intended central-agent changes.
- Worktree is clean before implementation.
- Baseline failures, if any, are recorded before code changes.
- No implementation begins if the expected central-agent files are missing.

**Verify**

```bash
git status --short
git merge-base --is-ancestor eb8714e8 HEAD
git log --oneline --decorate -12
python -m pytest -q
(cd vendor/gt-index-src && go test ./...)
```

A missing tool is a safe stop, not permission to skip its affected work.

## 1. Register direct feature 18: catalog selection

**Scope**

Add `select_catalog` to the direct-feature registry as feature 18. Attribute the planning call from creation through selection, delivery, and outcome receipts. Preserve the existing fail-closed bootstrap behavior. Do not count tool availability as feature delivery; count only a valid selection whose selected items were present in the delivered context.

The current registry begins in [`gt_engine/attribution.py`](https://github.com/harneet2512/gt-harness/blob/2bf3f4954b123c222b7f6c2b98761654ef2ef007/gt_engine/attribution.py#L20). The tool schema is built in [`gt_engine/persistent_execution_state.py`](https://github.com/harneet2512/gt-harness/blob/2bf3f4954b123c222b7f6c2b98761654ef2ef007/gt_engine/persistent_execution_state.py#L88-L116).

**Files touched**

- `gt_engine/attribution.py`
- `gt_engine/persistent_execution_state.py`
- `eval/gt_central_agent.py`
- `tests/test_gt_attribution.py`
- `tests/test_persistent_execution_state.py`
- `tests/test_gt_central_agent.py`

**Acceptance criteria**

- Registry contains a stable feature ID for catalog selection and the feature census reports 18 direct features.
- A valid planning call records visible item IDs, selected IDs, validation result, delivery boundary, and content-safe receipt.
- Unknown, duplicate, malformed, or out-of-catalog IDs fail closed.
- No-selection and fallback modes are distinguishable from successful selection.
- Existing central-agent tests still pass.

**Verify**

```bash
python -m pytest -q tests/test_gt_attribution.py tests/test_persistent_execution_state.py tests/test_gt_central_agent.py
python -m pytest -q
```

## 2. Rich symbol kinds and retained call candidates

**Scope**

Add an additive first-party representation for normalized symbol kind and call-resolution uncertainty. Preserve native parser kind and map it to a documented normalized vocabulary. Store all viable call targets with rank-free provenance before selecting any target. A selected target is optional and must reference one retained candidate.

The vendored Go model already exposes `Method`, `Confidence`, `CandidateCount`, `TrustTier`, and `EvidenceType` on resolved calls in [`resolver.go`](https://github.com/harneet2512/gt-harness/blob/2bf3f4954b123c222b7f6c2b98761654ef2ef007/vendor/gt-index-src/internal/resolver/resolver.go#L405-L425). Treat that source as evidence, not as editable scope during the unattended run.

**Files touched**

- `gt_engine/indexer.py`
- `gt_engine/repository_intelligence.py`
- `gt_engine/graph_evidence.py`
- New first-party migration or adapter module under `gt_engine/` if needed
- `tests/test_gt_repository_intelligence.py`
- `tests/test_graph_retrieval_repairs.py`
- New focused fixtures under `tests/fixtures/`

**Data contract**

For symbols: `native_kind`, `normalized_kind`, `scope_id`, `export_status`, `language`, `provenance`.  
For call candidates: `callsite_id`, `target_id`, `resolution_method`, `resolution_provenance`, `candidate_count`, `unique_in_scope`, `dynamic_dispatch_possible`, `export_status`, `selected`, `verification_status`.

**Acceptance criteria**

- Constructors, free functions, methods, types/classes, interfaces/protocols, fields/properties, modules/packages, imports, tests, and unknowns remain distinguishable.
- Ambiguous fixtures retain every viable candidate and do not emit a verified single-target edge.
- Exact lexical and explicit import fixtures select the correct candidate while retaining provenance.
- Existing stored rows continue to load with conservative defaults.
- If required information can only be produced by changing `vendor/`, stop this item and document the exact missing output contract.

**Verify**

```bash
python -m pytest -q tests/test_gt_repository_intelligence.py tests/test_graph_retrieval_repairs.py
python -m pytest -q
```

## 3. SQLite `vec0` candidate generation and exact hybrid rescore

**Scope**

Persist embeddings in a versioned `vec0` virtual table keyed to GT document/symbol identity. Query ANN for an oversized candidate pool, then apply the existing exact hybrid scorer. Keep deterministic brute-force fallback. Never let ANN distance become evidence authority.

GT's hybrid evidence interfaces live in [`gt_engine/hybrid_retrieval.py`](https://github.com/harneet2512/gt-harness/blob/2bf3f4954b123c222b7f6c2b98761654ef2ef007/gt_engine/hybrid_retrieval.py) and repository assembly in [`gt_engine/hybrid_repository.py`](https://github.com/harneet2512/gt-harness/blob/2bf3f4954b123c222b7f6c2b98761654ef2ef007/gt_engine/hybrid_repository.py).

**Files touched**

- `gt_engine/hybrid_retrieval.py`
- `gt_engine/hybrid_repository.py`
- `gt_engine/indexer.py`
- New `gt_engine/vector_index.py`
- `pyproject.toml` only if an optional dependency is required
- `tests/test_hybrid_retrieval.py`
- `tests/test_hybrid_repository.py`
- New `tests/test_vector_index.py`

**Acceptance criteria**

- Schema records embedding model, dimension, content hash, and index version.
- Unchanged embeddings survive process restart and are not recomputed.
- Changed/deleted documents update/delete vector rows transactionally.
- ANN candidate generation followed by exact rescore matches brute-force top-k on a fixed oracle corpus at the declared recall threshold.
- Missing/incompatible extension takes the deterministic fallback path with a receipt.
- Benchmark reports corpus size, cold/warm latency, candidate count, recall@k, and final ordering agreement.

**Verify**

```bash
python -m pytest -q tests/test_vector_index.py tests/test_hybrid_retrieval.py tests/test_hybrid_repository.py
python -m pytest -q
```

## 4. Leiden communities with two strict projections

**Scope**

Build unweighted communities over: (a) an inclusive structural projection and (b) a verified-only projection. Exclude speculative edges from the strict projection. Do not use scalar confidence or continuous trust as weights. Produce stable fingerprints from sorted member identities, plus run receipts.

**Files touched**

- New `gt_engine/communities.py`
- `gt_engine/graph_context.py`
- `gt_engine/repository_intelligence.py`
- Optional dependency declaration in `pyproject.toml`
- New `tests/test_communities.py`
- `tests/test_gt_repository_intelligence.py`

**Acceptance criteria**

- Fixed graph plus fixed seed is deterministic.
- Strict projection contains only verified edges; inclusive projection labels every admitted evidence class.
- Isolates and disconnected components are handled explicitly.
- Stable community fingerprints survive row-order changes.
- Reindex churn report shows membership stability and coverage for both projections.
- Community membership affects retrieval rank only; it never changes edge verification status.

**Verify**

```bash
python -m pytest -q tests/test_communities.py tests/test_gt_repository_intelligence.py
python -m pytest -q
```

## 5. Witnessed process objects feeding the planning call

**Scope**

Materialize compact, versioned process objects from entry anchors through witnessed structural edges to terminal effects. Build separate strict and inclusive variants. Represent gaps and branches instead of inventing a continuous story. Add process objects to the visible catalog and preserve their selection/delivery IDs.

**Files touched**

- New `gt_engine/process_objects.py`
- `gt_engine/persistent_execution_state.py`
- `gt_engine/repository_intelligence.py`
- `eval/gt_central_agent.py`
- New `tests/test_process_objects.py`
- `tests/test_persistent_execution_state.py`
- `tests/test_gt_central_agent.py`

**Acceptance criteria**

- Every process step references existing nodes, edges, and evidence IDs.
- Strict processes contain verified-only edges.
- Inclusive processes label candidate steps and expose gaps.
- Stable process ID is content-derived and changes when its witnessed path changes.
- Catalog size and token bounds remain enforced.
- A selected process is traceable from planning call to delivered context and feature receipt.
- Cycles, branches, stale edges, and missing anchors have tests.

**Verify**

```bash
python -m pytest -q tests/test_process_objects.py tests/test_persistent_execution_state.py tests/test_gt_central_agent.py
python -m pytest -q
```

## 6. Selective call precision and empirical tiers

**Scope**

Implement `02-trust-calibration.md`. Improve only resolver methods that can be identified and measured: exact lexical AST binding, explicit import-chain resolution, receiver/type evidence, unique-in-scope matches, and retained N-candidate matches. Do not add points-to or full taint analysis.

**Files touched**

- First-party GT adapters, calibration modules, reports, and tests named in `02-trust-calibration.md`
- No `vendor/` edits in the unattended run

**Acceptance criteria**

- Tier is derived from provenance class, never a scalar threshold.
- Explicit uncertainty reaches graph consumers and planning context.
- External-oracle evaluation publishes counts, labeled errors, coverage, and error rate with confidence interval per method.
- Before/after selective-precision report shows whether each changed method improved precision without unacceptable coverage loss.
- Any required vendored-indexer change is recorded as a blocked follow-up with exact proposed contract and no partial workaround.

**Verify**

Use the focused commands in `02-trust-calibration.md`, then:

```bash
python -m pytest -q
(cd vendor/gt-index-src && go test ./...)
```

## 7. Integrated ablation and closeout

**Scope**

Run each feature alone, then the ordered stack. Compare retrieval quality, call-edge precision, strict/inclusive coverage, catalog selection, decision outcomes, latency, and failures. Do not claim improvement from aggregate results without mechanism-level evidence.

**Files touched**

- New versioned report under `artifacts/` or `docs/benchmarks/`, following existing repository convention
- `instinct_work/NOTES.md`

**Acceptance criteria**

- Baseline and treatment use identical corpus, seed, queries, and environment receipt.
- Report includes raw counts, denominators, failures, confidence intervals where applicable, and artifact hashes.
- Each feature can be disabled independently.
- Full Python and Go suites pass.
- Worktree contains no unrelated changes and no modified `vendor/` files.

**Verify**

```bash
python -m pytest -q
(cd vendor/gt-index-src && go test ./...)
git diff --check
git status --short
git diff --name-only -- vendor/
```
