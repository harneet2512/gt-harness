# As-built product architecture

This record describes the canonical GT Harness product. It must change in the same commit as any
bundle, workflow, adapter, result, or acceptance-contract change.

## Lifecycle

1. `config/deepswe_product_bundle_v1.json` binds Mini-SWE-Agent 2.4.6, Python, uv, Harbor, Pier,
   the DeepSWE revision, task order, task-config hashes, image digests, Groundtruth identities,
   and the product source closure.
2. `gt_harness.product.build_product_bundle` hashes the actual bytes and emits
   `gt.product_bundle.v1`. Validation recomputes every digest and fails closed on mutation.
3. `eval.pier_gt_harness_adapter:PierGtHarnessMiniSwe246Agent` is the shipping Pier import. It
   inherits the same installation and run boundary used by both arms.
4. `scripts.miniswe_gt_run` is installed as a wheel module and runs Mini-SWE-Agent. The shell
   environment is credential-isolated. Groundtruth activation is selected without changing runner
   bytes or dependency identity.
5. Task results preserve process exit, stop reason, grader state, completeness, usage, evidence,
   and artifact digests. Aggregation represents every planned task exactly once and treats missing
   or invalid trials as failures.
6. `scripts/gt_product_acceptance.py` runs the deterministic provider-free fixture in both arms,
   proves parity and secret non-disclosure, and emits `gt.product_closeout.v1`.

The fixture currently proves the local installed-wheel and result contracts. It does not claim an
OpenAI-compatible transport or Harbor container execution; those remain explicit blockers.

## Authority

Groundtruth evidence is advisory. Candidate sets, flow witnesses, retrieval scores, communities,
framework overlays, and context packets cannot upgrade verification authority. Ambiguous,
incomplete, stale, malformed, cross-language, or tampered evidence abstains. Honest result
serialization preserves `complete`, `truncated`, `incomplete`, and `legacy_unknown` end to end.

## Active and historical surfaces

The only active workflow is `.github/workflows/deepswe_gt_harness_product.yml`. It is provider-free
and pinned. The local acceptance command uses the same manifest and product functions. Nano,
Terminal-Bench, SWE-bench, old Mini-SWE releases, and prior live workflows are historical or
internal surfaces and are not release evidence.

## Approval boundaries

Provider-free acceptance does not authorize a provider call. The one-task live smoke requires a
separate approval receipt binding exact source, bundle, task, provider, model, account reference,
command, duration, output location, estimated cost, and hard ceiling. The full DeepSWE benchmark
remains separately gated. No GCP action belongs to this lifecycle.

## Known release boundary

The provider-free core can be verified without Docker. A final release additionally requires the
clean Linux container install proof and an accepted-default Groundtruth rebuild whose source,
wheel, producer, capabilities, and receipt all agree. Until that proof exists, container status is
recorded as `NOT_EXECUTED`; it is never rounded up from local tests.
