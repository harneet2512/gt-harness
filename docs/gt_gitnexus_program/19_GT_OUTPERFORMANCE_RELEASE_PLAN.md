# GT outperformance release plan

This is the implementation companion to the parity ledger in
`18_GT_GITNEXUS_PARITY_LEDGER.json`. It is grounded in the pinned GitNexus
source at commit `aac7515d2a8c50a1f8f923c6fb77218b333560d6`, the official Akon
benchmark disclosure, and the current GT release contract.

## Current decision

GT is at **L2: substrate and integrity parity, with outcome parity unproven**.
GitNexus is stronger at process-shaped answers and same-observation graph
augmentation. GT is stronger at revision freshness, task semantics,
validation, delivery certification, and causal auditability.

## Ordered implementation

1. Certify the exact pushed GT commit with the Linux Go/SQLite/ONNX provider-free
   workflow and update `active_release.json` only after the hashes match.
2. Prove the existing bounded process projection on the exact release. It must
   compose current action anchor, certified callers/callees, types, tests,
   routes, and declared checks without adding a provider call.
3. Route the projection through the existing provider-value certificate,
   contribution compiler, request hash, message index, and delivery audit.
4. Bridge only certified receiver/type/MRO/runtime facts; ambiguity and
   conflicting evidence abstain.
5. Run the frozen 20-task matched evaluation with integrity, outcomes,
   efficiency, and intervention reports separate.
6. Run mechanism ablations: current GT, no process projection, process
   projection, and process projection plus coupled-change obligation.

## Acceptance

- Every task row and every provider request is replayable.
- No source-backed task has a missing/stale graph receipt.
- No selected delivery lacks an information-value certificate.
- No reproducible GT-attributable negative flip.
- Positive flips identify the delivered mechanism and first divergence.
- Common-solve median tokens/cost/wall time increase by no more than 10%.
- Process projection demonstrates replaced exploration rather than added text.

The public GitNexus 68.4% result is an external directional target, not a
causal GT comparison. GT may claim outperformance only after a comparable
frozen evaluation or a defensible Pareto result with stronger solve rate,
lower cost per solve, and better regression attribution.
