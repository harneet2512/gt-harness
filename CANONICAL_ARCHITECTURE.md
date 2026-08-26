# GroundTruth canonical architecture

The complete canonical architecture is [`arch_type.md`](arch_type.md).

`arch_type` is the authoritative contract for the product intent, production
execution path, component boundaries, graph and dense substrate, context
compiler, provider-visible delivery, Mini-SWE-Agent integration, benchmark
adapters, receipts, failure policy, language boundary, and verification gates.

This file remains as the required Gate 1 audit artifact and stable external
entry point. Architecture details are not duplicated here because two mutable
architecture descriptions would create an avoidable source-of-truth conflict.

The executable authorities remain, in order:

1. `eval/benchmark_product_contract.json` for benchmark product identity;
2. the installed `gt-harness` production entry point and reachable code;
3. emitted exact-SHA receipts for observed runtime behavior; and
4. `arch_type.md` for the maintained architecture explanation.

If any of these disagree, stop provider spend, treat the discrepancy as a
release defect, repair the implementation or contract, and update
`arch_type.md` in the same change.
