# GroundTruth Final Release Decision

Verdict: `BLOCK`

Exact hosted release subject:
`73217b6f42d0ec5678fe140077d06b9ef5bd0227`.

The exact hosted subject is mechanically reproducible but is not a complete
certified product. Run
[`33105512694`](https://github.com/harneet2512/gt-harness/actions/runs/33105512694)
passed every product gate except localization truth and the fail-closed product
certifier. It exposed a native parser-runtime defect and provider localization
noise. Those causes are repaired and locally verified in the current candidate,
but the candidate is not a release until exact-SHA Linux evidence passes.

| Question | Current answer | Confidence |
| --- | --- | --- |
| A. Mechanically reproducible? | Yes for hosted subject `73217b6`; clean install and all mechanical gates passed. | High |
| B. Complete production project? | Not yet; the repaired candidate lacks the required hosted certification receipt. | High |
| C. Does the graph reliably build? | Structural graph gates pass; the prior semantic parser could crash on real Go/TS repositories and is now pinned/fail-closed locally. | High |
| D. Is graph/context accurate and sufficiently complete? | Bounded graph truth passed; provider localization precision failed hosted and passes the current representative local witnesses. Full-cohort proof is pending. | High |
| E. Correct across edits, commits, crashes, and restarts? | Graph/language lifecycle and failure campaign passed; per-task process isolation preserved native failures. | High |
| F. Can Mini-SWE use the complete product? | Harness E2E passed through the actual product boundary without benchmark substitutes or provider credentials. | High |
| G. Are claimed languages supported? | Hosted language lifecycle passed; parser ABI drift is now explicit failure. | High for declared scope |
| H. Competitive with GitNexus? | GT leads the bounded structural/revision audit; broad superiority remains unproven. | Moderate |
| I. Causal solve-rate improvement? | Not established by this provider-free product campaign. | High |
| J. Better without unacceptable cost/regressions? | Not established until product certification and controlled benchmark evidence exist. | High |

Release blockers:

1. Commit and push the repaired candidate to `harneet2512/gt-harness`.
2. Pass the exact-SHA Linux product matrix and twenty-task localization gates.
3. Replace this verdict with `HOLD` only if product correctness passes but
   competitive/causal evidence remains insufficient; use `SHIP` or
   `SHIP_WITH_LIMITATIONS` only after the corresponding evidence exists.

No paid benchmark is authorized while this verdict is `BLOCK`.
