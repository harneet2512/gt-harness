# GroundTruth Final Release Decision

Verdict: `HOLD`

Exact implementation SHA under current provider-free verification:
`2bab25973fd1e4e90372aac30231bbbe3009b863`.

GT Harness is suitable for continued controlled prerelease engineering. The
latest official smoke [32717496816](https://github.com/harneet2512/gt-harness/actions/runs/32717496816)
passes end-to-end attestation and solves 13/20 against the frozen 17/20 baseline
while using a different model route. The retry and dense-update repairs are
live-verified. This is not a causal GT regression measurement; solve-rate
superiority, GitNexus superiority, and a controlled same-model causal uplift
remain unproven. Two task-level errors remain explicit in the receipts.

Localization v5 is verified provider-free and on exact archived Awilix/Boa
replays. The next official 20-task smoke is authorized but not yet represented
in this verdict; `HOLD` remains until that run and its trajectories are audited.

| Question | Answer | Confidence |
| --- | --- | --- |
| A. Mechanically reproducible? | Yes. Exact-SHA clean Linux install, Python, Go, lint, graph, dense, lifecycle, E2E, and failure gates pass. | High |
| B. Complete production project? | Complete for controlled prerelease scope with declared limits; retry and dense-update repairs are live-verified, but two task-level process failures and weak context packets remain. | Moderate |
| C. Graph reliably builds? | Yes in the frozen real-repository matrix and for every active second-smoke treatment; unsupported/source-less tasks abstained. | High |
| D. Graph accurate and sufficiently complete? | 62/62 on the bounded independent corpus; all live packet source text was real. Universal recall is not established. | High for corpus, moderate broadly |
| E. Correct across edits, commits, crashes, restarts? | Provider-free lifecycle is 9/9. Live process-kill/outer-timeout receipt finalization is fixed and regression tested, not paid-replayed. | Moderate-high |
| F. Normal coding agent can use it? | Yes through the actual Mini-SWE 2.2.8 Harbor adapter. The latest smoke produced 20 trajectories and passed attestation; `regex-chess` timed out and `write-compressor` exited with product-process 124. | Moderate-high |
| G. Claimed languages supported? | Python, JavaScript, TypeScript, Go, Rust, and Java structurally, with explicit parser/semantic limitations. | High |
| H. Competitive with GitNexus? | GT leads the bounded structural/revision audit; GitNexus remains broader in processes, communities, trace, PDG, and compact higher-order retrieval. | Moderate |
| I. Causally improves solve rates? | Unknown. The only repair20 baseline uses another model; no controlled GT-off pair exists. | High |
| J. More efficient without unacceptable regressions? | Latest GT used 627 calls and 22,333,393 input/output tokens versus the frozen baseline's 1,041/65,625,578, but it lost five baseline solves and the model differs; efficiency is observed, not quality-certified. | High |

## Exact evidence

- latest live run: [32717496816](https://github.com/harneet2512/gt-harness/actions/runs/32717496816), subject `c0b296f9`;
- raw reward: 13/20; attestation: PASS; one timeout and one explicit product-process error;
- prior corrected run: [32695000605](https://github.com/harneet2512/gt-harness/actions/runs/32695000605), raw reward 9/20;
- treatment integrity: 18 terminal receipts, two nonterminal receipts;
- context: 14 active treatments, six explicit abstentions, 41 deliveries, no
  fabricated/dummy source text found;
- observed epistemic defect: seven exact-path claims attached unjustified symbol
  authority; fixed at `8931876`;
- latest trajectory usage: 627 calls and 22,333,393 input+output tokens;
- current graph truth: precision/recall `1.0/1.0` over 62 expected relationships;
- lifecycle: 9/9; language matrix: 6/6; failure campaign: 18/18;
- current exact-SHA Codespaces certification: see
  `audit/receipts/localization-v5-2bab259.json` and the predecessor full campaign
  `audit/receipts/codespaces-8931876/codespaces-product-certification.json`.

## Remaining release blockers

1. Run a controlled same-model Bare versus GT experiment before making solve-rate
   or efficiency claims.
2. Re-run the direct repository-intelligence comparison when claiming superiority
   over a particular GitNexus release; do not substitute vendor feature names.

One new official repair20 run is authorized after the current documentation freeze;
its run ID and result will replace this pending statement after trajectory audit.
