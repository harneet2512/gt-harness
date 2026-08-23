# Canonical Harbor product path and cleanup ledger

Status: `IMPLEMENTED_NOT_DISPATCHED`

The canonical paid Mini-SWE product path is
`.github/workflows/tb2_miniswe_product.yml`. It is manual
`workflow_dispatch` only. Each of the frozen `repair20-v1` tasks gets one
Harbor trial, the matrix permits 20 tasks in parallel, and Harbor imports
`eval.harbor_gt_harness_adapter:GtHarnessMiniSwe228Agent`. That adapter installs
Mini-SWE-Agent 2.2.8 and invokes the released `gt-harness run` CLI. The workflow
uses the requested model `stealth/ox-alpha`; the adapter records that requested
identity and the effective LiteLLM route `openai/stealth/ox-alpha` without
writing the provider credential to either receipt.

The frozen task-set SHA-256 is
`36d5c8945f6f8d9ae23fe2cea759f16da0c0cea424a98f710cfaa0d9d6fd0303`.
Both the planning receipt and final attestation carry the ordered task list,
hash, attempt count, scaffold version, adapter identity, and requested/effective
model identity. The final attestation fails closed unless all 20 Harbor result
rows, all 20 adapter receipts, and all 20 `gt-harness run` receipts agree with
the frozen task set.

## Cleanup rule

Do not delete any path in this ledger as part of the canonical-path change.
Historical workflows and artifacts remain necessary evidence until a dispatched
run of the canonical workflow produces a `PASS` final attestation at the exact
release commit. Retirement also requires release authorities to point at that
attestation and a repository-wide dependency check in a separately reviewed
cleanup change.

| Path | Classification now | Retirement condition |
| --- | --- | --- |
| `.github/workflows/tb2_miniswe_product.yml` | Canonical replacement, not yet dispatched | Keep |
| `eval/harbor_gt_harness_adapter.py` | Canonical Harbor product adapter | Keep |
| `.github/workflows/tb2_miniswe_ox_alpha_diagnostic.yml` | Legacy central diagnostic; retirement candidate | Canonical repair20 attestation passes and release references migrate |
| `.github/workflows/tb2_miniswe_central.yml` | Legacy central evaluation; retirement candidate | All active release and mechanical gates migrate |
| `.github/workflows/tb2_miniswe_engine.yml` | Legacy engine experiment; archive candidate | No active workflow or receipt consumer remains |
| `.github/workflows/deepswe_miniswe_central.yml` | Separate DeepSWE experiment, not the canonical Harbor path | Retain unless that experiment is explicitly retired |
| `eval/gt_central_agent.py` | Shared legacy central implementation | Do not delete; multiple workflows and analysis tools still import it |
| `eval/miniswe_agent.py` | Shared historical installed-agent adapters | Do not delete while baseline/phase workflows import it |
| `eval/pier_gt_adapter.py` | Pier-specific experimental bridge | Do not delete while DeepSWE workflows import it |
| `scripts/miniswe_gt_run.py` | Historical runner and replay dependency | Do not delete while old receipts or tests require replay |
| `nano/` | Not tracked product source | No tracked Nano implementation exists; local ignored cache files are outside the release and the canonical workflow rejects Nano references |
| `artifacts/` | Historical benchmark evidence | Preserve until canonical artifacts are uploaded, hash-bound, and release authorities migrate |
| `artifact_deepswe/` | Historical DeepSWE evidence | Preserve independently of the Harbor replacement |

## Safe cleanup sequence after a live replacement exists

1. Verify `gt.harbor_repair20_attestation.v1` reports `PASS`, the exact task-set
   hash, 20 unique graded trials, 20 adapter receipts, and 20 product receipts.
2. Record the workflow run ID, source SHA, and uploaded artifact digests in the
   release authority.
3. Search every workflow, script, test, and document for each retirement
   candidate and reclassify any path that still has a live consumer.
4. Archive evidence before removing only unreferenced workflow entry points.
   Shared runtime code and historical artifacts require separate approval.
