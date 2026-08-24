# GroundTruth Project Completeness Audit

Implementation subject: `79321e0da09174805a0909f69dc695dd129a5ebf`

Observed: `2026-08-24` in a clean 32-GB Linux Codespace.

Verdict: **complete prerelease product with declared limitations**. This does not
establish agent solve-rate uplift; the final 20-task run has not been dispatched.

| Component | Exists | Production reachable | Tested | Real-world verified | Canonical | Disposition |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `gt-harness` CLI | Yes | Yes | Yes | Yes | Yes | PRODUCTION |
| Source-built Go indexer | Yes | Yes | Yes | 10 repositories | Yes | PRODUCTION |
| Graph readiness/identity receipt | Yes | Yes | Yes | Clean, dirty, stale, corrupt, interrupted | Yes | PRODUCTION |
| SQLite persistence/publication | Yes | Yes | Yes | Warm/restart/concurrency | Yes | PRODUCTION |
| Structural graph query service | Yes | Yes | Yes | 62 independently derived expected relationships | Yes | PRODUCTION |
| Persistent dense index | Yes | Yes | Yes | Real pinned Snowflake ONNX build/query/edit/restart | Yes | PRODUCTION |
| Dense+sparse RRF | Yes | Yes | Yes | Harness E2E | Yes | PRODUCTION |
| Bounded process projection | Yes | Yes | Yes | Persisted exact `CALLS` evidence | Yes | PRODUCTION |
| Bounded impact projection | Yes | Yes | Yes | Persisted exact relations | Yes | PRODUCTION |
| Semantic graph | Yes | Yes | Yes | Python source facts; duplicate-fact regression | Yes | PRODUCTION WITH LANGUAGE LIMITS |
| Mini-SWE treatment seam | Yes | Yes | Yes | Same-observation edit/update/restart | Yes | PRODUCTION |
| Harbor adapter | Yes | Yes | Yes | Provider-free contract tests; paid run pending | Yes | PRODUCTION BENCHMARK ADAPTER |
| Product certifier | Yes | Yes | Yes | Accepted exact Linux bundle with zero errors | Yes | PRODUCTION |
| MCP server | No | No | N/A | N/A | No | REMOVED; not the product |
| Nano agent path | No tracked product source | No | Static workflow rejection | N/A | No | REMOVED/EXCLUDED |
| Historical central engine and experiments | Yes | No | Inherited suite | Historical only | No | RESEARCH/LEGACY |

## Defects repaired in the final implementation series

1. Dense retrieval was optional and not persisted over the full supported-source
   inventory. It is now content-addressed by repository revision, graph inputs,
   model identity, tokenizer identity, and payload checksum.
2. Dense results were appended independently instead of fused with sparse evidence.
   File rankings now use deterministic equal-weight reciprocal-rank fusion (`k=60`).
3. Lexical/dense candidates could look like edit authority. Only exact source symbol
   or path identities become edit targets; ranked candidates are explicitly inspection-only.
4. Context updates arrived at the next provider boundary. They are now attached to
   the exact tool observation that created the evidence; raw tool output is preserved.
5. Provider context was verbose and weakly structured. The v4 ledger emits compact
   edit targets, inspection candidates, verified relations, process paths, impacts,
   tests, validation facts, limitations, and revision receipts under 500/350 tokens.
6. Process and impact context used shallow local approximations. It now projects from
   the persisted graph with hard depth, branch, expansion, result, and evidence limits.
7. Python imported calls could be falsely marked ambiguous because the same target
   was collected twice. Resolver candidates are now deduplicated before ambiguity is
   assigned; a real two-file import/call regression proves the fix.
8. The semantic graph could emit an identical fact twice from overlapping symbol
   documents. Facts are now deduplicated by stable claim identity and the receipt
   reports how many duplicates were removed.
9. Product certification still pointed at an obsolete MCP campaign. The MCP product
   path, tests, dependency, CLI command, and audit were removed and replaced by the
   actual Mini-SWE Harness E2E campaign.
10. The canonical Harbor path did not upload the dense model into each task or bind
    the installed product to an exact SHA. It now uploads the checksum-verified model,
    installs Mini-SWE-Agent 2.2.8, requires `hybrid_required`, and receipts the SHA.
11. The first dense Codespaces gate used authenticated `gh release download`, which
    fails inside an unauthenticated Codespace. It now downloads the public assets over
    HTTPS and independently verifies both pinned SHA-256 values.

## Canonical boundary and retained work

The sole coding-agent product boundary is `gt-harness run` using Mini-SWE-Agent
2.2.8. Historical central/LSP/benchmark code remains only where it contains unique
research evidence; it cannot certify or enter the final workflow. The obsolete MCP
product and tracked Nano ambiguity were removed. This preserves useful prior work
without leaving multiple production answers.

Raw Linux receipts are committed under
`audit/receipts/codespaces-79321e0/`. All campaign steps report `PASS`, the checkout
was clean, provider calls were `0`, and provider credentials were not inspected.
