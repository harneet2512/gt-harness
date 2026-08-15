# Twenty-task complete regression repair smoke — 2026-08-10

The local baseline census found ten true solve regressions in the latest 89-task
GT-on treatment. A ten-task sample cannot cover all ten while retaining enough
working controls, so the next diagnostic chunk is 20 tasks.

## Composition

- **10 outcome regressions:** `extract-elf`, `mcmc-sampling-stan`,
  `prove-plus-comm`, `qemu-alpine-ssh`, `regex-chess`, `sanitize-git-repo`,
  `torch-tensor-parallelism`, `video-processing`, `winning-avg-corewars`,
  `write-compressor`.
- **7 known GT-working controls:** `headless-terminal`,
  `portfolio-optimization`, `schemelike-metacircular-eval`,
  `cobol-modernization`, `llm-inference-batching-scheduler`,
  `fix-code-vulnerability`, `feal-linear-cryptanalysis`.
- **3 prior GT-on flips:** `count-dataset-tokens`, `largest-eigenval`,
  `torch-pipeline-parallelism`.

The frozen local GT-off arm solves 17/20: all ten regression witnesses and all
seven controls, while the three flips were baseline-unsolved. This gives full
coverage of the known outcome-regression set without using the source-less
`gpt2-codegolf` task as a graph test.

## What this run can establish

It can establish whether the repaired implementation preserves outcomes on
every known regression witness and whether graph/frontier deliveries still
work on the controls. It must report each task's graph applicability, parser
diagnostics, all 17 feature dispositions, effect provenance, first-eligible
delivery timing, request-hash coverage, and provider/model resources.

It cannot establish temperature-1 causality from one run. The 89-task run stays
blocked until this chunk has no uncensored baseline solve loss, no invalid
graph substrate, no late/predictive/duplicate/ungrounded delivery, and passes
the common-solved calls/steps/actions/tokens gate.

## Dispatch safety

The workflow defaults now select this 20-task list. It remains GT-on only,
`certified_full` + `integrated`, `preflight_mode=shadow`, timeout multiplier
1.0, with the frozen local GT-off trajectories as the comparison arm. No
baseline rerun and no 89-task dispatch is part of this change.
