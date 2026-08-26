# Graph lifecycle

## Sub-features

Repository identity, discovery, build, immutable publication, query, stale
detection, rebuild, persistence, checksum validation, and explicit limitations.

## How to get to it (user POV)

Use `gt-harness graph build|status|query --root <repo> --state-dir <state>`.

## Driving it with CLI

Run `python scripts/verify_gt_harness.py --output artifacts/verification/latest`.

## Gotchas

`>0` nodes is not readiness. Commit/source revision, manifest, graph checksum,
coverage, SQLite integrity, and CURRENT generation must all agree.
