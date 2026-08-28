# Task-Conditioned Implementation-Owner Ranking

Date: 2026-08-28  
GitNexus source inspected at: `6088d2e309de134688cb465fc76988ce801e06c6`  
Scope: deterministic repository-context ranking and bounded delivery. This note does not assess agent solve rate and does not authorize a paid benchmark.

## Finding

GroundTruth's observed failure mode is a ranking and delivery defect, not evidence that the graph lacks the correct implementation owners. The compiler found the relevant candidates, but an incidental symbol-token match outranked a stronger task-local module/artifact match. The provider planner then correctly bounded delivery, but bounded the wrong ordering to one owner.

The research-backed correction is:

1. Treat a task phrase matching a compound basename, or both the basename and its immediate parent directory, as a strong **local artifact identity** signal.
2. Use that signal to order implementation-owner candidates before weak, isolated symbol-token ratios.
3. Retain exact symbol identity, graph relations, sparse retrieval, and dense retrieval as independent evidence lanes and deterministic tie-breakers; path text must not create edit authority.
4. Deliver one decisive owner when evidence dominates. Otherwise deliver a typed, bounded ambiguity set rather than silently selecting an arbitrary top result or flooding the context.
5. Preserve exact-SHA, coverage, omission, and truncation receipts so a bounded result cannot masquerade as complete.

This is a general retrieval policy. It does not depend on benchmark task IDs, repository names, oracle paths, model behavior, or memorized patches.

## Evidence classification

### Verified GitNexus implementation facts

These claims are verified against the current public source, not inferred from product marketing.

#### 1. GitNexus deliberately preserves a bounded parent-plus-basename location signal

GitNexus's embedding text generator retains only the last one or two path segments: the immediate parent directory and basename. The implementation is `segments.slice(-2)` and places the bounded location after the description so the description remains the leading semantic signal. Unit tests independently verify that same-named artifacts retain different bounded locations. [GitNexus `text-generator.ts`, pinned source](https://github.com/abhigyanpatwari/GitNexus/blob/6088d2e309de134688cb465fc76988ce801e06c6/gitnexus/src/core/embeddings/text-generator.ts#L60-L120) [GitNexus bounded-location tests, pinned source](https://github.com/abhigyanpatwari/GitNexus/blob/6088d2e309de134688cb465fc76988ce801e06c6/gitnexus/test/unit/text-generator.test.ts#L167-L206)

This is the closest direct competitor evidence for GT's defect. It supports coherent basename-plus-parent and compound-basename evidence over isolated token overlap. It also supplies the necessary restraint: use the local path neighborhood, not the entire path.

#### 2. GitNexus combines lexical, semantic, graph-process, and cohesion signals

