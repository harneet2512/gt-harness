# review_me_gt result

Date: 2026-09-05. Scope: provider-free engine/product review before the
authorized one-task GT-on smoke.

Verdict: **ENGINE_REPAIR_ACCEPTED** and **PROVIDER_FREE_READINESS_ACCEPTED**.
The paid route is now explicitly authorized as OpenRouter
`deepseek/deepseek-v4-flash-0731` through Relace only, with provider fallback
disabled. This verdict does not claim solve-rate improvement, and the new
cross-model cohort is absolute/exploratory rather than a causal comparison to
the retained Muse GT-off baseline.

## Product identities reviewed

- Harness base: `9010199412dd1cb4fb5cd60e9ebd63000cc2132f`; final changes are
  intentionally uncommitted in the canonical checkout because its hooks push.
- Disposable clean harness review: `1476ae01b99dcf53ca235cc330d4636895b56385`.
- Producer source: disposable local commit
  `f15db51b78fd2e948091e0193dea7b15ba3d2c52`, tree
  `49ef4c65ee2559aed9a59a0f3c58e23d27152858`.
- Linux producer SHA-256:
  `d5f70b8b5353775ac77865cbc650aac26595344bb27d7d2db291b5586678e1d8`.
- Producer build: digest-pinned Go 1.22.5 Bookworm image, static musl,
  `netgo,osusergo,sqlite_fts5`; embedded build identity is complete.
- Mini-SWE: pinned 2.4.6. Installed Python: 3.12.13 image digest
  `sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2`.
- LSP remains explicitly unclaimed/non-shipping; no unbound scheduler is run.

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

- Provider-free acceptance: baseline, context, state, retrieval, features and
  performance suites green with `provider_calls: 0`.
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
- Full Windows and Linux producer suites green with `sqlite_fts5`.
- Clean installed Linux: site-packages imports, real graph publication and
  SQLite quick-check, zero legacy candidate rows/edges, pure parser inspection,
  deterministic context packet and final-request binding green; 33 installed
  preservation/state tests green.
- Product closure includes every shipped `gt_engine` module and the source-bound
  binary/build-info artifacts.
- Real SIGTERM path conserves the Mini-SWE patch and terminal receipts.
- Exact retained-graph transform preserves canonical semantic hashes at 53.85%
  lower size; fresh arktype rebuild is 52.71% smaller after VACUUM.

## Remaining release sequence

1. Freeze and publish the canonical source with the DeepSeek/Relace-only route.
2. Run exactly one authorized GT-on smoke. Do not rerun GT-off.
3. Inspect terminal, patch, graph, provider-admission, context and resource
   receipts. Stop on any integrity or Mini-SWE preservation failure.
4. Only after that verification, run the separately authorized 19-task GT-on
   set and compare against the existing baseline by exact task name while
   preserving every trial.
