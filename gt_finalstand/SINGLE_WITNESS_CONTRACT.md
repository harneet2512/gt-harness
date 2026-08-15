# FS-024 Single Matched Witness Contract

The project owner replaced the preregistered six-arm experiment with one
provider-bound GroundTruth run compared against a frozen GT-off trajectory
already present in local Downloads. The six-arm/60-run manifest was never
executed and is superseded; it is retained only as historical design evidence
and is not required for project closure.

## Selected pair

- Benchmark: `terminal-bench@2.0`
- Task: `fix-code-vulnerability`
- Dataset commit: `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`
- Task checksum: `13c4e35adbd7e55707f273aabd8f4108672f0fb790c96af543fbcbdcc977b119`
- Baseline run: GitHub Actions `30665246698`
- Baseline trial: `70179645-7ff8-4142-9354-a0613b6c04d0`
- Baseline harness: `eval.miniswe_agent:MiniSweAgent`, Mini-SWE `2.2.8`
- Candidate harness: `eval.miniswe_agent:MiniSweGtAgent`, Mini-SWE `2.2.8`
- Model: `deepseek-v4-flash`, resolved as `openai/deepseek-v4-flash`
- Required provider fingerprint: `fp_a18b46594c_prod0820_fp8_kvcache_20260402`
- Temperature: `1.0`; step limit: `100`; cost limit: `$3`; command timeout:
  `30` seconds; agent timeout multiplier: `1.0`; attempts: `1`; concurrency: `1`

The treatment is GroundTruth advisory activation. The workflow must fail before
the benchmark trial if the model fingerprint differs. After execution, the
candidate result must match the task checksum, task-prompt hash, dataset commit,
Mini-SWE version, model identity, temperature, budgets, and trajectory format
recorded in `receipts/fs024_single_witness_baseline.json`.

The system-prompt and task-prompt hashes are controlled-equality fields and are
identical across the recorded pair. GroundTruth did not alter the system
prompt; the treatment arrived through deterministic advisory observations at
the runtime delivery boundary.

## Completion rule

FS-024 completes when exactly one authorized candidate trial:

1. runs in GitHub Actions rather than on the local workstation;
2. produces an independently verified reward;
3. produces a GT delivery/receipt trail and a complete Mini-SWE trajectory;
4. matches every frozen baseline identity except treatment, provider request
   IDs, timestamps, and other execution-specific identities;
5. is analyzed by `scripts/phase2_single_witness.py`; and
6. freezes the workflow run, artifact, input hashes, and descriptive deltas.

The result is a one-task engineering witness. It can demonstrate execution,
non-regression on that task, and concrete exploration/token deltas. It cannot
estimate population solve-rate impact, support confidence intervals, or justify
a general causal claim.

## Recorded result

The owner-approved witness is complete. The independently verified reward was
`1.0` for both the frozen GT-off baseline and GT advisory candidate. The exact
descriptive measurements are:

- Candidate GitHub Actions run: `30731388242`, attempt 1, job `91452315208`
- Harness commit: `cdefd9a52c915364d346b790a65dde3104c17286`
- Candidate artifact: `8828119172`, API SHA-256
  `bbd7b620bfd9285c8a88a12714ce1331586052286fe2d96f9efcd36e2d6d12b5`
- Candidate trial: `4caf8b43-3a4a-4387-8a14-29528cb67e79`
- Frozen baseline trial: `70179645-7ff8-4142-9354-a0613b6c04d0`

| Measure | GT-off baseline | GT advisory | Delta (candidate − baseline) |
|---|---:|---:|---:|
| Reward | 1.0 | 1.0 | 0.0 |
| Provider calls | 33 | 25 | -8 |
| Total actions | 33 | 37 | +4 |
| Exploration actions before first edit | 19 | 25 | +6 |
| Raw bytes before first edit | 34,696 | 43,009 | +8,313 |

Both arms record the same system-prompt SHA-256
`1c5d1622923d128240923e4695f21917b90c5058c1f48796de3ec2e60685f216`
and task-prompt SHA-256
`9a682c518b4cfac416a70095ab8f33f983ccd4ee670cfcb96d7d230d70eae49a`.
The GroundTruth treatment was carried only by compiled advisory observations.

The candidate therefore supplies a reward-tied non-regression witness for this
single task and used fewer provider calls. It did not reduce total actions,
pre-edit exploration, or raw pre-edit bytes. No general GT efficacy,
solve-rate, non-inferiority, token-efficiency, or exploration-reduction claim
is supported.

The provider trial and verifier passed. The Actions workflow's overall
conclusion was failure only because its offline postprocessor initially rejected
Harbor's per-trial `result.json` shape. The analyzer was fixed locally and run
against the downloaded immutable artifact; no second provider trial ran.

Two limitations remain inside the bounded attestation. The archived baseline
does not contain a resolved container-image digest. Also,
`miniswe_report.json` records `delivered_evidence=0`, while the GT event journal
contains one localization `evidence_delivery` row and the trajectory contains
multiple `GT_EXECUTION_EVIDENCE` observation augmentations. That instrumentation
discrepancy is unresolved, so the witness establishes observed advisory
delivery but not reliable aggregate delivery-count instrumentation.

## Promotion and project closure

FS-025 is complete with decision `KEEP`: stock Mini-SWE remains the default and
GroundTruth remains explicit opt-in behind its existing activation, fail-open,
kill-switch, and rollback boundaries. The reward tie permits retaining the
option; the increased actions, exploration, and raw bytes plus the absence of
inferential power reject default promotion.

FS-026 is complete as a bounded one-task descriptive attestation. Closure means
the requested engineering project ended with an honest conservative decision;
it does not mean that one stochastic observation became a benchmark-wide
estimate.
