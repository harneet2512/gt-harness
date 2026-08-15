# Frozen-baseline regression census — 2026-08-10

## Method

The local frozen GT-off source is
`C:\Users\Lenovo\Downloads\gt-off-baseline deepseeknew`. I compared its
89-task `SUMMARY.md` outcome table with the latest full GT-on treatment
`D:\gt_runs\31355487270\SUMMARY.md`. A solve regression means exactly
`baseline solved=yes` and `GT-on solved=no`; baseline-unsolved tasks are not
regressions. Resource regressions are reported separately and never promoted
to outcome losses.

## Outcome regressions confirmed

The full treatment had **10** true solve regressions:

`extract-elf`, `mcmc-sampling-stan`, `prove-plus-comm`, `qemu-alpine-ssh`,
`regex-chess`, `sanitize-git-repo`, `torch-tensor-parallelism`,
`video-processing`, `winning-avg-corewars`, and `write-compressor`.

It also had four baseline-unsolved → GT-on-solved flips:
`count-dataset-tokens`, `largest-eigenval`, `protein-assembly`, and
`torch-pipeline-parallelism`. A flip is a useful witness but not proof that GT
caused the solve.

## Resource-only regressions on common solves

Among the 56 tasks solved in both arms, GT-on used more total model tokens on
7 tasks: `financial-document-processor`, `qemu-startup`,
`feal-linear-cryptanalysis`, `cobol-modernization`, `code-from-image`,
`multi-source-data-merger`, and `git-leak-recovery`.

It used more model calls on 20 common solves:

`qemu-startup`, `financial-document-processor`, `cobol-modernization`,
`feal-linear-cryptanalysis`, `fix-ocaml-gc`, `dna-assembly`,
`break-filter-js-from-html`, `tune-mjcf`, `code-from-image`,
`multi-source-data-merger`, `distribution-search`, `fix-git`,
`sqlite-db-truncate`, `git-leak-recovery`, `overfull-hbox`,
`password-recovery`, `bn-fit-modify`, `circuit-fibsqrt`,
`nginx-request-logging`, and `vulnerable-secret`.

These are efficiency-warning strata, not additional solve regressions.

## Updated smoke selection

The repair mix now exercises six outcome-regression classes:

- graph/cwd substrate: `prove-plus-comm`, `sanitize-git-repo`;
- deadline/context: `write-compressor`, `regex-chess`;
- environment/model-workload: `qemu-alpine-ssh`, `torch-tensor-parallelism`.

It retains three known GT-working cases with grounded graph/feature evidence:
`headless-terminal`, `portfolio-optimization`, and
`schemelike-metacircular-eval`, plus the resource-warning control
`feal-linear-cryptanalysis`, and the prior flip
`count-dataset-tokens`. The frozen baseline remains **9/10** on this exact
slice. The four unselected outcome regressions (`extract-elf`,
`mcmc-sampling-stan`, `video-processing`, `winning-avg-corewars`) remain a
reserve set for a follow-up diagnostic; they are not silently treated as
fixed.

## Interpretation

The current mix is a diagnostic balance, not a cherry-picked success set: six
of ten tasks are known outcome regressions, three are known successful GT
trajectories, and one is a prior flip. The paid smoke must report all ten
individually and cannot be promoted on aggregate tokens if any baseline solve
is lost or if calls/steps/actions increase on common solves.
