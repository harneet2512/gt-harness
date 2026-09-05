# Benchmark readiness evidence and approval

Recorded on 2026-09-05. This reference supersedes older status claims in
`GT_HARNESS_SESSION_HANDOFF.md`. The dispatch procedure remains
`BENCHMARK_DISPATCH_CHECKLIST.md`.

## Current verified release

The latest verified functional release is harness
`4df7ab9c042b8cc1dd6708ec46b431646f3b7d1d` on
`har81/canonical-task-identity`.

| Artifact | Exact identity |
|---|---|
| Canonical provider-free run | [33990917733, SUCCESS](https://github.com/harneet2512/gt-harness/actions/runs/33990917733) |
| Groundtruth source | `1ecd03674f7eb6a79f401c95bf147423379d5143` |
| Groundtruth source tree | `d9e48fd4702f37cc30b7562ef1abe691a1e39273` |
| Groundtruth wheel SHA-256 | `4c4ba9ac08ee8f352e125be69bc0e60d9fc540af1a04b4fe5010d9ac8c1f488f` |
| Linux producer SHA-256 | `8763262b13f44d4bc463a7481d93e74b86137d49b32d8f86bae06879086baf4f` |
| Build-info SHA-256 | `e80446b6010c0c49c647871c2dcdcb34331cedc8dd2519c42458a7f251fa7570` |
| Review-inbox commit | `8a5a5b87859b8360667480996354a98386d57b1a` |
| Exact-source review packet | `har83-unified-source-1ecd03674` |
| Product bundle digest from green closeout | `9d7a502d8f1e3a6e3cd5c61c4ec2c39b55db5786c528f851aa45e46b9fcdae59` |

The source review verified 317 byte-matching wheel files and the actual Linux
producer identity. Its PASS covers source correspondence and scoped repairs.
It does not certify benchmark outcomes.

Canonical CI passed provenance, installation, static workflow and secret-boundary
checks, recorded-content verification, deterministic bare and GT parity arms,
19 witnessed feature-matrix cells, and the full Python suite. Six tests skipped.
Those skips cover absent real-graph fixtures, unavailable sqlite_vec, and the
complementary graph-unavailable smoke case. They are not proof of those missing
integration cases.

A separate network-disabled installed-package run passed 90 lifecycle checks.
One complementary graph-unavailable case skipped because the graph was available.
The run included Mini-SWE submission and timeout conservation. Its harness wheel
contained the release-resolver implementation from 52b9e5b5, before the workflow-only
4df7ab9c change. It is supporting evidence, not an exact-4df7ab9c certification.

The green closeout reports zero provider calls, zero benchmark runs, no product
release blockers, and no secret-canary matches. Its release-eligible field is
an installation/admission result, not a claim of higher solve rate.

## Removed release defects

- The manifest now binds the rebuilt matching wheel, producer, and build receipt.
- The installer reads GT hashes and paths from that manifest. Duplicate literals
  and arbitrary last-wheel selection are removed.
- Paid attestation uses the same verified wheel resolver. Its obsolete hash and
  the structural assertion requiring that hash are removed.
- Producer installation precedes feature-matrix execution. Previously the graph
  witnesses skipped because their executable was not installed yet.
- Unused installer version placeholders and their tautological test are removed
  in the next source revision. Real archive digest checks remain.
- The local commit hook has a bounded 60-second Linear request. It still checks
  authority. Repeated Linear 502/504 responses remain an external publishing risk.

Historical renderer fixtures, recorded runs, and untracked
`artifacts/product-closeout-local/` remain unchanged. Committed removals are
recoverable from Git.

## State-export repair after the green release

The real supervisor CLI reproduced a state leak with its state directory inside
the task repository. A zero-budget timeout preserved the source edit but also
exported an internal JSON record into `model.patch`. The external-state variant
passed. This is a patch-contamination defect, not a missing test.

Normal completion and timeout recovery now share
`scripts.miniswe_supervisor.export_patch`. Both callers exclude their configured
state directory. The exporter also excludes its own output and temporary file,
uses literal Git path exclusions, rejects exclusions containing the whole
repository, and leaves the agent's Git index unchanged.

Local verification passed 27 tests with one Linux-only skip, including both
real timeout CLI variants and the normal export path. Installed Linux proof,
review, and successor CI remain required. This repair alone does not establish
complete state exclusion from every index-build input.

## Capability reference

The canonical census is `gt_engine.attribution.DIRECT_FEATURES`.
The following intended actions come from that registry. Positive and negative
witness bindings are in `gt_engine.feature_matrix`.

| Identity | Eligible boundaries | Intended action |
|---|---|---|
| `caller_contract` | file_view, edit_result | update or inspect proven callers |
| `cochange_prior` | file_view, edit_result | inspect or update the proven companion file |
| `covering_red` | edit_result, submit | repair an attributable covering-test regression |
| `def_partition` | search_result | distinguish definitions from references |
| `localization` | task_start, search_result | inspect ranked relevant source locations |
| `newfile_precedent` | search_result, edit_result | follow a verified repository precedent for a new file |
| `obligations` | task_start | satisfy issue-derived requirements |
| `recovery` | test_result, tool_result | change hypothesis after falsification or repair the observed required RED before further exploration |
| `signature_delta` | edit_result | repair call sites affected by a signature change |
| `submit_refusal` | submit | resolve positive failing evidence before submission |
| `syntax_result` | edit_result, submit | repair an executed syntax failure |
| `GT_CERT_DELIVERY` | submit | name the evidence state of the completion decision |
| `GT_CHANGE_SURFACE` | search_result | identify the proven change surface |
| `GT_EDIT_CHECK` | edit_result, submit | validate edited code with deterministic checks |
| `GT_HYPOTHESIS` | test_result, tool_result | track repeated failures across edits |
| `GT_LOC_RESLOT` | task_start, search_result | reslot a ranked localization result into the request |
| `GT_PATCH_DELTA` | edit_result | derive evidence from the actual before/after patch |
| `GT_SS_SUBMIT_RED` | submit | refuse once after an observed unresolved test failure |
| `select_catalog` | task_start | select and order existing catalog IDs for the next execution focus |

All 19 identities have provider-free matrix witnesses at 4df7ab9c. That does not
mean every identity is demonstrated through the installed native Mini-SWE loop.
A complete native proof needs eligibility, current source/graph binding, producer
execution, admitted bytes, the immediate model-facing request, and the resulting
action or explicit non-consumption. Capability execution and owning-fact delivery
remain separate claims. Submission context must preserve the initial
non-enforcing policy and Mini-SWE's action authority.

## Remaining pre-smoke evidence

HAR-83 review REV-356 independently confirms green acceptance and leaves these
items open:

| Item | Required evidence | Current disposition |
|---|---|---|
| Digest-based repetition control | Repeated evidence has stable content references; raw bytes remain recoverable; reasoning, action pairing, current failures, and fresh results survive | Open. The shipping runner still uses 16,000-character output truncation and a 120,000-character history target |
| State-directory exclusion | Writes to the configured GT state directory do not change workspace revisions, trigger graph rebuilds, or enter the exported task patch; legitimate source edits remain visible | Requires caller-level audit and installed proof |
| Unpaid full-flow rehearsal | The paid installation and orchestration path runs with a deterministic provider substitute, real file edits and subprocesses, final patch export, verifier binding, and typed receipts | Not established by the matrix or local lifecycle suite alone |
| Forced-timeout rehearsal | Timeout during active work preserves the latest patch, terminates children, and emits truthful failure receipts without score invention | Local cases pass; whole paid-path rehearsal remains open |
| Independent rehearsal review | Exact artifacts and source are inspected independently, with findings recorded on HAR-83 | Pending rehearsal artifacts |
| Component overhead | Cold/warm graph work, rebuild count, retrieval latency, repeated context bytes, and resource use are measured on fixed workloads | No complete current product budget established |

The broader architecture backlog remains in the Astra review: one current
base-plus-overlay engine, dependency-safe publication, useful packet priorities,
independent semantic retrieval recall, correct dense recipes and cache identity,
and real receipt-bound LSP consumers. Green CI does not close those items.

## User smoke authorization

The user wrote on 2026-09-05:

> yes document everything and go jext and u have approval for one smome and if that works 19 too

This records approval for one GT-on smoke and conditional approval for the
remaining 19 canonical tasks. It does not authorize an immediate dispatch before
technical gates pass, a baseline rerun, additional tasks, alternate routes,
automatic paid retries, or an unrestricted benchmark.

The one-task stage is `gate-one`, task
`arktype-json-schema-refs-dependencies`, with the existing 1,800-second agent
cap. The route remains defined solely by `config/provider_route.v1.json`.
No credential value belongs in this document or any receipt.

The conditional continuation requires a successful gate-one task and valid
official-verifier, patch, provider-route, integrity, capability, and terminal
receipts. `validate_prior_gate` must accept the exact prior run. The remaining
stage must bind that run and the same source and bundle. A failed or unknown
result, source change, invalid receipt, route change, or unresolved defect keeps
the remaining 19 halted. A failed attempt does not authorize a paid retry.

No numeric spending ceiling is specified in the user's message. The exact
dispatch plan and applicable spending limits must be made explicit before any
paid request. Approval is not evidence of readiness or an actual dispatch.

## Outcome claims

The retained Muse baseline contains 452 trials across 113 tasks and remains
read-only. The approved DeepSeek route uses a different model. Comparison with
Muse is descriptive and cannot isolate GT's causal contribution.

A causal claim that GT solves more tasks with fewer resources requires a matched
same-model control with task, scaffold, provider, budget, and environment parity.
No such control rerun is authorized here. No exponential efficiency claim is
supported by current evidence.

Confidence is high for the recorded artifact identities and executed checks.
Whole-product efficacy and the attainable solve/efficiency improvement remain
unknown until the relevant experiments execute.
