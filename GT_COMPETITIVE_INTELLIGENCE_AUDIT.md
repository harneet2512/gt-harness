# GT Competitive Repository-Intelligence Audit

Status: **bounded direct comparison complete; final live outcome comparison pending**.

Current certified GT implementation: `8931876541ec82ec96799f6c4462b5c0726e4518`.

## Direct fact comparison already completed

The same clean itsdangerous and Redux revisions were queried against GT and
GitNexus `1.6.9` with embeddings disabled. Expected answers were independently
enumerated from source. This 53-fact comparison predates the final GT feature
implementation but remains valid evidence about the graph revision tested then.

| System | TP | FP | FN | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| GT | 50 | 4 | 3 | 0.9259 | 0.9434 | 0.9346 |
| GitNexus | 20 | 0 | 33 | 1.0000 | 0.3774 | 0.5479 |

GT was materially stronger on imports, named TypeScript re-exports, and recall.
GitNexus made fewer claims and therefore had no false positives in this bounded
set. The aggregate is dominated by Redux's 22 named re-exports and must not be
generalized to all repositories.

After that comparison, the final GT graph independently scored 62 TP, 0 FP, and
0 FN over a six-language bounded truth corpus. That is product-truth evidence,
not a same-subject GitNexus score; the two numbers must not be merged.

## What GT learned and implemented

| Earlier weakness | Final mechanism | Verification |
| --- | --- | --- |
| No persistent semantic file retrieval | Revision/model/checksum-bound 768D Snowflake ONNX index | Real Linux build/query/edit/restart |
| Dense and sparse results not composed | Equal-weight deterministic RRF (`k=60`) | Unit and Harness E2E |
| Ranked candidates looked too authoritative | Exact edit targets separated from inspection-only candidates | Compiler regressions and provider packet audit |
| Weak execution context | Persisted exact bounded `CALLS` process paths | Projection tests and real imported-call fix |
| Weak change surface | Typed reverse traversal with edge/assertion evidence and caps | Projection tests and lifecycle |
| Late or extra context turn | Context attached to the action's own observation | Mini-SWE E2E receipt |
| Verbose/dummy-looking packets | Compact v4 fact ledger with evidence IDs and explicit uncertainty | 257/256-token real receipt |
| Duplicate/ambiguous facts | Import candidate and semantic-claim deduplication | Go and Python regressions |

## Current comparison

GT's certified advantages are exact dirty-source identity, fail-closed readiness,
atomic recovery, explicit limitations, claim-level receipts, stronger audited
import/re-export recall, real persistent dense retrieval, and automatic
same-observation Mini-SWE delivery.

GitNexus remains stronger in seeded architecture communities, optional PDG/def-use,
framework-specific routes/ORM/DI/event extraction, contract-aware multi-repository
bridges, and a broader explicit query/tool catalog. GT now has bounded processes,
impact, and hybrid retrieval, so the old statement that those capabilities are
absent is no longer true.

The remaining competitor capabilities are not release blockers because their
incremental agent value is unmeasured. Adding them before the final smoke would
create new surface area without causal evidence.

## Verdict

GT is **structurally competitive on the bounded audited facts and now implements
the higher-order context mechanisms most plausibly connected to agent efficiency**.
Broad superiority remains unproven until the frozen 20-task trajectories show
that the context is correct, consumed, and associated with better outcomes or
lower effort. Confidence: high for implementation and bounded facts; unknown for
causal solve-rate uplift.
