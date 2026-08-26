# Final Benchmark Report

Status: `DEEPSWE20_DIAGNOSTIC_REJECTED_REPLACEMENT_PENDING_CERTIFICATION`

Current implementation under audit: branch `final/gt-harness-benchmark-update`;
the exact replacement SHA must be bound by the next hosted certification.

## 2026-08-26 DeepSWE diagnostic — rejected

Run [32913521485](https://github.com/harneet2512/gt-harness/actions/runs/32913521485)
at `ca068e11a8ea000df38c5e776ef1c472a0372636` completed all 20 task jobs but is
not an official GT-on benchmark result. Twelve tasks failed GT treatment setup
before the first provider call with `FAILED:context_budget_too_small`. The old
reward binder also represented valid Pier aggregate rewards as verifier
`ERROR`. These are GT Harness defects, not model losses, and invalidate the run.

The canonical aggregate rewards for the eight tasks that actually crossed the
provider boundary are four solves and four failures. Against the local Ox Alpha
Mini-SWE-Agent 2.4.6 baseline for those identical task IDs, the paired result is
four both-solve, three both-fail, one baseline-only (`anko-default-function-arguments`),
and zero GT-only.

| Metric, eight executed tasks | Local GT-off baseline | Rejected GT-on run | Difference |
| --- | ---: | ---: | ---: |
| Solved | 5/8 | 4/8 | -1 |
| Provider calls | 912 | 804 | -108 (-11.8%) |
| Input tokens | 77,378,708 | 70,869,624 | -6,509,084 (-8.4%) |
| Cached input tokens | 74,527,040 | 69,764,992 | -4,762,048 (-6.4%) |
| Output tokens | 500,450 | 528,767 | +28,317 (+5.7%) |
| Input + output tokens | 77,879,158 | 71,398,391 | -6,480,767 (-8.3%) |

These subset metrics show lower calls and total tokens but no solve uplift. They
do not compensate for twelve treatment failures and are not a release result.
The GT wrapper also reserves 90 seconds of the task-owned 5,400-second Harbor
ceiling for durable finalization, so this historical subset is not a strict
causal experiment even though task IDs, Mini-SWE 2.4.6, and Ox Alpha match.

The final task, `oxvg-structural-selector-preservation`, was a both-fail. GT's
initial packet supplied real repository evidence in
`crates/oxvg_ast/src/style.rs`, one of three reference-patch production files.
It omitted `collapse_groups.rs` and `remove_empty_containers.rs`. The agent later
found and edited `collapse_groups.rs` but not `remove_empty_containers.rs`; the
official verifier passed 62/62 pass-to-pass tests and only 4/6 fail-to-pass
tests. This is incomplete localization, not dummy context.

The replacement candidate fixes both invalidators and adds regression evidence:

- the reward standardizer reads Pier's canonical
  `stats.evals.*.metrics[].reward` representation and rejects conflicts;
- provider context drops weak semantic noise before decision-grade facts,
  rather than aborting when every available role cannot fit simultaneously;
- long process/impact rows are bounded while exact target, scoped public and
  integration boundaries, one certified relation, and affected tests remain;
- the real itsdangerous Mini-SWE-Agent 2.4.6 lifecycle passes at 468 initial
  tokens and 435 update tokens; and
- the campaign derives the installed Mini-SWE version and rejects anything
  other than 2.4.6 instead of hardcoding the claimed version.

No replacement paid run is launched until this exact candidate passes hosted
provider-free certification. The replacement run is acceptable only with all
20 treatment receipts present and no pre-provider context-budget failures.

## Canonical final official run

The official GitHub Actions/Harbor run [32717496816](https://github.com/harneet2512/gt-harness/actions/runs/32717496816)
ran the immutable `repair20-v1` task set with Mini-SWE-Agent 2.2.8,
`stealth/ox-alpha` through OpenRouter, one attempt per task, and max parallelism
20. Attestation was `PASS`, all 20 task jobs completed, and all 20 product
receipts and trajectories were present. The source SHA was verified in the
attestation. The machine-readable receipt is
`audit/receipts/smoke-32717496816-summary.json`.

| Metric | Frozen baseline | Final GT run | Difference |
| --- | ---: | ---: | ---: |
| Solved | 17/20 | 13/20 | -4 tasks |
| Provider calls | 1,041 | 627 | -414 (-39.8%) |
| Input + output tokens | 65,625,578 | 22,333,393 | -43,292,185 (-66.0%) |
| GT product error receipts | n/a | 2/20 | timeout, product_process_exit_124 |
| Attestation | n/a | PASS | official |

The bounded retry repair removed the earlier provider read-timeout failures.
The dense-update repair prevented the prior `fix-code-vulnerability` treatment
unavailability. The final run still exposed two product-facing weaknesses:
`regex-chess` ended in a Harbor `Timeout`, and `write-compressor` ended with
`ProductProcessExitError` / `SUPERVISOR:product_process_exit_124`. These are
explicit receipts, not silent success. The final run also shows that the current
initial-packet policy still permits some weak, inspection-only context packets.

The baseline uses `deepseek-v4-flash`, while GT uses `stealth/ox-alpha`; the
solve-rate delta is therefore descriptive, not a same-model causal result.

### Paired task result

GT-only solved: `count-dataset-tokens` (1). Baseline-only solved:
`extract-elf`, `regex-chess`, `video-processing`, `winning-avg-corewars`, and
`write-compressor` (5). Both failed: `largest-eigenval` and
`torch-pipeline-parallelism` (2). The remaining 12 tasks were solved by both.

No fabricated repository text was found. Active packets were sourced from the
task files/graph and carried revision/checksum identities. Six source-less tasks
explicitly abstained with `NOT_APPLICABLE:graph_not_ready:FAILED`; they were not
represented as healthy graphs.

## Historical authoritative corrected run (superseded by 32700056236)

The official GitHub Actions/Harbor run [32695000605](https://github.com/harneet2512/gt-harness/actions/runs/32695000605)
ran the exact frozen `repair20-v1` task set with Mini-SWE-Agent 2.2.8,
`stealth/ox-alpha` through OpenRouter, one attempt per task, and max parallelism
20. The final attestation was `PASS`; all 20 Harbor task jobs, GT receipts, full
trajectories, adapter receipts, and source-SHA bindings were present.

| Metric | Frozen baseline | GT corrected run | Difference |
| --- | ---: | ---: | ---: |
| Solved | 17/20 | 9/20 | -8 tasks |
| Provider calls | 1,041 | 651 | -390 (-37.5%) |
| Input + output tokens | 65,625,578 | 30,111,583 | -35,513,995 (-54.1%) |
| GT error receipts | n/a | 2/20 | headless, tensor |
| Attestation | n/a | PASS | official |

The baseline uses `deepseek-v4-flash`, while the GT run uses `stealth/ox-alpha`;
therefore the solve-rate delta is directional and cannot be attributed causally
to GT. The call/token reduction is an observed harness-efficiency difference,
not proof of equal-quality or lower-cost agent performance.

### Per-task authoritative comparison

`B` is the frozen baseline reward and `G` is the corrected GT reward.

| Task | B | G | GT status | Calls | GT deliveries | Treatment | Error |
| --- | ---: | ---: | --- | ---: | ---: | --- | --- |
| cobol-modernization | 1 | 0 | COMPLETED | 21 | 1 | ACTIVE | - |
| count-dataset-tokens | 0 | 1 | COMPLETED | 10 | 0 | NOT_APPLICABLE | - |
| extract-elf | 1 | 1 | COMPLETED | 20 | 0 | ACTIVE | - |
| feal-linear-cryptanalysis | 1 | 1 | COMPLETED | 23 | 3 | ACTIVE | - |
| fix-code-vulnerability | 1 | 1 | COMPLETED | 25 | 3 | ACTIVE | - |
| headless-terminal | 1 | 1 | ERROR | 17 | 3 | ACTIVE | Timeout |
| largest-eigenval | 0 | 0 | COMPLETED | 11 | 1 | ACTIVE | - |
| llm-inference-batching-scheduler | 1 | 1 | COMPLETED | 36 | 3 | ACTIVE | - |
| mcmc-sampling-stan | 1 | 0 | COMPLETED | 43 | 0 | NOT_APPLICABLE | - |
| portfolio-optimization | 1 | 1 | COMPLETED | 25 | 3 | ACTIVE | - |
| prove-plus-comm | 1 | 1 | COMPLETED | 6 | 0 | ACTIVE | - |
| qemu-alpine-ssh | 1 | 0 | COMPLETED | 40 | 0 | NOT_APPLICABLE | - |
| regex-chess | 1 | 0 | COMPLETED | 88 | 3 | ACTIVE | - |
| sanitize-git-repo | 1 | 1 | COMPLETED | 25 | 3 | ACTIVE | - |
| schemelike-metacircular-eval | 1 | 0 | COMPLETED | 14 | 3 | ACTIVE | - |
| torch-pipeline-parallelism | 0 | 0 | COMPLETED | 22 | 0 | NOT_APPLICABLE | - |
| torch-tensor-parallelism | 1 | 0 | ERROR | 30 | 0 | NOT_APPLICABLE | Timeout |
| video-processing | 1 | 0 | COMPLETED | 100 | 0 | NOT_APPLICABLE | - |
| winning-avg-corewars | 1 | 0 | COMPLETED | 88 | 3 | ACTIVE | - |
| write-compressor | 1 | 0 | COMPLETED | 7 | 0 | ACTIVE | - |

Flips: one GT-only solve, nine baseline-only solves, eight both solve, and two
both fail. This is a measurable regression in this cross-model smoke, not a
claim about GT causality.

All sections below are historical run notes retained for audit traceability; the
authoritative result above supersedes their older run IDs and SHA references.

No claim of solve-rate superiority is certified. Two authorized GT-only repair20
smokes found real product defects. The second run showed that the graph and dense
context path now works across the portfolio, but two tasks still ended without a
terminal product receipt. Those causes are fixed and provider-free tested at the
current implementation SHA; they have not been verified by a third paid run.

## Live smoke receipts

| Run | Subject SHA | Raw reward | Valid terminal solves | Product receipts | Verdict |
| --- | --- | ---: | ---: | --- | --- |
| [32676409425](https://github.com/harneet2512/gt-harness/actions/runs/32676409425) | `49cce4dee7ef4c57011b522c8c00404cdf480bd9` | 7/20 | 7/20 | 4 completed, 11 error, 4 running, 1 absent | FAIL |
| [32680131105](https://github.com/harneet2512/gt-harness/actions/runs/32680131105) | `b6e16099a827449953670b053d9ac873d4f6f367` | 12/20 | 11/20 | 18 completed, 2 running | FAIL |

The first run exposed stale-after-edit aborts, a glibc-only Go binary on QEMU
Alpine, weak repeated context updates, timeout/receipt loss, and ordinary symbol
matches represented too strongly. Commit series `4730ed7..b6e1609` fixed those
causes and made final attestation fail closed.

The second run removed every stale-after-edit abort, reached QEMU with a static
indexer, and produced all 20 receipts and trajectories. Its final attestation
correctly failed. Two of four reported errors were an attestation counting bug:
`portfolio-optimization` and `sanitize-git-repo` each contained a provider format
error counted by Mini-SWE's `trajectory.info.model_stats.api_calls`, but no
assistant transcript row. The other two were genuine:

- `schemelike-metacircular-eval` earned reward 1, but the product was killed with
  exit 137 after spawning long-running tests. The durable receipt remained
  `RUNNING` and its checkpoint call count was one ahead of the saved trajectory.
  It is excluded from valid terminal solves.
- `winning-avg-corewars` reached Harbor's native task ceiling while an operation
  was still in flight. Its receipt remained `RUNNING`.

## Second-run resource and delivery observations

- trajectory-backed provider calls: `691`;
- assistant messages: `689` (the difference is the two recorded format errors);
- input/output/cached tokens: `32,841,716 / 712,920 / 31,488,320`;
- uncached input: `1,353,396`; total input+output: `33,554,636`;
- GT evidence deliveries: `41`;
- inspection-only updates suppressed before provider delivery: `31`;
- treatment status: 14 `ACTIVE`, 6 explicit `NOT_APPLICABLE`;
- graph state for active treatments: `READY` or
  `READY_WITH_DECLARED_LIMITATIONS`; no stale graph was delivered.

## Was GT sending real context or dummy context?

All 14 active initial packets and all delivered update classes were inspected.
The excerpts, paths, relationships, value-flow facts, process paths, revision
identities, and limitations came from the task repository or persisted graph.
No placeholder, template-only, fabricated source text, or provider-authored graph
fact was found.

The packets were not all epistemically correct. Earlier runs had seven
`exact_task_path` claims
across five tasks attached a representative symbol/range to a path that the task
named without naming that symbol. The file was real and relevant; the symbol-level
authority was not justified. The clearest case was `fix-code-vulnerability`, which
rendered `bottle.py:1056#wsgi` even though the named fact was only `bottle.py` and
the vulnerability was elsewhere. The historical path-only-anchor correction from
SHA `8931876` emits those anchors as
`file_identity` at line 1 with no symbol or excerpt. Exact quoted/symbol anchors
retain symbol authority.

## Historical fixes after the second run

Historical correction series ending at `8931876`:

1. uses Mini-SWE 2.2.8 only;
2. preserves Harbor's task-owned `task.toml` ceiling and reserves a 90-second
   finalization gap;
3. shrinks every provider timeout to at most 60 seconds or actual remaining GT
   time, with one attempt rather than ten hidden retries;
4. bounds each shell action to at most 30 seconds or remaining GT time;
5. externally and atomically converts only a durable `RUNNING` checkpoint to
   `ERROR` on product exit or Harbor cancellation, preserving the trajectory and
   reconciling usage from `trajectory.info.model_stats`;
6. validates provider calls against trajectory stats rather than assistant-row
   count; and
7. removes unjustified symbol authority from exact file-path anchors.

The corrected canonical workflow was registered on `main` by commit
`2693e9ab6ea53408d7f7a10acdc304717754616a` with workflow blob
`7baa36fd4c31a9fbf347e49b003cced298184d01`. Registration did not dispatch a
third paid run.

The real Scheme artifact was replayed provider-free through the new supervisor:
it became terminal `ERROR`, preserved its transcript, and reconciled to the
trajectory's 57 provider calls. The complete Python suite, targeted lint, and all
Go packages pass locally. Exact-SHA Codespaces certification is recorded separately.

The later source series adds two further controls. `1fd7efae` restores three
bounded Mini-SWE transport retries for transient provider read timeouts.
`c0b296f` adds the dense-update fallback and narrows initial abstention. The
official run above verifies both controls, while also showing that weak
inspection-only packets can still be delivered in some cases.

## Directional baseline only

The frozen local repair20 baseline used the same Mini-SWE-Agent 2.2.8 task set but
`deepseek-v4-flash`, not Ox Alpha. It solved 17/20 with 1,041 calls and 65,625,578
input+output tokens. The final GT run solved 13/20 with 627 calls and 22,333,393
tokens. This remains a descriptive cross-model comparison, not a causal GT-on/
GT-off result.

## Conclusion

The current implementation fixes the demonstrated Harness defects, but a live
repair20 run has not yet certified those fixes end to end. Broad paid comparison,
GitNexus superiority, and causal solve-rate claims remain unauthorized.
