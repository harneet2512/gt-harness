# HAR-83 benchmark dispatch checklist

This is the local fail-closed order of operations. Do not dispatch a paid run
until every P0 row is green for one immutable harness commit. Do not infer
readiness from local tests, a vendored binary, or a manifest claim alone.

## P0 — immutable Groundtruth producer identity

1. The manifest's `groundtruth.source_commit` must be reachable from the exact
   `groundtruth.repository` used by GitHub Actions. Prove this with a fresh
   fetch/checkout, not a local object database or dirty worktree.
2. The fresh checkout's `HEAD^{tree}` must equal the manifest's
   `groundtruth.source_tree`.
3. The vendored Linux producer's literal SHA-256 must equal both
   `groundtruth.producer_sha256` and `producer_build.binary_sha256`.
4. The producer's `-build-info` output and tracked build-info file must name the
   same reachable source commit, source fingerprint, build tags, graph schema,
   capabilities, and executable SHA-256.
5. The exact source SHA must have clean Linux static-build and full tagged Go
   suite evidence. A build-only pass does not override a failed test run.
6. The pinned review-inbox commit must contain one live, digest-valid PASS
   packet for that exact source SHA. A packet copied only into the product
   manifest is invalid.

Any absent remote object, source/tree mismatch, stale review packet, dirty
checkout, or failed clean runner is a hard stop. Rebuild and rebind; never
weaken the verifier or bypass the fixture-first hook.

## P0 — immutable harness and provider-free acceptance

1. Commit and push the complete source closure. Preserve unrelated local
   artifacts outside the closure.
2. Run canonical provider-free product acceptance on that exact harness SHA.
3. Require a successful closeout receipt with `provider_calls=0`,
   `benchmark_runs=0`, correct source SHA, empty blockers, and release-eligible
   product identity.
4. Use that exact successful readiness run ID in the paid plan. Never substitute
   an older green run after the source, producer, review packet, route, or
   workflow changes.

## P0 — exact paid route

The only authorized HAR-83 paid route is:

- gateway: OpenRouter;
- model: `deepseek/deepseek-v4-flash-0731`;
- provider allow-list: `relace` only;
- `allow_fallbacks`: `false`;
- `require_parameters`: `true`.

`config/provider_route.v1.json` is the sole route authority. The preflight,
immutable run plan, Mini-SWE request body, provider-gate receipt, and final
attestation must all match it exactly. Source the OpenRouter credential only as
specified by `AGENTS.md`; never print or persist its value.

## P1 — one-task gate

Dispatch only `cohort_stage=gate-one`, with the exact green readiness run ID.
Verify the task's terminal state, conserved patch, Mini-SWE integrity, graph and
context receipts, provider admission/route, official verifier result, and final
attestation. Report DeepSeek results as absolute/exploratory; do not claim
causal uplift against the retained Muse GT-off cohort.

## P2 — remaining nineteen

Dispatch `cohort_stage=remaining-19` only after the one-task run completes and
its artifacts pass the gate. Bind `prior_gate_run_id` to that exact run. A
failed, timed-out, setup-failed, provider-failed, route-divergent, unverified, or
missing gate-one result stops the remaining cohort.

Never rerun GT-off. Never start the remaining nineteen while repairing a smoke.
