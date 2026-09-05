# review_me_gt result

Date: 2026-09-05. Scope: provider-free engine/product review before the
authorized one-task GT-on smoke.

Verdict: **PRODUCER_ENGINE_REPAIR_ACCEPTED**; exact-harness
**PROVIDER_FREE_READINESS_PENDING**.
The paid route is now explicitly authorized as OpenRouter
`deepseek/deepseek-v4-flash-0731` through Relace only, with provider fallback
disabled. This verdict does not claim solve-rate improvement, and the new
cross-model cohort is absolute/exploratory rather than a causal comparison to
the retained Muse GT-off baseline.

## Product identities reviewed

- Harness base: `9010199412dd1cb4fb5cd60e9ebd63000cc2132f`; the
  final canonical harness SHA is pending this review's rebinding commit.
- Producer source: remote-reachable commit
  `84e19be7011fd3b94d8e28616402898e73849bc0`, tree
  `ae7954c586a000e4f79439191256448840bab26b`.
- Linux producer SHA-256:
  `86975d2463eb85c7bbae284da0a18393d4412b6b00ed0ab9e0681b9c1d922827`.
- Build-info SHA-256:
  `1a1a3aa578952f14d170629c745ac12e22592f317dff8a56c10838da814b93b7`;
  source fingerprint `05ac96f41fd09a4b803f83ab003fc28515cf39dd8a109a9fbddcb5e515ee10b3`
  and build ID `c1c73f30dec4cab222a6072296e0ac33d9a4633c5d3f479201a4f9e28c0c3c03`
  independently recompute.
- Producer build: digest-pinned Go 1.22.5 Bookworm image, static musl,
  `netgo,osusergo,sqlite_fts5`; embedded build identity is complete.
- Mini-SWE: pinned 2.4.6. Installed Python: 3.12.13 image digest
  `sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2`.
- LSP remains explicitly unclaimed/non-shipping; no unbound scheduler is run.
- Review inbox commit: `c86d7d515f9178ef138dbaeb46ce53440d13a727`;
  exact PASS packet `har83-benchmark-producer-84e19be70`, digest
  `cb24a092ed17c9fd4fd3f4743e51b86ea0b862189a589a3d64b8f261542ddd2e`.

## Biting mutations

All mutations were applied only in a disposable clean review clone and then
reversed. Expected RED results:

| Mutation | Production seam | Regression witness | Result |
|---|---|---|---|
| Remove freshness guard | `context_packet.build_context_packet` | `test_tamper_and_stale_revision_are_rejected` | RED |
| Drop substantive payload | `context_packet._normalise_claim` | `test_substantive_fact_is_preserved_and_bound_into_identity` | RED |
| Pre-admit delivery identity | `MiniSweAdapter.admit_model_visible_delivery` | `test_provider_refusal_allows_identical_delivery_retry` | RED |
| Restrict dense pool to 256 | `retrieval._dense_pool` | `test_default_dense_pool_has_no_first_256_cutoff` | RED |
| Classify tests from exit code only | `runtime_observation.classify_execution_outcome` | `test_execution_result_does_not_invent_test_success` | RED in four cases |
| Drop later native actions | `miniswe_runtime.execute_actions` | `test_real_native_action_batch_survives_optional_gt_failure` | RED |

The pre-admit mutation exposed a missing identical-byte retry witness. That
test was added to the canonical suite; a provider refusal cannot poison future
deduplication state.

## Accepted engine evidence

- Prior provider-free acceptance proved the engine families without provider
  calls, but it does not authorize dispatch after the producer/harness identity
  changed. A new exact-harness clean Linux acceptance is mandatory.
- Feature matrix: 19 positive plus 19 negative native witness groups green.
  The verifier now fails closed unless every cell is `WITNESSED` and both
  witness polarities exit zero; a regression test proves unexecuted evidence is
  rejected.
- Complete clean Python suite green at disposable source
  `9e77d0dc3b16c0a019d7eef0349eac594e9bf29a`; explicit six-family engine
  acceptance green with `provider_calls: 0`.
- Explicit product closeout is `VERIFIED_PROVIDER_FREE`, `release_eligible:
  true`, and has no release blockers in an isolated environment matching
  Harbor 0.20.0, Pier 0.3.1 and Mini-SWE Agent 2.4.6.
- Exact producer CI `33950014767`, static Linux build `33950014652`, and the
  full local tagged suite are green. Both independent exact-head reviews pass.
- Clean installed Linux: site-packages imports, real graph publication and
  SQLite quick-check, zero legacy candidate rows/edges, pure parser inspection,
  deterministic context packet and final-request binding green; 33 installed
  preservation/state tests green.
- Product closure includes every shipped `gt_engine` module and the source-bound
  binary/build-info artifacts.
- Real SIGTERM path conserves the Mini-SWE patch and terminal receipts.
- Two exact-source arktype builds have identical context-critical semantic rows,
  176,710 nodes, 461,622 edges, and 40 `COMPOSES` edges. The final ambiguity fix
  removes one unjustified relationship. Graph size is at least 68.1616% below
  the retained 2,679,271,424-byte graph, with zero legacy candidate storage and
  zero ordinary materialized derivation nodes/links.

## Remaining release sequence

1. Finish manifest rebinding, commit and publish one immutable canonical harness.
2. Pass provider-free clean Linux acceptance on that exact SHA with
   `provider_calls=0` and `benchmark_runs=0`.
3. Run exactly one authorized GT-on smoke. Do not rerun GT-off.
4. Inspect terminal, patch, graph, provider-admission, context and resource
   receipts. Stop on any integrity or Mini-SWE preservation failure.
5. Only after that verification, run the separately authorized 19-task GT-on
   set and compare against the existing baseline by exact task name while
   preserving every trial.
