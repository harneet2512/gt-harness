# GT Harness production cleanup ledger

Status: `RETIRED_FROM_DISPATCH`

The release exposes exactly five GitHub Actions workflows: the TB2, DeepSWE,
and SWE-Live Lite Mini-SWE 2.4.6 product workflows, the complete prerelease
certification workflow, and the provider-free repository-intelligence audit.
Each suite workflow selects `bare` or `groundtruth` through the same adapter,
agent scaffold, task cohort, budgets, and verifier.

The historical `central`, `engine`, `diagnostic`, split Live Lite, standalone
baseline, V4Flash, and finalstand workflows were deleted from `.github/workflows`.
They remain recoverable from Git history but can no longer be accidentally
dispatched or mistaken for the current benchmark product.

## Current production surface

| Path | Disposition |
| --- | --- |
| `.github/workflows/tb2_miniswe_product.yml` | Canonical TB2 bare/GT workflow |
| `.github/workflows/deepswe_gt_harness_product.yml` | Canonical DeepSWE bare/GT workflow |
| `.github/workflows/swe_live_lite_gt_harness_product.yml` | Canonical Live Lite bare/GT workflow |
| `.github/workflows/prerelease_product_matrix.yml` | Complete provider-free product certification |
| `.github/workflows/repository_intelligence_audit.yml` | Focused graph/localization/delivery audit |
| `eval/harbor_gt_harness_adapter.py` | Canonical Harbor adapter |
| `eval/pier_gt_harness_adapter.py` | Canonical Pier adapter |
| `eval/swe_live_lite_gt_harness_adapter.py` | Canonical direct-container adapter |

## Historical implementation

`eval/gt_central_agent.py`, `eval/miniswe_agent.py`,
`eval/pier_gt_adapter.py`, `scripts/miniswe_gt_run.py`, the legacy `gt_engine`
modules, and `src/groundtruth` are retained only as source/history inputs while
the prerelease stabilizes. They are excluded from the wheel by the exact Hatch
allowlist in `pyproject.toml`; `production-surface.toml` and
`validate_product_surface` fail if a canonical module imports them or a wheel
contains them. None is a dispatchable treatment path.

Historical benchmark evidence under `artifacts/` and `artifact_deepswe/` is
not product code and is not packaged. It remains evidence for regression
analysis until the controlled replacement runs are complete.
