# Agent treatment

## Sub-features

Hybrid retrieval, typed requirements, edit/inspection/public/integration roles,
ambiguity, semantic facts, bounded updates, delivery timing, and uptake.

## How to get to it (user POV)

Run `gt-harness run ... --treatment groundtruth` through a canonical suite adapter.

## Driving it with CLI

Provider-free: run the treatment/compiler/analysis tests in
`repository_intelligence_audit.yml`. Provider-backed: dispatch one canonical
suite workflow with an immutable ref and inspect all four per-task artifacts.

## Gotchas

A rendered string is not delivery proof. v4 treatment claims must reconcile to
v2 provider delivery receipts and the exact provider-visible trajectory.
