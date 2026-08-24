# GroundTruth Final Release Decision

Verdict: `HOLD`

Certified implementation SHA: `79321e0da09174805a0909f69dc695dd129a5ebf`.

The core GT Harness prerelease product is complete and certified with declared
limitations. Broad release remains on hold because the authorized final 20-task
trajectory audit has not run, so causal solve-rate uplift and live efficiency are
still unknown.

| Question | Answer | Confidence |
| --- | --- | --- |
| A. Mechanically reproducible? | Yes; clean Linux install/build/test/certification passed. | High |
| B. Complete production project? | Yes for the two-month prerelease Harness scope, with declared limits. | High |
| C. Graph reliably builds? | Yes on 10 frozen repositories; every attempted file failure count was zero. | High |
| D. Graph accurate and sufficiently complete? | 62/62 on the bounded six-language truth corpus; universal recall is unproven. | High for corpus, moderate broadly |
| E. Correct across edits, commits, crashes and restarts? | Yes in 9/9 lifecycle phases and the failure campaign. | High |
| F. Normal coding agent can use the complete product? | Yes through Mini-SWE 2.2.8 and same-observation delivery; no MCP substitute. | High |
| G. Claimed languages genuinely supported? | Python, JavaScript, TypeScript, Go, Rust, Java structurally; declared parser/semantic limits apply. | High |
| H. Competitive with GitNexus? | Competitive on bounded structural facts and integrity; GitNexus remains broader in communities/PDG/framework/cross-repo features. | Moderate |
| I. Causally improves solve rates? | Unknown until the final 20-task run. | High |
| J. Improves efficiency without unacceptable regressions? | Unknown live; provider context is bounded to 500/350 tokens and product latency is measured. | High |

Current evidence:

- product: `CERTIFIED_WITH_DECLARED_LIMITATIONS`;
- graph truth: precision/recall `1.0/1.0` over 62 expected relationships;
- lifecycle: 9/9 PASS;
- language matrix: 6/6 PASS;
- Harness+dense E2E: PASS, 257/256 context tokens, zero provider/network calls;
- failure campaign: 18/18 PASS;
- final paid smoke: authorized but not run.

The decision moves from `HOLD` only after all 20 final trajectories and receipts
are adjudicated. A successful prerelease smoke may justify `SHIP_WITH_LIMITATIONS`;
`SHIP` still requires controlled broader competitive outcome evidence.
