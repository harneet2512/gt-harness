# GroundTruth Final Release Decision

Verdict: `HOLD`

Exact implementation SHA: `8931876541ec82ec96799f6c4462b5c0726e4518`.

GT Harness is suitable for continued controlled prerelease engineering. It is not
ready for a broad competitive release. The second live smoke proved that graph and
dense delivery work on real Harbor tasks and that the major stale/QEMU failures
were repaired, but it also found two nonterminal product receipts. Their causes are
fixed and provider-free certified at the implementation SHA, not yet live-replayed.
Causal solve-rate uplift and GitNexus superiority remain unproven.

| Question | Answer | Confidence |
| --- | --- | --- |
| A. Mechanically reproducible? | Yes. Exact-SHA clean Linux install, Python, Go, lint, graph, dense, lifecycle, E2E, and failure gates pass. | High |
| B. Complete production project? | Complete for controlled prerelease scope with declared limits; current supervisor/deadline fixes still need live replay. | Moderate |
| C. Graph reliably builds? | Yes in the frozen real-repository matrix and for every active second-smoke treatment; unsupported/source-less tasks abstained. | High |
| D. Graph accurate and sufficiently complete? | 62/62 on the bounded independent corpus; all live packet source text was real. Universal recall is not established. | High for corpus, moderate broadly |
| E. Correct across edits, commits, crashes, restarts? | Provider-free lifecycle is 9/9. Live process-kill/outer-timeout receipt finalization is fixed and regression tested, not paid-replayed. | Moderate-high |
| F. Normal coding agent can use it? | Yes through the actual Mini-SWE 2.2.8 Harbor adapter. The second smoke produced 20 trajectories and 41 GT deliveries. | High |
| G. Claimed languages supported? | Python, JavaScript, TypeScript, Go, Rust, and Java structurally, with explicit parser/semantic limitations. | High |
| H. Competitive with GitNexus? | GT leads the bounded structural/revision audit; GitNexus remains broader in processes, communities, trace, PDG, and compact higher-order retrieval. | Moderate |
| I. Causally improves solve rates? | Unknown. The only repair20 baseline uses another model; no controlled GT-off pair exists. | High |
| J. More efficient without unacceptable regressions? | Directionally fewer calls/tokens, but invalid GT treatments and a different model prevent a causal efficiency claim. | High |

## Exact evidence

- second live run: [32680131105](https://github.com/harneet2512/gt-harness/actions/runs/32680131105), subject `b6e1609`;
- raw reward: 12/20; valid terminal treatment reward: 11/20;
- treatment integrity: 18 terminal receipts, two nonterminal receipts;
- context: 14 active treatments, six explicit abstentions, 41 deliveries, no
  fabricated/dummy source text found;
- observed epistemic defect: seven exact-path claims attached unjustified symbol
  authority; fixed at `8931876`;
- trajectory usage: 691 calls and 33,554,636 input+output tokens;
- current graph truth: precision/recall `1.0/1.0` over 62 expected relationships;
- lifecycle: 9/9; language matrix: 6/6; failure campaign: 18/18;
- current exact-SHA Codespaces certification: see
  `audit/receipts/codespaces-8931876/codespaces-product-certification.json`.

## Remaining release blockers

1. Replay a bounded live smoke on `8931876` and require all GT product receipts to
   be terminal, all call counts trajectory-backed, and final attestation PASS.
2. Run a controlled same-model Bare versus GT experiment before making solve-rate
   or efficiency claims.
3. Re-run the direct repository-intelligence comparison when claiming superiority
   over a particular GitNexus release; do not substitute vendor feature names.

No third paid repair20 run was launched while producing this decision.
