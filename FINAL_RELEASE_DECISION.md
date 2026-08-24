# GroundTruth Final Release Decision

Verdict: `HOLD`

Exact implementation SHA under latest official verification: `1fd7efae7eca064cbec56ba319f2169eb81a9ceb`.

GT Harness is suitable for continued controlled prerelease engineering. The
latest official smoke [32700056236](https://github.com/harneet2512/gt-harness/actions/runs/32700056236)
passes end-to-end attestation and still solves 9/20 against the frozen 17/20
baseline while using a different model route. The retry repair removed provider
read-timeout failures but did not improve solve rate. This is not a causal GT
regression measurement; solve-rate superiority, GitNexus superiority, and a
controlled same-model causal uplift remain unproven.

| Question | Answer | Confidence |
| --- | --- | --- |
| A. Mechanically reproducible? | Yes. Exact-SHA clean Linux install, Python, Go, lint, graph, dense, lifecycle, E2E, and failure gates pass. | High |
| B. Complete production project? | Complete for controlled prerelease scope with declared limits; retry and dense-update repairs are live-verified, while the narrowed initial abstention policy awaits replay. | Moderate |
| C. Graph reliably builds? | Yes in the frozen real-repository matrix and for every active second-smoke treatment; unsupported/source-less tasks abstained. | High |
| D. Graph accurate and sufficiently complete? | 62/62 on the bounded independent corpus; all live packet source text was real. Universal recall is not established. | High for corpus, moderate broadly |
| E. Correct across edits, commits, crashes, restarts? | Provider-free lifecycle is 9/9. Live process-kill/outer-timeout receipt finalization is fixed and regression tested, not paid-replayed. | Moderate-high |
| F. Normal coding agent can use it? | Yes through the actual Mini-SWE 2.2.8 Harbor adapter. The latest smoke produced 20 trajectories and passed attestation; the narrowed abstention policy awaits replay. | High |
| G. Claimed languages supported? | Python, JavaScript, TypeScript, Go, Rust, and Java structurally, with explicit parser/semantic limitations. | High |
| H. Competitive with GitNexus? | GT leads the bounded structural/revision audit; GitNexus remains broader in processes, communities, trace, PDG, and compact higher-order retrieval. | Moderate |
| I. Causally improves solve rates? | Unknown. The only repair20 baseline uses another model; no controlled GT-off pair exists. | High |
| J. More efficient without unacceptable regressions? | Latest GT used 591 calls and 22,153,615 input/output tokens versus the prior GT run's 651/30,111,583; the model differs from baseline, so this is observed efficiency only. | High |

## Exact evidence

- latest live run: [32700056236](https://github.com/harneet2512/gt-harness/actions/runs/32700056236), subject `1fd7efae`;
- raw reward: 9/20; attestation: PASS; two explicit product-process errors;
- prior corrected run: [32695000605](https://github.com/harneet2512/gt-harness/actions/runs/32695000605), raw reward 9/20;
- treatment integrity: 18 terminal receipts, two nonterminal receipts;
- context: 14 active treatments, six explicit abstentions, 41 deliveries, no
  fabricated/dummy source text found;
- observed epistemic defect: seven exact-path claims attached unjustified symbol
  authority; fixed at `8931876`;
- latest trajectory usage: 591 calls and 22,153,615 input+output tokens;
- current graph truth: precision/recall `1.0/1.0` over 62 expected relationships;
- lifecycle: 9/9; language matrix: 6/6; failure campaign: 18/18;
- current exact-SHA Codespaces certification: see
  `audit/receipts/codespaces-8931876/codespaces-product-certification.json`.

## Remaining release blockers

1. Run a controlled same-model Bare versus GT experiment before making solve-rate
   or efficiency claims.
2. Re-run the direct repository-intelligence comparison when claiming superiority
   over a particular GitNexus release; do not substitute vendor feature names.

No third paid repair20 run was launched while producing this decision.
