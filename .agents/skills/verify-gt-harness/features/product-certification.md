# Product certification

## Sub-features

Exact wheel surface, clean install, graph/language/truth/lifecycle matrix,
localization truth, failure campaign, harness E2E, and receipt cross-binding.

## How to get to it (user POV)

Run the prerelease workflow or `gt-harness certify --receipt-dir <bundle>`.

## Driving it with CLI

In Codespaces run `bash scripts/codespaces_product_certification.sh <output>`;
then pass its bundle to `gt-harness certify` with the exact checkout SHA.

## Gotchas

Certification is provider-free and does not establish solve-rate uplift. A
missing receipt, stale SHA, partial language matrix, or skipped required gate is
`NOT_CERTIFIED`, never a warning-only pass.