GitNexus runs BM25 and semantic retrieval concurrently, combines their ranks using reciprocal rank fusion, maps the fused symbols to persisted execution processes, and ranks processes using aggregate relevance with a small community-cohesion boost. The default response is capped at five processes and ten symbols per process. [GitNexus local query pipeline, pinned source](https://github.com/abhigyanpatwari/GitNexus/blob/6088d2e309de134688cb465fc76988ce801e06c6/gitnexus/src/mcp/local/local-backend.ts#L2539-L2649) [GitNexus process ranking and caps, pinned source](https://github.com/abhigyanpatwari/GitNexus/blob/6088d2e309de134688cb465fc76988ce801e06c6/gitnexus/src/mcp/local/local-backend.ts#L2849-L2875) [GitNexus architecture: embeddings and search](https://github.com/abhigyanpatwari/GitNexus/blob/6088d2e309de134688cb465fc76988ce801e06c6/ARCHITECTURE.md#embeddings-and-search)

The transferable mechanism is multi-lane corroboration followed by bounded output. GT should not copy GitNexus's specific score formula. GT can outperform it by making requirement coverage, evidence authority, graph revision, and ambiguity explicit in the delivered claim contract.

#### 3. GitNexus separates a bounded fast path from its full hybrid query

GitNexus's augmentation fast path uses BM25, examines at most five unique symbol matches, limits callers and callees to three per symbol, enriches them with process and community data, and ranks the bounded result by cohesion. It is designed to inject relationships at search/read decision points under a latency target. [GitNexus augmentation engine, pinned source](https://github.com/abhigyanpatwari/GitNexus/blob/6088d2e309de134688cb465fc76988ce801e06c6/gitnexus/src/core/augmentation/engine.ts)

The useful lesson is delivery timing and strict caps, not the exact ranking policy. Its source also returns an empty string on any augmentation error. GT must not copy that behavior: under GT's release contract, unavailable context must be a typed degraded or failed state, never silent absence.

#### 4. GitNexus budgets output and exposes truncation

GitNexus applies response budgets to query, context, and impact output and includes truncation metadata rather than emitting unbounded graph material. [GitNexus output budget, pinned source](https://github.com/abhigyanpatwari/GitNexus/blob/6088d2e309de134688cb465fc76988ce801e06c6/gitnexus/src/mcp/output-budget.ts#L1-L49)

Bounding output is therefore a competitive requirement, but top-one collapse is not sufficient. A correct bounded system must also expose uncertainty and omitted coverage.

#### 5. A current GitNexus schema claim is stronger than its local implementation

The public query schema accepts `task_context` and `goal` and says they help ranking. [GitNexus query tool schema, pinned source](https://github.com/abhigyanpatwari/GitNexus/blob/6088d2e309de134688cb465fc76988ce801e06c6/gitnexus/src/mcp/tools.ts#L124-L174) In the current local backend, however, the search text is built only from `search_query`/legacy `query`; `task_context` and `goal` are accepted in the parameter type but are not incorporated into the local search query or ranking shown in that implementation. [GitNexus local query implementation, pinned source](https://github.com/abhigyanpatwari/GitNexus/blob/6088d2e309de134688cb465fc76988ce801e06c6/gitnexus/src/mcp/local/local-backend.ts#L2552-L2600)

This is a verified implementation discrepancy, not a claim that all GitNexus entry points ignore task context. It identifies a concrete opportunity for GT to be better: compile task clauses into typed requirements and actually use their local-artifact, role, graph, lexical, and semantic evidence in deterministic ranking.

### Verified published findings

#### 6. Hierarchical structure and semantic retrieval are complementary

Agentless localizes hierarchically: repository tree and issue description identify top files, embedding retrieval adds semantically relevant files, then class/function skeletons narrow the edit region. The method combines structural/path and semantic evidence rather than treating a single token match as sufficient. [Agentless, FSE 2025, Section 3](https://lingming.cs.illinois.edu/publications/fse2025.pdf)

For GT, the direct implication is that coherent module identity is useful early evidence, but must be followed by symbol- and graph-level evidence before authority is granted.

#### 7. Exact identity, content retrieval, and graph traversal each contribute

LocAgent builds hierarchical file/class/function entities, indexes entity identities and contents, and exposes type-aware graph traversal over containment, import, invocation, and inheritance. Its search returns fold/preview/full detail levels specifically to reduce lengthy, noisy context. Its component ablations report lower localization when BM25 or graph traversal is removed. [LocAgent, ACL 2025](https://aclanthology.org/2025.acl-long.426/) [LocAgent paper, Sections 3.1-3.2 and Table 6](https://aclanthology.org/2025.acl-long.426.pdf)

This supports GT's intended sequence: exact/local artifact anchoring, graph expansion, semantic corroboration, then compact role-aware delivery. It does not justify making embedding similarity authoritative.

#### 8. Wider graph expansion can add large amounts of noise and tokens

RepoGraph retrieves task-centered ego graphs. Its reported variants show a sharp growth from one-hop flattened context (about 11.6 nodes, 37.1 edges, and 2,310.7 tokens on average) to two-hop flattened context (about 54.5 nodes, 89.9 edges, and 10,505.3 tokens), and the authors limit exploration to two hops because larger neighborhoods introduce irrelevant nodes and context cost. [RepoGraph, ICLR 2025, Sections 3.2 and 5.2/Table 4](https://proceedings.iclr.cc/paper_files/paper/2025/file/4a4a3c197deac042461c677219efd36c-Paper-Conference.pdf)

The implication is not that GT should always use one hop. It is that every additional owner or relation needs a decision-relevance justification and an explicit budget.

#### 9. Search flooding creates direct agent cost

SWE-agent's interface limits search output to fifty results because broad searches can consume excessive context; the paper describes a trade-off between concise observations and the extra model call needed to reformulate an over-broad query. It also favors simple, consistent result formats. [SWE-agent, NeurIPS 2024, Appendix A](https://papers.neurips.cc/paper_files/paper/2024/file/5a7c947568c1b1328ccc5230172e1e7c-Paper-Conference.pdf)

Fifty is not a target for GT. The general principle is that candidate recall and provider-visible volume are separate quantities. GT may retain a larger candidate ledger while delivering only the evidence that changes the next engineering decision.

#### 10. More context is not monotonically better

Lost in the Middle finds that models can use long contexts unreliably and that performance changes materially with the position of relevant evidence. Increasing retrieved documents from twenty to fifty produced only marginal gains in the paper's tested QA setting. [Lost in the Middle, TACL 2024](https://aclanthology.org/2024.tacl-1.9/)

This is not code-localization evidence by itself. It is primary evidence for the general delivery risk: adding every plausible owner can reduce usable signal even when nominal recall rises.

### Vendor claims

Akon Labs reports that GitNexus provides process-level context and improves DeepSWE outcomes, steps, tokens, and cost. [Akon Labs product and benchmark page](https://www.akonlabs.com/) These are vendor-reported results. They are useful for choosing what to test, but they are not independent evidence that the same mechanism causes GT improvements or that the published comparison is reproducible under GT's controlled benchmark protocol.

The GitNexus maintainer comment for bounded location says that dropping the location regressed service-qualified semantic search and that a full path diluted the embedding. [GitNexus `text-generator.ts`, pinned source](https://github.com/abhigyanpatwari/GitNexus/blob/6088d2e309de134688cb465fc76988ce801e06c6/gitnexus/src/core/embeddings/text-generator.ts#L60-L100) The implementation and its unit behavior are verified; the regression history is maintainer-reported rationale, not an independently reproduced evaluation.

### Engineering inference

No cited source proves GT's exact ranking tuple. The defensible inference from the combined evidence is:

```text
strong local artifact identity
  = compound basename match
    OR (basename match AND immediate-parent match)

owner ordering
  = strong local artifact identity
    -> exact/qualified symbol identity
    -> requirement-role compatibility
    -> graph/process support
    -> sparse and dense corroboration
    -> deterministic tie-break
```

The local-artifact signal should reorder inspection-owner candidates; it must not manufacture an exact symbol, graph edge, requirement binding, or edit instruction. If no candidate clearly dominates across authoritative and corroborating lanes, GT should emit a typed ambiguity set with a hard cap and a receipt listing the omitted candidates.

## Recommended GT contract

For each implementation-owner candidate, persist the following independent fields rather than collapsing them prematurely into one opaque score:

| Evidence lane | Meaning | Can grant edit authority? |
| --- | --- | --- |
| exact identity | Unique parser/LSP/graph-backed symbol resolution | Only with an edit-permitting task directive |
| local artifact | Compound basename or basename-plus-parent task phrase | No; ranks inspection candidates |
| lexical | BM25/token overlap over names and content | No |
| semantic | Embedding similarity over bounded, versioned representations | No |
| structural | Calls/imports/containment/process/impact relation to a resolved anchor | No by itself; can corroborate ownership |
| role | Implementation, public surface, integration, validation, constraint | Controls delivery family, not identity |

Selection must be deterministic under input permutation. Provider delivery should contain at most one decisive implementation owner per resolved requirement, or one atomic ambiguity set of at most two candidates when evidence does not dominate. Public surface, integration, tests, documentation, and constraints must retain separate role budgets; they must not be collapsed into or deleted from the implementation-owner list.

## Required proof before promotion

The change is research-backed only at the mechanism level. GT still has to prove its own implementation:

1. **Targeted regression:** a compound basename and a basename-plus-parent phrase outrank an incidental high symbol-token ratio, with no task IDs or repository-specific rules.
2. **Metamorphic generalization:** repository renames, path-depth changes above the immediate parent, candidate input permutation, and unrelated identifier decoys do not change the selected semantic role.
3. **Authority invariant:** path or embedding evidence alone never creates exact edit authority.
4. **Precision/recall gate:** implementation-role precision remains at least `0.80`, implementation-fact recall reaches at least `0.95`, exact-edit precision remains `1.0`, and required-facet coverage remains at least `0.95` on the frozen localization audit.
5. **Budget gate:** provider-visible owner count and tokens do not increase through broad behavior binding; omitted candidates and truncation remain explicit in receipts.
6. **Whole-product gate:** the exact pushed SHA passes clean install, graph truth and lifecycle, language matrix, query, treatment/provider delivery, failure campaign, and persistence checks in the hosted prerelease certification workflow.

## Rejected interpretations

- **Restore every behavior binding.** Rejected: it raises nominal coverage by flooding unrelated owners and previously reduced implementation precision.
- **Select top two or top three everywhere.** Rejected: a fixed wider window hides ranking defects and spends tokens independent of uncertainty.
- **Make dense similarity authoritative.** Rejected: semantic retrieval is a complementary lane, not proof of symbol identity or edit permission.
- **Copy GitNexus's cohesion formula or tool surface.** Rejected: the evidence supports the mechanism class—hybrid retrieval, structural context, bounded delivery—not source or product cloning.
- **Add benchmark-task or repository-specific bonuses.** Rejected: that is overfitting and provides no basis for release claims.

## Competitive conclusion

Confidence: **high** that coherent local artifact identity should outrank incidental token overlap for implementation-owner inspection ranking. Confidence: **moderate** that this specific change will improve downstream solve rate; only controlled trajectories can establish causality.

The path to outperforming GitNexus is not to return more graph facts. It is to deliver fewer, stronger, proof-carrying facts at the correct decision point: task-conditioned local artifact identity, exact symbol and revision receipts, graph/process corroboration, explicit role separation, bounded ambiguity, and no silent degradation. GitNexus supplies direct evidence for bounded parent-plus-basename semantics and hybrid structural retrieval; GT can exceed that design by making task requirements, authority, completeness, and delivery omissions machine-verifiable end to end.
