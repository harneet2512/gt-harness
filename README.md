# GT Harness

GT Harness is a reproducible benchmark product, not a general-purpose local agent CLI. Its
shipping path is:

`GitHub workflow → pinned DeepSWE tasks and images → Pier/Harbor → Mini-SWE-Agent 2.4.6 → GT Harness → Groundtruth evidence → isolated task execution → typed results and closeout`

The `nano` command and historical Terminal-Bench, SWE-bench, Mini-SWE 2.2.8, and Mini-SWE 2.3.0
paths remain available in Git history for development and artifact interpretation. They are not
supported release surfaces and are not product-acceptance evidence.

## Supported surface

- Scaffold: `mini-swe-agent==2.4.6` only.
- Benchmark snapshot: DeepSWE commit
  `435ee89ec2f2e2289f33b0da4f992f0b7b7266b9`.
- Task set: the ordered 20-task, five-language cohort in
  `config/deepswe_product_bundle_v1.json`.
- Task runtime: Pier `0.3.1` and Harbor `0.20.0`.
- Arms: `bare` and `groundtruth`. Their bundle, runner, model route, task images, budgets,
  package set, and task order are structurally identical. Only activation and evidence delivery
  may differ.
- Operator surfaces: one provider-free command, one provider-free acceptance workflow, and one
  separately approval-gated paid smoke workflow.

No supported command performs a live benchmark. A live one-task smoke and any full benchmark
require separate approval receipts. GCP is not part of installation, acceptance, or scoring.

## Provider-free acceptance

Run from a clean checkout with Python 3.12:

```text
python scripts/gt_product_acceptance.py --manifest config/deepswe_product_bundle_v1.json --fake-provider --output artifacts/product-closeout
```

The command constructs and validates `gt.product_bundle.v1`, executes a frozen disposable coding
fixture through both arms, forces a failed test and recovery, seals typed task results and
summaries, checks structural parity, scans every output for credential canaries, and emits
`artifacts/product-closeout/product-closeout.json`.

The current fixture is provider-disabled; it does not yet exercise an OpenAI-compatible fake
transport or the Harbor container boundary. Both omissions are emitted as release blockers rather
than being inferred from the local fixture.

It makes zero provider calls, zero benchmark requests, and incurs zero provider cost. Its closeout
records container proof as not executed unless a separate container acceptance layer actually ran;
the receipt does not manufacture that claim.

## Public artifact contracts

The canonical implementation is `gt_harness/product.py` and uses one UTF-8 canonical JSON
encoding. It defines:

- `gt.product_bundle.v1`: Git identities, source closure, exact local artifact bytes, dependency
  and tool versions, Groundtruth identities, dataset revision, ordered tasks, container digests,
  capabilities, and a bundle digest.
- `gt.install_attestation.v1`: reserved for the exact installed environment proof. A source-run or
  missing container cannot issue it as verified.
- `gt.benchmark_plan.v1`: immutable task identity, arm, zero-call ceiling for fake-provider runs,
  cost estimate, expected artifacts, and parity identity.
- `gt.benchmark_task_result.v1`: exit, stop reason, completeness, grader outcome, usage, evidence,
  and artifact digests.
- `gt.benchmark_summary.v1`: every planned task exactly once; missing, malformed, timed-out, OOM,
  or failed tasks count as failures.
- `gt.product_closeout.v1`: bundle, both arms, parity, secret scan, live-smoke status, full-benchmark
  gate, and container-proof status.

Every task result and summary carries `gt.honesty_envelope.v1`. A known complete result has a known
and conserved `true_total`; unknowable legacy data remains `legacy_unknown`.

## Security and failure rules

`scripts/miniswe_gt_run.py::CredentialIsolatedLocalEnvironment` removes provider, GitHub, GCP,
cloud, token, key, password, and credential variables from model-executed shell commands and
template variables. Provider credentials remain confined to the model client. The adapter projects
only the closed safe GT configuration allowlist defined by `project_task_environment`.

Installation fails closed on an unsupported Mini-SWE version or a changed uv installer. The
versioned uv installer is downloaded before task execution and must match the manifest SHA-256
before it is executed. Runtime outcomes preserve nonzero exits and explicit stop reasons; missing
results never silently become baseline successes.

## Workflow

The active workflow surface is closed to two files. The canonical
`.github/workflows/deepswe_gt_harness_product.yml` acceptance workflow is provider-free: it
contains no secret reference or provider route, pins every action by commit SHA, validates its own
module and path reachability, runs both provider-free arms, asserts zero provider and benchmark
calls, and uploads the closeout evidence. The separate
`.github/workflows/deepswe_gt_harness_product_p0731.yaml` paid smoke wrapper is disabled unless its
explicit approval and readiness gates pass. Historical workflows were removed from the active
directory and remain recoverable from Git history.

## Groundtruth boundary

Groundtruth owns the graph producer and framework overlays. GT Harness consumes only typed,
revision-bound evidence and never lets retrieval, ranking, ambiguity, or community membership
upgrade authority. `why_this_edge`, candidate conservation, five-language framework resolution,
calibration, eligibility, Leiden refinement, index reuse, and honesty semantics retain their
provider-free regression suites under `tests/`.

The bundle records both the claimed Groundtruth source revision and the exact bytes currently in
the repository. A final release still requires a clean accepted-default Groundtruth rebuild whose
wheel and Linux producer provenance agree with those bytes; a stale provenance receipt is not a
substitute.

## Development checks

```text
python -m pytest -q -ra
python -m pytest -n auto -q -ra
python -m ruff check .
python -m scripts.validate_product_workflow --workflow .github/workflows/deepswe_gt_harness_product.yml
```

The `nano` package is retained as an internal compatibility surface. Changes to it do not establish
product acceptance unless the canonical bundle, adapter, installed runtime, both arms, results,
and closeout are exercised.
