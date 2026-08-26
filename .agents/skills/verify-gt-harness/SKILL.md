---
name: verify-gt-harness
description: Verify the GT Harness CLI, repository graph lifecycle, deterministic context delivery, official outcome binding, and paired benchmark analysis before release or benchmark dispatch.
---

# Verify GT Harness

Use this skill after changing graph construction, retrieval, context compilation,
Mini-SWE delivery, benchmark adapters, receipts, comparison, or certification.
The primary user surface is the `gt-harness` CLI. GitHub Actions are transport;
they do not replace the CLI proof.

## Launch

GT Harness is a short-lived CLI, so there is no shared server. From the repository root:

```bash
python -m pip install -e '.[dev,retrieval]'
python -m gt_harness.cli --version
```

Each verification drive creates an isolated temporary Git repository and state
directory. No process remains after the command exits.

## Doctor

Run this read-only check first:

```bash
python -m gt_harness.cli doctor --no-build
```

Continue only when the JSON reports the expected Python/Git/Go/product runtime.
For release proof, run `doctor` without `--no-build` so the source-bound Go
indexer is compiled and checked.

## Drive

Drive the graph lifecycle through the public CLI and retain evidence:

```bash
python scripts/verify_gt_harness.py --output artifacts/verification/latest
```

Then run the canonical production suite:

```bash
python -m pytest
ruff check $(python -c 'import tomllib; from pathlib import Path; s=tomllib.loads(Path("production-surface.toml").read_text()); print(" ".join(x.replace(".", "/")+".py" for x in s["python_modules"]))')
```

On Linux/Codespaces, the complete campaign is:

```bash
bash scripts/codespaces_product_certification.sh /tmp/gt-product-certification
```

## Evidence

`scripts/verify_gt_harness.py` preserves action/result pairs under
`artifacts/verification/latest/`: doctor output, cold graph receipt, definition
query, stale-status refusal, rebuilt generation, stderr logs, and a
content-addressed `verification-summary.json`. The proof must show a real Git
repository, a query result grounded in its source, explicit STALE after an edit,
a different immutable generation after rebuild, zero provider calls, and cleanup
of scratch state.

Provider-backed benchmark proof is valid only when each task also preserves
`benchmark-adapter.json`, `gt-run.json`, `gt-run.trajectory.json`, and the
official verifier result. Never substitute an internal test reward for the
suite verifier.

## Cleanup

The helper owns and removes its temporary repository and graph state. It never
kills processes by name. Do not delete `artifacts/verification/latest`; that is
the retained proof. If a failed run strands a directory, remove only the exact
path printed by Python's `gt-harness-verification-*` temporary-directory error.

## Helpers

`python scripts/verify_gt_harness.py --output <evidence-dir>` is the executable
helper. It exits nonzero at the first false product claim and emits no provider
request.
