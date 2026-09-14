# Benchmark readiness evidence and approval

Recorded on 2026-09-05. This reference supersedes older status claims in
`GT_HARNESS_SESSION_HANDOFF.md`. The dispatch procedure remains
`BENCHMARK_DISPATCH_CHECKLIST.md`.

## Current verified release — 2026-09-11 (supersedes 4df7ab9c below)

The latest verified functional release is harness
`aa7cf40c486447678665282bba66fff28b55dc83` on
`codex/context-plan-integrity`.

| Artifact | Exact identity |
|---|---|
| Canonical provider-free run | [34568047031, SUCCESS](https://github.com/harneet2512/gt-harness/actions/runs/34568047031) |
| Groundtruth source | `f1e0a7f3659648d43fae70910bf69b6fa8b300b2` |
| Groundtruth source tree | `295d64c48160a59795456b796d5531a8f74a506a` |
| Groundtruth wheel SHA-256 | `97af90f5480def3f9370cbbfef5f4dc6257b80ba015a584ccbf169c2be0fbe6c` |
| Linux producer SHA-256 | `f132885c4686ae214721dfd2163665f2ec7954c321eadb442214ff08c26e1069` |
| Build-info SHA-256 | `85575e5ae6ae5d8ecc780ad43c302556059515ba325386b307bdb56288c0b7b4` |
| Review-inbox commit | `911a003c5cecc959768a98c6e6a16a1b9823c7f9` |
| Exact-source review packets | `har83-context-plan-producer-f1e0a7f3-ci` + 13 prior |
| Lineage | PASS, 14 packets, wheel correspondence 323 files, provider_calls=0 |
| Feature matrix | 21/21 WITNESSED at 6479fb00 (superset covering aa7cf40c state) |

This release carries the analysis-phase fix (resolution tables now populate:
0->71544 callsites, 0->14526 symbols, 0->1835 processes on matplotlib), the six
derived MCP endpoints, communities/processes consumption, and transport fixes
F1/F2/F3/F5/F10. Amend-vs-rebuild parity re-proven +0/-0 identical on the
populated shape (739527 nodes / 1085943 edges).

## Prior verified release (superseded)

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
real timeout CLI variants and the normal export path. The rebuilt harness and
Groundtruth wheels then passed all 28 checks in 11.48 seconds in a
network-disabled Linux container, with no source packages mounted. The repair
is committed in `bea8d59adbf481d8254428fdcf719af4e97cf3de`. Independent review
and successor CI remain required. This repair alone does not establish complete
state exclusion from every index-build input.

Independent review found a follow-up interruption gap: the atomic writer uses
`.model.patch.tmp.<random>`, while the first repair excluded the former
`model.patch.tmp` name. An orphan-file reproduction failed for that exact reason.
The exporter now enumerates and excludes the atomic writer's output-specific
temporary namespace, without excluding unrelated dotfiles. It does not delete
those files or follow artifact-file symlinks when constructing exclusions.

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

### Repeated-history repair

The state-export follow-up at `7d79f4fbbc1e9510520c213bfe8de5372422811f`
passed canonical [CI 33992784333](https://github.com/harneet2512/gt-harness/actions/runs/33992784333).

The next runner change replaces the 120,000-character history target with
digest-based references for identical older tool results. Two regressions failed
before the repair: duplicates below the target replayed in full, and unique older
evidence above the target was deleted from the model request.

Each reference names a full tool result still present in the request. The runner
retains original content under `extra`, validates its UTF-8 digest on reuse, and
restores it if the full result disappears. Different raw output, return codes,
or exception metadata prevent deduplication. Assistant messages and the latest
tool batch remain intact. References assert content equality, not freshness.

A rebuilt harness wheel and the pinned Groundtruth wheel passed 39 targeted
checks in network-disabled Linux. Only wheels, tests, and pytest configuration
were mounted. The checks include the real `BoundedHistoryAgent.query` entry point,
reference recovery and corruption rejection, and normal and timeout patch export.
The command was `python -m pytest -o addopts= -p no:cacheprovider -q -ra
tests/test_miniswe_supervisor.py tests/test_miniswe_repro.py` after offline installation.

This is not a complete evidence-retrieval implementation. The separate
16,000-character output truncation remains. Unique history is preserved and may
reach the final context gate. No token, solve-rate, or end-to-end speedup claim
follows from the targeted checks.

Independent review found no blocking compactor defect and passed 14 focused
checks. The review also confirmed that Mini-SWE 2.4.6 removes audit-only `extra`
before provider dispatch. The entry-point test itself captures messages before
that provider preparation step.

A synthetic request containing twenty identical 16,000-character tool results
shrank from 322,060 to 20,777 serialized UTF-8 bytes after removing audit metadata,
a 93.55% reduction. This measures one repetitive fixture, not provider tokens or
benchmark efficiency.

HAR-83 review REV-356 independently confirms green acceptance and leaves these
items open:

| Item | Required evidence | Current disposition |
|---|---|---|
| Digest-based repetition control | Repeated evidence has stable content references; raw bytes remain recoverable; reasoning, action pairing, current failures, and fresh results survive | Partial. Installed history-reference checks pass. Model-readable retrieval for the separate 16,000-character output truncation remains open |
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

Authorization is **not** recorded here. The verbatim owner record lives in
Linear HAR-81, *Block DeepSWE smoke20 until provider, receipt, and workflow
gates are repaired*
(<https://linear.app/harneet2512/issue/HAR-81/block-deepswe-smoke20-until-provider-receipt-and-workflow>),
and that record governs. A document repeating an owner's words becomes a second
source of authority that can drift from the first, and a reader cannot tell
which one binds. Read HAR-81 for the wording and its date.

What this document records is the *scope* the authorization is exercised under,
which is a technical constraint and belongs with the technical status: approval
covers one GT-on smoke, and the remaining 19 canonical tasks only conditionally.
It does not authorize dispatch before the technical gates pass, a baseline
rerun, additional tasks, alternate routes, automatic paid retries, or an
unrestricted benchmark. Where this paragraph and HAR-81 disagree, HAR-81 is
correct and this paragraph is a defect.

The one-task stage is `gate-one`, task
`aiomonitor-task-snapshots-diff` (small Python repository; the canary moved off
arktype because it was the cohort's largest workspace in a language the
producer abstains on). The route remains defined solely by
`config/provider_route.v1.json`.
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

## Gate-one attempt — run 34790375793 (2026-09-13)

The first paid gate ran task `cyclotruc__gitingest-94` (SWE-bench-Live Lite,
per the documented pivot from DeepSWE for the paid gate). Verdict: **the task
solved** — the official verifier executed 23:55:05→23:55:21 and returned
`reward = 1.0`; `eval_report.json` reports `resolved: true` with all
PASS_TO_PASS green. Engine evidence was clean: 56/56 provider calls on the
relace-pinned route, graph CERTIFIED (`cochange_rows = 1242`), 26 deliveries
admitted / 17 consumed / 7 fair, iter-0 localization hit the gold file with
`prior_touches: 0`, plan gate suppressed two premature submits and accepted the
final one `no_blocking_evidence` honestly labeled `submitted_unverified`.

The run was *reported* as ERROR because two consumer-layer defects converted
the solved journal into `ValueError: refused_then_delivered` plus
`official_verifier_missing`:

- **F9** — the refusal/delivery join compared refusal sequence against
  commit-time `evidence_delivery` sequence instead of the admission axis
  (`delivery_ordinal >= candidate_ordinal`). Legitimate same-batch twin
  deduplication (`localization_fire_once`, sibling admitted ordinal 1, twin
  refused candidate ordinal 2) read as refused-then-delivered.
- **F10** — `_reward` read `metrics[*].reward`, a shape real pier never emits;
  the production aggregate carries `reward_stats.reward` maps. Every solved
  run would have classified `missing_verifier`.
- **F11** — monitor snapshots were written outside the collected artifact
  tree and transient upload failures were swallowed; zero mid-run artifacts
  existed for the whole run.

All three are fixed and regression-tested in `e41d1fe9`
(branch `codex/phase5-harness-proof`). Capability audit found no capability
defect: the 14 live-INELIGIBLE feature identities were `no_trigger_observed`
or profile-gated by design (`expected_profile_controls: []`); LSP
`no_edge_mutations` is an honest environment limit (only JS-edge candidates in
a Python repo, no `node_modules/typescript`). Full forensic record:
`docs/HANDOFF-2026-09-14-run-34790375793-forensics.md`.

A main-branch side effect of the workflow registration also broke
`test_only_closed_supported_workflow_set_is_active` there; repaired in
`e4fe9661`.

**Status:** provider-free acceptance `34795536349` and installed rehearsal
`34795537759` passed on `e41d1fe9`; the owner authorized the final gate-one
retry, dispatched as run `34796061920` on source `8dd02318`.

## Gate-one retry — run 34796061920 (2026-09-14)

The retry ran the same task `cyclotruc__gitingest-94` on `8dd02318`. Verdict:
**the task solved again** — attestation PASS, `solved: true`, `reward: 1`,
`status: GRADED`, `verified: true`, graph CERTIFIED, 15/15 provider calls on
the relace-pinned route ($0.010 total), terminal `submitted_verified` (the
plan gate bound verification before submit this time). F9/F10/F11 all held:
no `refused_then_delivered`, reward bound `GRADED`, diagnostics shipped.

The workflow still reported `failure` on one line: `diagnose_benchmark_run
--strict` exits 1 whenever a `required=True` capability is not WORKING, and
`lsp_promotion` read DEGRADED `terminal_succeeded:no_edge_mutations` because
the run's only promotable candidates were 2 JavaScript edges whose leg was
skipped — `install_missing_reason: no workspace TypeScript install
(node_modules/typescript) and no tsserver.path`. Root cause: the staged
`typescript@7.0.2` package ships no `lib/tsserver.js` (the TS7 package is the
native/API layout), so `typescript-language-server` could never initialize
even though staging succeeded. `validate_prior_gate` requires
`diagnostics.exit_code == 0` plus every required capability WORKING+verified,
so this solved run could not unlock the remaining cohort — under the old
contract a Lite gate-one could never produce healthy diagnostics on any repo
whose only promotable edges need an absent toolchain.

Fixed in `aef3a2f5`, two layers:

- **Provision:** all three LSP-staging workflows now pin `typescript@5.9.3`
  (ships `lib/tsserver.js`, published 2025-09-30), pass `--tsserver-path` to
  the `typescript-language-server` shim, and assert the staged file exists so
  a package-layout change fails setup instead of silently degrading legs.
- **Classification:** `_leg_serviceable` marks a language leg unserviceable
  only on positive evidence (`install_missing_reason`, server never
  launched, zero candidates, zero requests issued despite candidates). When
  every leg is environment-bound, `lsp_promotion` reports DEGRADED with
  `:no_serviceable_candidates` evidence but `required=False`; missing
  receipts, absent fields, mixed legs, and never-scheduled all fail closed
  as required. `no_op` stays not-required. A leg that served requests and
  produced nothing remains a required failure — the gate keeps its teeth on
  real capability gaps.

**Status:** provider-free acceptance `34799716299` and installed rehearsal
`34799717545` passed on `aef3a2f5`. The corrected paid gate-one ran as
`34800169651` and is the first fully green paid run: the task solved
(`resolved: true`, `reward: 1`, `GRADED`), attestation PASS, strict
diagnostic `exit_code: 0`, `lsp_promotion` honestly
`DEGRADED:terminal_succeeded_no_edge_mutations_no_serviceable_candidates`
with `required=False`, 20/20 provider calls on the relace-only route
($0.0117), `submitted_verified`, and `run-status/` snapshots now ship in the
collected artifact tree. The LSP receipt additionally showed
`typescript-language-server@6.0.0` rejecting `--tsserver-path` as an unknown
flag — the leg stayed environment-bound rather than failed, and the
classifier held; the provisioning refinement is queued for the next
SHA-binding window.

The owner then authorized the conditional continuation: DeepSWE smoke20
dispatched as run `34801009507`, `all-20` stage on `aef3a2f5`, readiness
reused from `34799716299`.

## DeepSWE smoke20 — run 34801009507 (2026-09-14)

**Outcome: 6/20 solved** (abs-module-cache-flags, abs-stepped-slices,
actionlint, awilix, csstree, katex — verifier `resolved: true`, reward 1.0);
12 graded-unsolved genuine failures; 2 typed errors (boa `provider_failure`
rate-limit, fd `churn_abort`). Workflow `failure`: all three attest
verification steps rejected on real defects — full census in
`docs/HANDOFF-2026-09-14-run-34801009507-smoke20-forensics.md`.

Defects the smoke exposed, in fix order: missing-patch bind-step crash on
non-submitting trials; audit join cannot model legitimate post-recovery
redelivery (oxvg duplicate identity); 8 tasks missing diagnostics artifacts;
`GT_GRAPH_REFRESH_FAILED` under rust-class workspace churn (oxvg, pest,
plus fd's 180 s rust-analyzer readiness stall); `--tsserver-path` unknown
flag crashing TLS 6.0.0; provider rate-limit at 20-way parallelism.

What held: `lsp_promotion` WORKING on go (`306_edges` published); F9/F10/F11
under 20-way load; `_leg_serviceable` classification (env-bound legs honest
`required=False`, serviceable gaps still `required=True`); churn governor;
typed ERROR outcomes with `reward: null`.

**Status:** smoke20 complete; the defect queue above precedes any next paid
run. The cohort needs its fixes verified provider-free before another
gate-one + smoke cycle on a new SHA.

## Post-smoke hardening + certification wave — 2026-09-14

The smoke20 defect queue and the capability-hardening sweep landed on
`codex/phase5-harness-proof` (commits through `b508a4fc`). The Groundtruth
runtime language-matrix wave was upstreamed to `harneet2512/groundtruth`
branch `wheel-patch-lsp-inventory`:

- `e65febd3` — runtime patterns/obligations/test-runner/repo-adapters/
  resolve language-matrix coverage (upstream CI lint red on formatting).
- `4f19c80a` — format-only fixup; upstream CI `34833873538` all-green
  (lint, test matrix ubuntu/windows/macos 3.11+3.12, go-build, benchmark).

Certified artifacts at source commit
`4f19c80a6263cc0f45539ad329f4d3a200941231` (tree `357e8d2e`):

| Artifact | Identity |
|---|---|
| Wheel SHA-256 | `160120290c2584a2a6e54a61ba9b5e6327de4eabb62656f18c0a8fbce0092180` (built from `git archive` LF export; an earlier CRLF working-tree build `0d3c72c5` was byte-rejected by Linux correspondence) |
| Producer SHA-256 | `6dad9e3104981a54a734623835232f9b499f5e2d3674be016bc4a0ea4dfe852f` (upstream run `34833870450`, pinned `golang:1.22.5-bookworm`, `sqlite_fts5`, static) |
| Build-info SHA-256 | `fa5e0b6023e1f1684bda01c960b612b364b301f1671294725874ede1754bea95` (fingerprint `30536060` unchanged — Go subtree identical) |
| Review inbox | `gt-review-inbox-4f19c80a` @ `4cb9ec66cbc0db9cdbb03cfdec909d1c3859a18b` |
| Head packet | `har83-context-plan-producer-4f19c80a-ci`, digest `aee82049` |
| Local lineage dry-run | PASS — 19 packets, exact-source review, 323-file correspondence, zero failures |

Acceptance run `34835849569` on `ed35e3b5` correctly rejected the CRLF
wheel (`wheel_source_correspondence_mismatch`) — the gate works as
designed. Two further latent defects surfaced on the way to green: the
`smoke20_recorded` verbatim-capture fixtures failed FD-definition
scanning (`invalid_machine_syntax` — captured `[GT_*]`-tagged bytes are
not authored documents; the fixture root is now excluded), and three
serial-suite literals drifted (budget table, prompt-kind producer scan,
closed workflow set). All fixed; the canonical provider-free acceptance
is **GREEN on `4f1c106c` — run
[34839281975](https://github.com/harneet2512/gt-harness/actions/runs/34839281975)**.
Installed rehearsal re-dispatched on the same SHA as run `34840346061`.

## Provider retry pacing + cohort dispatch stagger — `b2b5fc05` (2026-09-15)

The boa `provider_failure` mechanism is fixed at both layers it had:

- **In-loop pacing** (`gt_engine/provider_pacing.py`): Mini-SWE's tenacity
  retry used one fixed exponential schedule, so twenty matrix legs retried
  in lockstep against a shared account ceiling. `query_transport` now wraps
  the per-attempt transport call: a retryable failure (rate-limit, timeout,
  disconnect) is journaled as `provider_retry_paced` (secret-redacted),
  diagnosed as consequential `WARNING` evidence in phase
  `provider_retry_pacing` (never competing with the terminal primary row),
  sleeps the provider's own `Retry-After` (capped at 120 s) or a bounded
  uniform jitter draw (`GT_PROVIDER_RETRY_JITTER_MAX_SECONDS`, default
  45 s), then re-raises the ORIGINAL exception so the loop keeps its
  attempt budget. Non-retryable failures (billing, bad-request,
  context-window, resource-exhausted, malformed) are never paced. A pacing
  bookkeeping fault takes the non-disabling observation channel — it
  cannot silence the terminal `provider_failure` receipt.
- **Dispatch stagger + declared config**: `provider_route.v1.json` gained a
  closed `retry_pacing` block (`dispatch_stagger_seconds` 20,
  `retry_jitter_max_seconds` 45, `retry_after_cap_seconds` 120,
  `model_retry_attempts` 15), validated fail-closed in `load_route` and
  attested plan↔manifest by `attest_deepswe`
  (`planned_cohort_pacing_mismatch`). Both paid workflows emit
  `cohort_pacing` into the immutable plan, sleep `(ORDINAL-1)*stagger`
  before the run step, and export the jitter cap, Retry-After cap, and
  `MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT` to the run environment.

Verified provider-free with tests/mocks/stubs through the real wrapped
transport: 21 tests in `tests/test_provider_pacing.py` — 429/status/name/
code rate-limit classes, Retry-After honored and capped, retry-then-
success response binding, exhaustion ordering (paced rows precede the
terminal `provider_failure` and its primary diagnostic), 20-way delay
decorrelation, all five non-retryable classes never paced, secret
redaction, env resolution + invalid-env fail-closed, and the
fault-channel contract. The broken-recorder fault-injection suite still
proves the terminal receipt survives.

**Gates:** canonical provider-free acceptance GREEN on `aeff21be` (run
`34847423642`, serial suite back to ~4m20s); installed full-flow
rehearsal GREEN on the same SHA (run `34847426452`). An earlier green
pair on `01e5eac1` (`34845687656`, `34845689986`) exposed that scripted
retryable failures paid real wall-clock per attempt; `tests/conftest.py`
now stubs the `provider_pacing._sleep` seam autouse so no test burns
real provider delays (pacing tests still stub the seam explicitly to
assert decisions).

Remaining open items before any next paid run: **resolved** — the owner
kept the `-0731` relace route and authorized "1 smoke then 20" (verbatim
record on HAR-81, 2026-09-15). Gate-one dispatches on `aeff21be` with
readiness `34847423642`; `remaining-19` follows only after the gate-one
result passes `validate_prior_gate`.

## Paid gate-one `34849119441` — task SOLVED, attestation rejected (2026-09-15)

The paid gate-one ran on `aeff21be` against
`aiomonitor-task-snapshots-diff`. The task itself **solved** (GRADED,
official verifier resolved; 107 provider calls completed). The pacing
wave proved under real load: a live 429 was journaled
`provider_retry_paced`, the retry recovered inside the provider budget,
and the run continued to a solve.

Attestation correctly rejected the run (`exit_code` 1,
`validate_prior_gate` fails, `remaining-19` stayed halted — no dispatch
was attempted or authorized). Three defects, all load-dependent shapes
the provider-free pyramid did not reproduce:

1. **`lsp_promotion` DEGRADED (required)** — reporter blind spot, not an
   engine failure. 26 terminals: 9 published, 9 no-edge-mutations, 8
   obsolete. The last candidate raced edits to `obsolete`, the scoped
   merge salvaged its 284 mutations (`merge-b0bptwou`, zero stale or
   diverged skips), and the merged graph was the run's final publication.
   The reporter only joined `lsp_promotion_terminal` rows and never saw
   the salvage channel — it graded the tier the engine had actually
   landed.
2. **`GT_GRAPH_REFRESH_FAILED` ×5 (primary ERROR)** — 19 amend refusals,
   8× `GT_INDEX_MEMORY_GUARD_TRIGGERED` exit -9 plus 4×
   `GT_INDEX_PROCESS_FAILED` exit 1, all dying in Pass 1 file discovery
   while resident LSP legs held the cgroup. Both amend paths journaled
   the refusal and returned without feeding `_index_memory_defer_until`,
   so every serving boundary spawned back into the same pressure (five
   dead spawns inside ~60 events) and the escalation ladder could aim a
   heavier full rebuild at measured pressure.
3. **Verdict counted a handled WARNING as fatal** —
   `diagnose_artifact_root` failed any run with any event row, so the
   recovered paced-429 WARNING alone would have failed the gate even
   with every capability WORKING.

## Defect fix wave — `6d19b195` (2026-09-15)

All three fixed on one commit with deterministic coverage:

- **Salvage-aware promotion reporting**: `lsp_salvage` drains now journal
  `graph_revision`/`source_revision`; the reporter joins the salvage
  row whose revision matches the adopted publication to its source
  promotion receipt and grades the tier from that receipt's yield
  (promoted edges, per-language `selection_complete`). Superseded,
  partial, and unreceipted salvage all fail closed.
- **Unified spawn backoff**: `_note_amend_failure` feeds a 60s
  amend-spawn window for any dead producer and the 120s cgroup window
  (`index_memory_backoff`, renamed from `lsp_memory_backoff` since it
  now gates every index-family spawn) for the memory family. Both the
  transaction-boundary and serving-boundary amends consult the window
  before spawning (`graph_amend_deferred`, one row per window);
  `_recovery_build_inline` defers while the cgroup window is open
  (`graph_recovery_deferred`). Memory-family refusals report as
  WARNING/consequential/retryable and stay off the escalation ladder;
  non-memory `amend_failed:*` keeps the primary-ERROR streak, one
  window apart per attempt.
- **Severity-based verdict**: ERROR events and required capabilities
  below WORKING still fail the run; consequential WARNINGs remain the
  task's noteworthy event without forcing a verdict.

Coverage added: 5 salvage reporter cases (adopted, lost-race,
superseded, partial, unreceipted) in `test_gt_session.py`; 6 memory
cases (killed-amend defer, consequential typing, deterministic
escalation preserved, recovery defer, transaction feed, and a
40-boundary sustained-churn storm replay asserting ≤5 spawns, zero
escalations, all-WARNING typing, clean recovery) in
`test_miniswe_integration.py`; 2 verdict cases in
`test_run_diagnostics.py`. 4 pre-existing tests in
`test_index_incremental.py` were updated to drain the spawn window
between attempts — their old timing was the defect shape.

Full affected surface green: `test_miniswe_integration`,
`test_gt_session`, `test_run_diagnostics`, `test_admission_transactions`,
`test_agent_loop_soak`, `test_index_incremental`,
`test_index_resource_guard`, `test_scoped_merge`,
`test_lsp_graph_publication`, `test_lsp_promotion_wiring`,
`test_capability_matrix`, `test_index_reuse`.

Provider-free re-verification on `eb1611b5` (fix wave + ledger tip):
canonical acceptance `34874051204` **GREEN**; installed full-flow
rehearsal `34874053460` **GREEN**.

**Paid state**: gate-one's verdict stands rejected — the fix is not
evidence the live run passed. A new paid smoke requires a fresh owner
approval receipt on the green-gate SHA; `remaining-19` stays gated
behind a clean one-task gate throughout.

## Generalized workload simulator + ancestry fix (2026-09-15, `23badbcb`)

The standing gap the gate-one postmortem named: the deterministic suite
proved unit behavior, never sustained load. `tests/test_workload_simulation.py`
closes it — a `_Sim` harness that drives a real `MiniSweAdapter` through
scripted ticks (edit transactions, bare dirty-markings, provider waits,
serving boundaries) with `time.monotonic` stubbed to a script clock and
producer outcomes stubbed at the process boundary as a function of a
pressure flag. Scenarios:

- **Sustained churn (gate-one shape)**: 150 ticks at 45s, resident legs
  pressing the cgroup six ticks in seven, legs losing every publication
  race — pressured spawns bounded by the windows not by boundary count,
  every refresh event WARNING/consequential/retryable, zero recovery
  builds under pressure, ≥2 salvages landed through the real
  wait-scheduler drain, `lsp_promotion` attested **WORKING** at seal.
- **Persistent pressure**: honest degraded seal, bounded attempts.
- **Deterministic failure escalation**: bounded, streak-based.
- **Calm control**: published leg, zero diagnostics.
- **Parameterized storm**: three workload shapes (continuous-tight,
  sparse-slow, front-loaded-clears) asserting the same invariants under
  different cadence/pressure profiles.

The simulator's first run caught a **real defect** the unit suite missed:
a salvage merge landing mid-run is only the newest graph until the next
amend — the capability reporter's join on the *last* publication missed
it and would have reported `lsp_promotion` DEGRADED on a graph whose
tier was populated via the merge's descendants. Fixed by journaling
`parent_graph_sha256`/`build_mode` from the manifest
(`_record_graph_publication`) and walking publication ancestry in the
reporter: the nearest chain ancestor minted by a published terminal or a
published salvage merge decides the tier; a parentless publication is a
fresh build where the walk stops. This also closed a latent
false-positive — a fallback terminal claiming `published` can no longer
credit a tier on a graph the run already replaced with a rebuild; the
yield gate now requires the output provably on the adopted chain.

New coverage: 4 sim scenarios + 3 parameterized shapes in
`test_workload_simulation.py`; 3 ancestry cases (carried-forward salvage,
rebuild-severs-chain, published-output-replaced) plus an honest fixture
update in `test_gt_session.py`. Lint clean on the new file; only
pre-existing findings remain elsewhere.

Full serial suite: vendored-producer fingerprint and smoke20 recorded-replay
failures reproduce at clean `HEAD` — pre-existing Windows-environment
defects, not this wave. Product-identity tests green on the committed tip.
Provider-free gates re-dispatched on `23badbcb`: acceptance `34884400879`,
installed rehearsal `34884403332`.

## SWE-bench-Live smoke authorization — 2026-09-15

Owner instruction (verbatim): "do 5 smoke of swe live lite tasks as they are
smaller and use 5 different languages". Scope resolved before dispatch:
SWE-bench-Live `lite` is Python-only upstream (per the SWE-bench-Live paper —
"our benchmark SWE-bench-Live primarily focuses on the Python language only" —
and the leaderboard), and the pinned suite binds exactly two tasks:
`cyclotruc__gitingest-94` (gate task) and `dynaconf__dynaconf-1241`
(remaining). A 5-language Lite smoke does not exist; the multi-language
variant is a different dataset (`SWE-bench-Live/MultiLang`) and would be a
separate suite integration, not a dispatch. The owner selected the bound
2-task smoke.

Authorization scope: paid SWE-bench-Live Lite smoke, staged — `gate-one`
(gitingest-94) first, attestation validation, then `remaining`
(dynaconf-1241) bound to the gate run via `prior_gate_run_id`. Preconditions
unchanged: provider-free acceptance and installed rehearsal green on the
dispatch SHA (`23badbcb`), readiness bound by `readiness_run_id`, the
official verifier is sole authority for task success, GT-off never run (no
SWE-Live baseline exists and none may be created). Any further paid scope —
DeepSWE gate-one re-run, expanded cohorts, a MultiLang suite — requires its
own fresh approval receipt.

### SWE-Live gate-one — run `34885373005` (2026-09-15): **PASS**

Dispatched on tag `dispatch-swelive-23badbcb` (GitHub dispatch requires a
branch/tag ref; the tag resolves `source_sha=23badbcb`, matching the
readiness receipt). `gate-one` + `approve_paid_run=true` +
`readiness_run_id=34884400879`. All pre-flight stages green (plan,
readiness_binding, image_digest_gate, provider_gate); task leg ~10.5 min.

- `cyclotruc__gitingest-94`: **SOLVED** by the official verifier — reward 1,
  graded, `failure_class=graded`, status GRADED.
- Capability report: all 5 required capabilities **WORKING** and
  independently verified — including `lsp_promotion`
  (`terminal_succeeded_published_2_edges_last_of_6`, published-terminal
  channel; the run was calm, no salvage needed).
- Diagnostics: **HEALTHY**, zero artifact issues, exit_code 0 — no memory
  storm, no refusals, no escalation. The severity-based verdict change held:
  zero events, nothing to misjudge.
- Live gate `passed=true`: complete census, 7/7 features witnessed and
  exercised, `source_sha=23badbcb`.
- Provider: 36/36 calls completed, 0 failed; route exact
  (`deepseek-v4-flash-0731`, relace-only, `allow_fallbacks=false`,
  `require_parameters=true`). Tokens: 1,153,356 in / 15,545 out /
  917,504 cached; 31 deliveries; `unmet_predicate_count=0`; graph CERTIFIED.
- `validate_prior_gate` conditions verified against the downloaded
  attestation before dispatching `remaining`.

`remaining` dispatched as run `34888711134` (same tag),
`prior_gate_run_id=34885373005`, `readiness_run_id=34884400879`.

### SWE-Live remaining — run `34888711134` (2026-09-15): attestation **FAIL**

- `dynaconf__dynaconf-1241`: **SOLVED** by the official verifier (reward 1,
  graded). All 5 mandatory diagnostic capabilities WORKING at seal; all 21
  feature-matrix identities WITNESSED. `lsp_promotion` exercised for real:
  `terminal_succeeded_published_1_edges_3_of_10_obsolete` — the ancestry path
  salvaged 3-of-10 obsolete legs through the live merge.
- The gate **correctly rejected** the run: `GT_GRAPH_REFRESH_FAILED` ERROR —
  `amend_failed_gt_index_process_failed_exit_1`, four identical deaths, all on
  parent `lsp-b62_t634/graph.db`, prescribed recovery
  `rebuild_graph_for_current_workspace_revision`. Reproducible across retries =
  deterministic defect, not pressure. The verdict was right; the smoke gate did
  its job.
- **Root cause (proven, reproduced locally on the real artifact):**
  `merge_lsp_candidate` performs in-place `UPDATE`/`DELETE`/`INSERT` on
  parser-owned node rows but does not maintain `parser_*_inventory`
  (not in `_MERGEABLE_TABLES` — the "unlisted table is under-merged (safe)"
  assumption was wrong). Merge `merge-9bdqrbry` wrote `updated: 441` node rows
  carrying LSP-enriched `signature`/`return_type` fields; the inventory still
  mapped un-enriched base identities to those rowids. The leg copies inherited
  the stale aliases verbatim (339 live aliases in `lsp-b62_t634`). Next batch
  amend parses the file fresh, hits the inventoried identity, compares against
  the enriched stored row, `DeepEqual` fails, producer `log.Fatalf` → exit=1.
- **Fixes landed:**
  - `gt_engine/scoped_merge.py`: merge now maintains `parser_*_inventory` —
    evicts identities whose rows it mutates/deletes, re-mints inventory for
    rows it inserts. Stops manufacturing corrupt parents.
  - Producer (`harneet2512/groundtruth` `1e893f63`, vendored mirror
    `vendor/gt-index-src`): diverged inventory entries are treated as missing —
    stale row swept by the existing non-inventoried DELETE, fresh parse minted
    under a new rowid, counted and surfaced as `inventory_diverged` in the
    result JSON and on the batch-structure stderr line. Fail-closed behavior
    for genuinely missing legacy inventories and foreign producer identity is
    unchanged. Local E2E on the real `lsp-b62_t634` artifact:
    `909 retained, 441 inserted, 339 inventory-diverged` — healed, no fatal.
  - `gt_engine/indexer.py`: `_amend_failure_reason` now reports the **end** of
    `stderr_tail` (the fatal line), not the head banner; failed amends persist
    `index-failure-resource.json` + `graph.failure.json` with
    `exit_code`/`stderr_tail`/`stderr_sha256`/cgroup fields — the evidence gap
    that hid this defect's true signature is closed.
- **Capability-surface reconciliation:** the "19 capabilities" census
  (11 FACT identities + `GT_CERT_DELIVERY`, `GT_CHANGE_SURFACE`,
  `GT_EDIT_CHECK`, `GT_HYPOTHESIS`, `GT_LOC_RESLOT`, `GT_PATCH_DELTA`,
  `GT_SS_SUBMIT_RED`, `select_catalog`; plus post-census `persistent_plan`,
  `plan_gate`) is the `feature-matrix.json` contract — 21 identities, all
  WITNESSED on both smoke tasks. The 5-row report is a different surface
  (`_mandatory_capability_rows()` in `gt_session.py`). Proving "capabilities
  work" = the 21-row matrix + real delivery/attribution evidence, not the
  5-row diagnostic alone.
- **Sequencing:** this two-task smoke correctly failed its gate. Benchmark
  readiness still requires a clean two-task SWE-Live smoke on the healed
  producer, then the 20-task DeepSWE matched cohort. No readiness claim yet.

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
