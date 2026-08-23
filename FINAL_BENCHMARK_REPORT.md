# Final Benchmark Report

Status: `COMPLETED_NOT_CERTIFIED`

Experimental release: `2140693bc038449cfdf02b49fb03e34eae50ac29`

Implementation under test: `d8286c15783ba090e1594bba69d0645d439a1b5c`

GitHub Actions: `32635379908`

Artifact: `tb2-gt-32635379908` (942,358 bytes compressed)

The historical experiment was one GT-only Terminal-Bench 2.0 smoke with `stealth/ox-alpha`, Mini-SWE-Agent through a now-retired compatibility wrapper, 20 frozen tasks, one attempt, temperature 1, timeout multiplier 1.0, and concurrency 10. It was not a Bare/GT/GitNexus causal benchmark and is not evidence about the current Mini-SWE-only runner.

## Outcome

| Metric | Observed |
| --- | ---: |
| Trials graded | 20/20 |
| Reward 1 | 8 |
| Reward 0 | 12 |
| Mean reward | 0.400 |
| Harbor exceptions | 7 `AgentTimeoutError` |
| GT run receipts bound | 20/20 |
| GT receipts `COMPLETED` | 6 |
| GT receipts `ERROR` | 7 |
| GT receipts left `RUNNING` by outer timeout | 7 |
| ACTIVE treatments | 7 |
| NOT_APPLICABLE treatments | 13 |
| Provider calls recorded | 404 |
| Input tokens recorded | 4,445,693 |
| Output tokens recorded | 258,533 |
| Cost | not emitted by Harbor/provider receipts |

The eight solved tasks were `portfolio-optimization`, `prove-plus-comm`, `headless-terminal`, `count-dataset-tokens`, `extract-elf`, `fix-code-vulnerability`, `mcmc-sampling-stan`, and `sanitize-git-repo`. Two (`headless-terminal` and `extract-elf`) passed grading despite Harbor timing out the agent.

## GT treatment delivery

GT made 12 context deliveries across the seven ACTIVE tasks, delivered 18 evidence items and 36,307 characters, and never exceeded the four-delivery cap. The final receipts contained no unverified delivered evidence. NOT_APPLICABLE tasks received zero GT context.

The inspected packets for FEAL, Bottle, Corewars, headless-terminal, largest-eigenval and the scheduler contained source facts rather than dummy text. The sanitize task correctly became NOT_APPLICABLE instead of receiving the irrelevant generic `modify`/`commit` facts seen in the first diagnostic.

## Failures exposed

1. Seven Harbor timeouts killed the process before final receipt publication. Checkpoints survived but remained `RUNNING`.
2. Interrupted checkpoint transcripts did not preserve the initial user message plus exact GT packet, so the complete delivered text is not reconstructible from `gt-run.json` alone.
3. Five trials ended with explicit `EmptyProviderResponseError` after bounded retries. They were no longer mislabeled as successful blank completions.
4. `video-processing` exhausted 100 iterations.
5. `qemu-alpine-ssh` exposed a Rich `MarkupError` from rendering `[/app/alpine-disk.qcow2]` as markup. This was fixed after the experiment in `b1020bd47929e740ce5e4532d4e151f510602afb`, with regression coverage, but has not had paid-run certification.

Run `32631659145` is diagnostic evidence only: Harbor completed 20 trials at mean 0.350 with five timeouts, but the old binder rejected Harbor 0.20's structured task identity and root-owned receipts prevented artifact upload. Those defects were fixed and provider-free run `32634873373` passed before the final run.

## Statistical conclusion

Sample size is 20 GT-only trials. There is no paired Bare or GitNexus arm, no repeated trials, and no cost receipt. Causal solve-rate uplift, negative flips, relative efficiency and statistical superiority are all `NOT_ESTABLISHED`. The 8/20 result must not be presented as improvement over baseline.
