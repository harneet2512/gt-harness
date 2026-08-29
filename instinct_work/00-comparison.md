# GT and gnx: learning plan for a major overhaul

**Status:** ready  
**Code baseline:** `eb8714e8b739e37f39e2a6a3e95fe41c7a1db739`, with the later central-agent work from `2bf3f4954b123c222b7f6c2b98761654ef2ef007` reapplied.  
**Rule:** code wins over damaged or aspirational documentation.

## Starting point

GT is not a blank slate. It already has broad repository ingestion, AST-based graph construction, incremental indexing, local dense embeddings, hybrid structural and semantic evidence, persistent execution state, planning controls, verification, receipts, replay, and failure handling. The overhaul should preserve those strengths.

gnx was better in several narrower places because it represented ambiguity and structure more directly, persisted expensive retrieval work, and gave agents compact behavior-shaped objects instead of raw graph volume. We should learn from those design advantages, test the mechanism behind each one, and build a GT-native version. The goal is not source or schema imitation.

## 1. Make planning a measured feature

**What we borrow from gnx:** compact, precomputed context units that an agent can select before acting.

**Why they were better because of it:** the agent spent less context reconstructing repository structure and made an explicit choice about what mattered. The selected context had a stable identity, so delivery and outcome could be measured.

**How GT learns and improves:** GT already has a `select_catalog` planning call in [`persistent_execution_state.py`](https://github.com/harneet2512/gt-harness/blob/2bf3f4954b123c222b7f6c2b98761654ef2ef007/gt_engine/persistent_execution_state.py#L35-L118) and central-agent wiring in [`gt_central_agent.py`](https://github.com/harneet2512/gt-harness/blob/2bf3f4954b123c222b7f6c2b98761654ef2ef007/eval/gt_central_agent.py). Register it as direct feature 18, preserve the chosen item IDs in receipts, and measure whether a selected item was delivered and used. GT's advantage should be causal attribution, not merely another planning prompt.

## 2. Preserve symbol identity and call ambiguity

**What we borrow from gnx:** richer symbol kinds and retained candidate sets for unresolved or ambiguous calls.

**Why they were better because of it:** queries could distinguish constructors, methods, interfaces, fields, imports, tests, and other roles instead of flattening them into a few generic node kinds. Ambiguous calls remained inspectable rather than becoming one confident-looking edge or disappearing.

**How GT learns and improves:** extend GT's normalized symbol model without cloning another node layout. Preserve the parser's native kind, normalized kind, scope, export status, and call candidates. Keep the chosen target separate from the candidate set. The agent should see candidate count, uniqueness in scope, dynamic-dispatch possibility, and resolution provenance. GT can then be correct-or-explicitly-uncertain while retaining its existing receipts and verification controls.

## 3. Persist vector search inside SQLite

**What we borrow from gnx:** a persisted approximate nearest-neighbor index instead of rebuilding or scanning dense vectors for each process.

**Why they were better because of it:** semantic retrieval was fast enough to use routinely across large repositories. Candidate generation was cheap, and later graph-aware ranking could spend effort on a small set.

**How GT learns and improves:** keep SQLite as the store and add a `vec0` virtual table. Use ANN only to generate candidates, then rescore those candidates with GT's exact similarity, lexical score, graph distance, freshness, authority, and uncertainty signals. Preserve deterministic fallback when the extension is unavailable. This avoids a store migration and makes ANN an acceleration layer rather than a new truth source.

## 4. Add communities as navigation, not truth

**What we borrow from gnx:** Leiden communities that turn a large graph into stable, useful neighborhoods.

**Why they were better because of it:** an agent could retrieve a subsystem-sized unit and its summary instead of walking thousands of edges. Community context improved global orientation without filling the prompt with raw topology.

**How GT learns and improves:** compute two unweighted projections: an inclusive structural projection and a strict verified-only projection. Do not apply continuous trust weights. Store the projection name, algorithm version, seed, membership, stable community fingerprint, and coverage receipt. Compare the projections to expose uncertainty. Communities may guide retrieval and planning, but cannot certify an edge or override direct evidence.

## 5. Turn paths into witnessed process objects

**What we borrow from gnx:** named behavior flows that connect entry points, intermediate calls, data movement, and terminal effects.

**Why they were better because of it:** agents reasoned in the unit of software behavior rather than a bag of files and symbols. That made change impact and test selection easier to explain.

**How GT learns and improves:** build process objects only from witnessed edges. Each process records anchors, ordered steps, branch or gap markers, evidence IDs, strict/inclusive status, freshness, and verification state. Feed compact process items into `select_catalog`; retain the selected process IDs in execution state and receipts. GT improves the idea by making every process auditable and by refusing to smooth over missing links.

## 6. Improve call precision selectively

**What we borrow from gnx:** deeper resolution only where language semantics and evidence justify it.

**Why they were better because of it:** receiver-aware calls and import-aware symbol identity reduced noisy fan-out in impact queries. Better call edges improved every downstream feature, including communities and flows.

**How GT learns and improves:** focus on exact AST binding in lexical scope, explicit import chains, receiver and type facts already present in the AST, and unique candidates under a declared scope. Retain candidates when uniqueness cannot be proved. Use language fixtures and external compiler/LSP oracles to decide which resolver mechanisms deserve a high empirical tier. This is selective precision, not points-to analysis.

## 7. Calibrate trust before consuming it as fact

**What we borrow from gnx:** explicit resolution provenance and visible ambiguity.

**Why they were better because of it:** uncertainty was available to the query and agent layers instead of being hidden behind one scalar.

**How GT learns and improves:** replace hand-scored tiering with tiers derived from resolution mechanism and then calibrated by external outcomes. Exact AST binding, import-chain resolution, receiver/type evidence, unique name match, and N-candidate name match are different evidence classes with different failures. Measure each class against compiler/LSP definitions, persistence across reindex, co-change witnesses, and test/assertion outcomes. This may matter more than any new capability because every graph consumer inherits resolver error. The runnable specification is in `02-trust-calibration.md`.

## What we deliberately do not borrow

- Points-to analysis or whole-program alias analysis
- Full taint analysis
- Another system's node tables or identifiers
- UI, wiki generation, or Cypher compatibility
- A graph-store or vector-store migration
- Continuous trust weighting of graph edges

These exclusions keep the overhaul tied to demonstrated decision value. GT should win by combining precise evidence, visible uncertainty, fast retrieval, witnessed behavior, and measured delivery, not by accumulating surface area.
