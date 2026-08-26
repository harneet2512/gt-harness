# Runtime doctor

## Sub-features

Python, Git, Go, product dependencies, and the source-built `gt-index` runtime.

## How to get to it (user POV)

Run `gt-harness doctor`; use `--no-build` only for a read-only quick check.

## Driving it with CLI

Run `python -m gt_harness.cli doctor` and require structured JSON plus exit zero.

## Gotchas

A binary found on PATH is not sufficient release proof; its content identity
must match the checked-in Go source.
