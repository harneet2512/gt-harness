# Phase II Experiment Execution Contract

This contract is consumed by `scripts/phase2_experiment.py`. It does not authorize provider spend.

## Provider-free execution planning

The canonical ten-task smoke matrix is compiled without provider access by:

```text
python scripts/phase2_experiment.py plan \
  --manifest gt_finalstand/phase2_experiment_manifest.json \
  --task-manifest config/tb2_deepseek_smoke10.json \
  --inspect \
  --out gt_finalstand/receipts/experiment_execution_plan.json
```

The output contains all 60 task-arm cells, stable matched-pair and trial IDs,
manifest hashes, and explicit readiness blockers. It always records
`executed=false`, `provider_calls=0`, a null authorization receipt, and a null
provider-receipt root. The planner cannot authorize spend or manufacture an
execution receipt. The checked-in template therefore remains blocked until
concrete frozen identities and all six executable arm bindings exist.

Without `--inspect`, any blocker returns exit status 2. Inspection mode returns
zero only for a structurally valid provider-free plan; validation or binding
errors still return nonzero. The provider-free workflow uses inspection mode
because its purpose is to preserve the current blocked readiness state, not to
claim authorization or execution readiness. Finalstand validation recomputes
the expected 60-cell inspection plan and rejects any semantic drift.

Arm bindings use the closed `gt.phase2.arm_binding.v1` schema: `runner`,
`runner_sha256`, `agent`, `mode`, and `provider_calls_per_iteration`, plus the
schema discriminator. Additional fields, including environment, token, key, or
credential payloads, are rejected and never copied into the plan. The runner
must be a repository-relative Python file with the declared hash and a static
`PHASE2_SUPPORTED_MODES` declaration containing the arm's exact mode.

## Authorization receipt

Analysis requires a JSON document with:

- `schema`: `gt.phase2.execution_receipt.v1`
- `authorized`: literal `true`, recorded only after explicit user authorization
- `provider_receipt_root_sha256`: root hash binding every provider request/response receipt
- `manifest_sha256`: exact authorized experiment manifest hash
- `task_count`: number of manifest-identical matched task identities

The analyzer rejects a missing or malformed authorization receipt.

## Result rows

The CSV must contain exactly one row per matched identity and arm. Every matched identity must contain all six canonical arms. Required columns are:

| Column | Contract |
|---|---|
| `task_id` | Frozen benchmark task identity |
| `matched_pair_id` | Identity shared by all six manifest-identical arms |
| `arm` | One canonical arm from the experiment manifest |
| `solved` | Independently verified `0` or `1` |
| `exploration_actions` | Nonnegative action count under the preregistered classifier |
| `raw_bytes_consumed` | Nonnegative model-visible raw repository bytes |
| `false_interventions` | Nonnegative independently adjudicated count |
| `stale_incomplete_incidents` | Nonnegative independently adjudicated count |
| `verified_by` | Nonempty independent verifier identity |

Duplicate task-arm rows, incomplete six-arm identities, invalid endpoints, or task-count disagreement with the execution receipt cause hard failure. Unmatched historical runs are not accepted into confirmatory analysis.

## Analysis

The analyzer computes paired solve-rate deltas, exact paired McNemar tests, paired exploration deltas, and deterministic seeded paired-bootstrap intervals. The analysis output binds the execution receipt by SHA-256.

## Promotion boundary

`scripts/phase2_promotion.py` never edits runtime configuration. It may emit an eligible-arm decision only when the paid analysis, provider-free suite, Go source/binary receipt, and executed rollback receipt all validate. Solve-rate non-inferiority requires a lower paired confidence bound at or above zero; exploration reduction requires an upper paired confidence bound below zero.
