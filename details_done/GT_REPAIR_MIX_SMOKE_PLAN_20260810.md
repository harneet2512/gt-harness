# Repair-mix ten-task smoke plan — 2026-08-10

## Why the original ten was insufficient

The previous smoke slice was inherited from an older baseline witness. It
contained several healthy tasks, but it did not deliberately include both
graph-invalid regressions from the 89-task run (`prove-plus-comm` and
`sanitize-git-repo`) together with the independent deadline/context failures
(`write-compressor` and `regex-chess`). A green result on that slice could
therefore miss the exact failure classes that caused the 60/89 treatment.

## Selected mix

| Stratum | Tasks | Purpose |
|---|---|---|
| Known regression witnesses | `prove-plus-comm`, `sanitize-git-repo`, `write-compressor`, `regex-chess`, `qemu-alpine-ssh`, `torch-tensor-parallelism` | exercise graph/cwd substrate, graph applicability, deadline reserve, oversized/context behavior, and environment/model-workload paths |
| Known GT-working witnesses | `headless-terminal`, `portfolio-optimization`, `schemelike-metacircular-eval` | retain tasks with certified graph/frontier or grounded feature deliveries and successful GT-on outcomes |
| Prior GT-on flip | `count-dataset-tokens` | check that the repaired controller does not lose a previously positive GT-on outcome |

The frozen GT-off baseline solved 9/10 of this mix; only
`count-dataset-tokens` was baseline-unsolved. The mix therefore preserves a
clean outcome comparison while concentrating six of ten slots on known failure
classes. The four remaining full-run outcome regressions—`extract-elf`,
`mcmc-sampling-stan`, `video-processing`, and `winning-avg-corewars`—are
reserved for a follow-up diagnostic rather than silently considered fixed. It
intentionally does not use `gpt2-codegolf`, whose source-less
substrate is correctly excluded from the graph denominator and would add no
repository-intelligence coverage to this diagnostic smoke.

## Required run configuration

- workflow: `tb2_miniswe_central.yml` (or the equivalent engine workflow);
- ref: `inline-engine` at the exact pushed repair commit;
- arm: `certified_full`; feature: `integrated`;
- `integration_mode=active`, `preflight_mode=shadow`;
- temperature 1.0, model `deepseek-v4-flash`, timeout multiplier 1.0;
- parallelism 10 or less for the ten jobs; no 89-task dispatch;
- use the frozen local GT-off trajectories; do not rerun baseline.

The workflow defaults now enumerate this mix. A full 89-task run requires
clearing `include` explicitly and remains blocked by the outcome gate.

## Acceptance audit

Audit each task before aggregate deltas:

1. official reward and uncensored completion, with no new baseline solve loss;
2. graph applicability, index status, source revision, nodes/edges, and any
   parser diagnostic;
3. all 17 feature IDs and their applicability reason (fired, abstained,
   ambiguous, substrate failure, or missed trigger);
4. effect provenance: private state, engine actuation, candidate, represented,
   delivered, or genuinely withheld;
5. first-eligible timing, request hash, message indices, no prediction, no
   duplication, and grounded payload semantics;
6. provider/model calls, assistant steps, model actions, tokens, wall time,
   and controller-inclusive effective executions.

The smoke is a targeted diagnostic witness, not causal proof. Promotion still
requires outcome preservation and an outcome-first common-solved efficiency
gate; the 89-task benchmark must not start merely because this mix passes.
