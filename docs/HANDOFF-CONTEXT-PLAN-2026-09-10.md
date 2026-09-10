# GT context + plan implementation handoff — 2026-09-10

## Release state and objective

**NOT BENCHMARK READY.** Continue the existing implementation, not a redesign.
The objective is better correctness and lower harness overhead without removing
capabilities, weakening evidence gates, or repeatedly rebuilding graphs.
Deterministic context/plan delivery is being verified; it is not proof of
universal task success or 4/4 repeated success. No measured six-repository
speedup has yet been established.

The authoritative checklist is `docs/plans/context-plan-implementation-status.md`:
51 checked and 17 open items at handoff. This is an item count, not an effort
percentage. Its historical evidence paragraphs are cumulative; use exact commit
and artifact identities, not the word “passed” from an older paragraph.

## Worktrees, branches, and authority

| Purpose | Worktree | Branch | Implementation HEAD at handoff |
| --- | --- | --- | --- |
| Harness | `D:/gt-context-plan` | `codex/context-plan-integrity` | `7e9911baf9bcd6f0771c2365b6d5e5c6edc56f67` |
| Producer | `D:/gt-context-producer` | `codex/context-plan-producer` | `efa70e52d4e85bff2364b0038ceca2306de218b0` |
| Review evidence | `D:/gt-context-review` | `codex/context-plan-review` | `86293f7122c9c4975ddf9280b8fffab7b4737717` |

Harness GitHub repository: `harneet2512/gt-harness`.
Producer push remote: `upstream`, repository `harneet2512/groundtruth`.
**Do not print producer origin or `git remote -v`: that origin contains a token.**

Approved harness base: `ce309e90613de3aad6ccfa9a8251b009cdef5bea`.
Approved producer base: `193b9d93b0650721be6120ae519ab3a1d8e8137f`.
Both ancestry checks passed earlier; repeat before delivering a successor.

**Do not edit `D:/gt-harness`: it is an unrelated dirty worktree.** It owns the
shared Git administration/hooks but is not the implementation working directory.
Producer and review worktrees were clean when this handoff was prepared.

Use `apply_patch`, preserve unrelated changes, and read the engineering-loop
skill before implementation. No subagents are authorized. No paid provider or
benchmark dispatch is authorized. No GCP actions, auth changes, credential
cleanup, secret exposure, or provider routing changes are needed.

The harness pre-commit hook performs a read-only authority check, **not pytest**.
Its post-commit hook auto-pushes this branch. Do not assume committing ran tests.

## Immediate state: resume here

1. Check provider-free CI **34443015691**, source **7e9911ba**. It was in progress
   at handoff preparation. Prior CI **34442196007**, source **f5b9c94f**, PASSED.
2. Fix real forced-interruption rehearsal **06**, now **FAILED** solely on
   `unexpected_runtime_receipt_errors`. Retry/no-submit, exact collected patch,
   reward and process cleanup checks no longer report failures. Do not merely
   replace the expected error list: trace failure-receipt accounting and worker
   finalization ordering. The actual errors are recorded below.
   Output: `D:/gt-context-proof/rehearsal-interruption-06/rehearsal.json`.
   Log: `D:/gt-context-proof/rehearsal-interruption-06-runner.log`.
   Exact source: 7e9911ba; installed harness wheel 59; actual producer 06.
3. There are **two uncommitted source-binding changes** owned by this session:
   `gt_engine/persistent_plan/baseline.py` and
   `tests/test_persistent_plan_baseline.py`. Do not discard or silently commit.
   Details and verification are below.
4. Finish those checks and inspect the full diff. Then commit a coherent verified
   increment, update the tracker, build the next exact artifact and rerun any
   invalidated release/rehearsal checks. Do not mark all remaining items done.

No background agent will continue after this session ends. Docker/CI processes
may continue, so query their actual state rather than relying on this snapshot.

## What is implemented

### Evidence, plans, and verification

- Lexical passing-command matches no longer grant arbitrary requirement proof.
  Bound checks distinguish `CHECK_PASSED` from semantic proof; missing mappings
  remain visible and cannot silently imply completion.
- Exact semantic assertions reject contradictory pass/fail or pass/unknown
  markers. Check admission binds command, test identities, source/configuration,
  environment, output and complete execution evidence.
- CheckSpec uses admissible explicit argv, not replayed shell commands. Shared
  checks are deduplicated; equivalent agent executions discharge queued work.
  Verification drains at bounded VERIFY/submission boundaries, not every edit.
- Requirement/source/example conservation, stable row IDs, full command
  rendering, explicit pending designs and interactions, and `gt-plan`
  show/revise/defer/bind-check requests are implemented.
- Original plan/context/baseline checkpoints and check definitions recover from
  validated persisted state. Historical passing evidence does not regain current
  proof authority. A corrupt original checkpoint cannot trigger a post-edit
  baseline recapture disguised as the original baseline.
- Initial baseline capture now occurs before the one initial index build, so
  baseline-written source cannot leave the initial graph silently stale.
- Baseline output is parsed completely, source mutations are not destructively
  restored, disappearing test IDs remain incomplete, and environment mismatches
  remain unknown. Baseline subprocesses use the existing descendant containment.
- Commit **24d259b5** additionally rejects missing/false output completeness
  receipts. This adds no test execution or graph build.
- The 300-step workflow limit is removed (`max_iterations=0`); wall-time and
  submission reserve remain enforced. Installed 301-query/deadline proof exists.
- Gate reasons distinguish completion evidence, mapping gaps, failed checks,
  baseline uncertainty and budget/stall escape. Acceptance is not correctness.
- Current committed/uncommitted patch state and bounded finalization reminders
  are implemented. The production harness does not auto-commit agent changes.

### Graph amendments

- Producer efa70e52 implements complete parser-result caching and one staged
  batch amendment over a certified immutable parent.
- Unchanged parser nodes, exact parser properties, target-bound assertions,
  containment and taxonomy edges are retained. Changed/deleted structures are
  replaced; parser-local cached references are remapped safely.
- The existing complete resolver and derived analysis run **once per batch**.
  Do not introduce an unproven narrow resolver or legacy per-file fallback.
- The harness uses the existing one-active/one-pending coordinator and external
  task cache. Capability is conservatively named `batch_parser_node_reuse_v1`.
- Source-suite transition tests cover unchanged/edit/add/delete/rename/import/
  inheritance/ambiguity/new target. Installed binary proof verifies retained IDs,
  immutable parent bytes, clean foreign keys and structural parity on its fixture.
- History/cochange, derived layers and FTS still recompute. Full all-consumer
  equivalence and the fixed-workload performance comparison are **not complete**.

### Audit and actual installed integration

- Persistent-plan rendering and plan-gate directives have CAS-backed exact-byte
  provider exposure audits, supporting monolithic and message-CAS requests.
  Immediate response identity is checked. Exposure is not semantic use/proof.
- Missing/tampered/late/wrong-size renderings and mismatched responses cannot
  become witnessed. Gate receipt faults preserve the directive and record the
  declared degradation stage.
- Lineage manifest self-seals are recomputed and validated. Missing or mismatched
  seals fail; a prior provenance verifier incorrectly omitted this check.
- **f5b9c94f** repairs the synthetic fixture: bootstrap calls do not consume
  action ordinals; its plan and execution share a source-bound test-file command;
  the canned agent commits its repair; task-owned `verifier.collect` writes
  `git diff --binary gt-rehearsal-base HEAD` to `artifacts/model.patch`.
  The verifier does not substitute `agent/gt-worktree.patch`.
- **21d0836b** fixes audit call reconciliation: agent turns plus separately
  reported catalog/planning calls must equal provider requests. Malformed,
  negative and boolean counters remain errors, as do arithmetic mismatches.
- **7e9911ba** fixes interruption transport retries: ordinals count returned
  actions, not HTTP requests. Retried calls remain parked at the blocked action.
  Interruption request census excludes bootstraps and permits unanswered retries;
  six-action, no-submit, terminal, receipt and process cleanup checks remain.

## Current uncommitted baseline source-binding increment

RED witness: `D:/gt-context-proof/baseline-source-red.xml` shows a test suite
mutating `data.txt` was still labeled a captured baseline.

The working change adds `source_revision` and `after_source_revision` to
BaselineResult and serialization. It uses the existing workspace snapshotter
before/after the baseline command, rejects incomplete snapshots, subtracts the
initial snapshot time from the execution allowance, and labels differing
revisions `source_changed_during_baseline`. Mutated files remain untouched.
This performs no graph rebuild and no extra test command, but adds two source
captures per baseline run. Measure their cost before claiming speed neutrality.

Wheel 60 was built successfully:
`D:/gt-context-proof/wheels-60/nano_harness-0.0.1-py3-none-any.whl`
SHA256: `287d47b984dc9711740d46f08b3083c8b96dbc125bbab8402556173e02d224f9`.

The three-suite installed test **FAILED: 14 failed, 22 passed**. Result path:
`D:/gt-context-proof/baseline-source-60.xml`.
Failures generally return `spawn_failed`, detail `ValidationError`, before
normal baseline execution. **This candidate must not be committed as working.**
First reproduce the underlying exception without the advisory catch. A concrete
lead (not yet confirmed): subtracting snapshot elapsed time changes an integer
timeout into a fractional float; the installed environment's Pydantic timeout
field may require an integer. Fix without exceeding the remaining budget, then
rerun all 36 tests and real source-mutation behavior. Ruff passed on both files.
Review compatibility carefully: the original checkpoint decoder requires exact
dataclass fields. Older checkpoints lacking these new fields will conservatively
reject; decide whether an explicit versioned, non-authoritative migration is
needed. Never backfill historical source identity with the current workspace.
Installed dependency/config/test identity conservation remains incomplete.

## Evidence index and limits

All listed local evidence is under `D:/gt-context-proof`; it is not necessarily
available to a remote session. Do not claim remote availability unless uploaded.

| Evidence | Observed result | Scope |
| --- | --- | --- |
| `producer-repin-44.xml` | 200 passed, one explicit source-only deselection, no skips | Installed graph/recovery/baseline/check tests |
| `structural-binary-06.xml` | 1 passed | Actual static CLI structural amendment fixture |
| `plan-exposure-installed-49.xml` | 153 passed, no skips | Native provider exposure and negative bindings |
| `gate-exposure-installed-50.xml` | 179 passed, no skips | Gate exposure and related boundaries |
| `rehearsal-collector-56.xml` | 63 passed + four subtests, no skips | Fixture collector/check-source binding |
| `plan-off-guard-56.xml` | 17 passed | Positive mandatory AND guard; OR/negation rejected |
| `bootstrap-audit-57.xml` | 97 passed + four subtests | Audit accounting + rehearsal tests |
| `baseline-capture-58.xml` | 36 passed | Baseline capture completeness and recovery regressions |
| `rehearsal-retry-59.xml` | 98 passed + four subtests | Retry regression + full native audit tests |
| Rehearsal 04 | Reward 1, exact official patch, bound evidence and graph refresh true | Actual installed synthetic repair |
| `rehearsal04-audit57.json` | GREEN-delivered, zero integrity issues | Re-audit of actual preserved rehearsal 04 journal |
| Rehearsal 05 | FAILED; patch gradable, process tree cleaned | Retry advanced to submit; fixed in 7e9911ba, successor required |

Rehearsal 04's original receipt remains FAILED because its original audit had
the bootstrap accounting bug. Do not rewrite it. The separate newer audit is
the evidence that the preserved journal passes the corrected auditor.
Rehearsal 06 FAILED only `unexpected_runtime_receipt_errors`. It recorded ten
transport requests, an exact gradable collected patch, four checked process
identities, no surviving descendants and no new processes. Actual runtime errors:

```text
synthetic_transport_not_paid_evidence
product_not_completed
product_provider_call_conservation_failed
product_effective_model_report_mismatch
product_event_journal_digest_mismatch
product_event_journal_conservation_failed
provider_response_count_exceeds_attempts
treatment_receipt_missing
```

Inspect `scripts/miniswe_supervisor.py`, `gt_harness/runtime_receipts.py`, final
worker/report/journal writes and the interruption oracle. Determine which errors
are truthful expected incomplete evidence and which reveal a race or incorrect
counter. Do not whitelist errors merely to turn this rehearsal green.

Canonical CI:

- 34442196007 at f5b9c94f: **PASSED**.
- 34443015691 at 7e9911ba: **in progress at preparation; query now**.
- 34441061328 at b2a53930: failed static guard test; fixed in f5b9c94f.
- 34440017657 at 509a2a20: failed new degradation-stage registry; fixed in b2a53930.
- 34439493122 at 0d1806bc and 34438826174 at 440c337b: PASSED historical checkpoints.

Canonical CI has known environment-specific skips (real retrieval graph fixtures,
sqlite_vec, and complementary graph-available/unavailable scenarios). Inventory
and exercise required complements; do not call the whole CI zero-skip proof.

## Remaining work and how to close it

These are the tracker’s 17 open items, with concrete completion criteria.

1. **Protocol coverage:** enumerate supported parsers from the actual producer;
   run positive and skip/no-test/contradiction/timeout/late-failure negatives.
   Unsupported output must abstain, never infer proof from a passing command.
2. **No unbound advancement:** exercise those observations through the real
   adapter, cursor and submit gate, not just a parser helper. Assert row IDs and
   proof/check states before and after each observation.
3. **Restart/interruption integrity:** finish rehearsal 06, then test valid,
   truncated, tampered and externally anchored journal tails. Restore definitions
   and original context without restoring stale passing authority or rerunning
   the planning call. Reject replayed already-admitted requests.
4. **Anchor/source validation:** inspect `persistent_plan/anchors.py`, bootstrap
   validation and rendering. Require anchors to belong to their recorded graph
   and source revision. Distinguish proposed symbols/tests from existing ones;
   historical anchors must be labeled historical, not silently current.
5. **Counts/exposure:** expose exact row IDs with separate existing/proposed,
   executed, passed, proven, unmapped and model-exposed counts. Keep compatibility
   aliases explicitly labeled. Test that retrieval availability is not exposure.
6. **Full CLI matrix:** extend `test_plan_check_specs.py`,
   `test_plan_check_recovery.py`, `test_original_plan_recovery.py` and native cursor
   tests for stale/multiple/replayed requests, invalid specs and deferral. Run
   actual installed `gt-plan` requests through their journal owner.
7. **Eligible history reuse:** inspect producer cochange/history owners. A HEAD-only
   key is unsafe (shallow state, replace refs, grafts, configuration/environment
   can change history). Reuse only with a sufficient verified identity; preserve
   embedding cache and LSP ownership. Do not shortcut complete derived analysis.
8. **All-consumer graph parity:** expand the existing transition corpus to every
   consumer (resolution, calls, assertions, properties, covering tests, retrieval,
   LSP, derived layers). Compare amended and fresh graphs on identical snapshots,
   parent immutability and failure fallback; assert one resolver pass per batch.
9. **Baseline identities:** finish the current source-binding increment; bind
   test/configuration/environment/dependency identities and preserve skipped and
   missing-test distinctions. A changed test definition must not masquerade as
   the original passing test; unknown dependency identity remains explicit.
10. **Integrated mutation lifecycle:** installed Linux sequences combining
    background writers, typed edits, auto-checks, incomplete captures and carried
    snapshots. Verify descendant cleanup and epoch invalidation together.
11. **Official versus recovery patches:** record both artifact identities
    separately; test committed-only, uncommitted-only, mixed and interrupted
    states. Only the task-owned collected patch is graded. Actual rehearsal now
    exercises that collector, but the complete separation matrix is still open.
12. **21-capability census:** rerun `issue_feature_matrix.py` /
    `verify_feature_matrix.py` using their actual CLI help/workflow. Audit positive
    and negative bindings for every current feature; preserve historical 19-feature
    evidence. A synthetic feature witness does not prove causal task benefit.
13. **Exact release artifact binding:** after source stabilizes, build the final
    harness wheel, verify producer/review lineage and source-to-wheel matching,
    and bind only real hashes. Rebuild after material code edits.
14. **Required installed tests:** run from installed wheels with the actual static
    producer, then audit real journals. Provide source/Git-only complementary
    tests explicitly; resolve all unexplained skips.
15. **Release/full flow:** canonical provider-free CI and normal repair plus forced
    interruption on the final artifact must pass. Synthetic transport remains
    `paid_smoke_eligible=false`; never suppress that exclusion.
16. **Performance:** stage the fixed six repository transitions and run five
    alternating baseline/candidate repetitions with equal budgets/environment.
    Record blocked/background graph time, parsed files, resolver passes, checks,
    snapshot time, peak memory and semantic parity. Do not substitute toy-fixture
    timings or extrapolate median-turn savings as measured end-to-end speedup.
17. **Final review/handoff:** inspect all changed consumers and serialized formats,
    rerun invalidated checks, commit/push scoped changes, confirm clean intended
    worktrees and exact-SHA passing CI. Only then update readiness. Paid benchmark
    efficacy still requires separate authority and actual task outcomes.

## How to verify and run the actual artifacts

Use PowerShell from `D:/gt-context-plan`. Do not use the shared Windows venv for
new harness pytest: its installed GT is old and the provenance guard rejects it.
Do not bypass that guard or upgrade the shared venv. It is usable for wheel
building and Ruff only.

```powershell
git status --short
git branch --show-current
git merge-base --is-ancestor ce309e90613de3aad6ccfa9a8251b009cdef5bea HEAD
gh run view 34443015691 --repo harneet2512/gt-harness --json status,conclusion,headSha
gh run view 34443015691 --repo harneet2512/gt-harness --log-failed |
  Select-String -Pattern 'FAILED |AssertionError|E   ' | Select-Object -Last 35
```

Installed Linux check example (wheel 60 is the uncommitted baseline candidate):

```powershell
docker run --rm --network none `
  --mount type=bind,source=D:/gt-context-proof/wheels-60,target=/wheels,readonly `
  --mount type=bind,source=D:/gt-context-proof/producer-wheels-06,target=/producer,readonly `
  --mount type=bind,source=D:/gt-context-plan/tests,target=/tests,readonly `
  --mount type=bind,source=D:/gt-context-proof,target=/proof `
  gt-linux-suite:3.12 sh -c 'python -m pip install --no-deps /producer/groundtruth_mcp-1.0.0-py3-none-any.whl /wheels/nano_harness-0.0.1-py3-none-any.whl && cd /tmp && python -m pytest /tests/test_persistent_plan_baseline.py /tests/test_gt_baseline_preservation.py /tests/test_original_plan_recovery.py -q -p no:cacheprovider --tb=short --junitxml=/proof/baseline-source-60.xml'
```

For graph/native tests additionally mount
`D:/gt-context-proof/producer-build-06/gt-index-linux-amd64` read-only at `/binary`
and set `GT_INDEX_BINARY=/binary/gt-index-linux`.
Source/Git AST or release-issuer tests need a separate real Linux checkout,
not `site-packages` as a pretend repository root.

Real journal audit (correct CLI flag is `--json`, not `--json-out`):

```text
python -m scripts.gt_audit /proof/rehearsal-repair-04/trials --json /proof/rehearsal04-audit57.json
```

Run this inside an installed Linux environment with `/proof` mounted. Keep old
receipts intact and write successor audits to distinct names.

Provider-free CI dispatch:

```powershell
gh workflow run "GT Harness: canonical provider-free product acceptance" --repo harneet2512/gt-harness --ref codex/context-plan-integrity
```

### Installed full-flow launcher

Existing local helper: `D:/gt-context-proof/run_rehearsal_01.py`.
Arguments: `--name`, `--wheel`, `--source`,
`--scenario repair|forced-interruption`. It invokes the repository's real
`scripts.gt_installed_rehearsal` through installed Harbor/Pier and the real
supervisor, indexer, model files and network-filtered task container. The only
model endpoint is the local synthetic transport; no paid call is made.

Archive the **exact committed source** into a new proof directory before each
new attempt. Match it to the actual wheel, and choose unused output/container
names. Example shape, replacing SOURCE_DIR, SHA and NAME before execution:

```powershell
docker run --rm --name NAME --pid=host --cgroupns=host -p 80:80 `
  --mount type=bind,source=/sys/fs/cgroup,target=/sys/fs/cgroup,readonly `
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock `
  --mount type=bind,source=D:/gt-context-proof,target=/run/desktop/mnt/host/d/gt-context-proof `
  --mount type=bind,source=SOURCE_DIR,target=/repo,readonly `
  --mount type=bind,source=D:/gt-dense-model-snowflake,target=/dense,readonly `
  --mount type=volume,source=gt-context-rehearsal-assets-01,target=/assets,readonly `
  gt-harness-live-smoke:runner-nftfix-dns4 `
  python /run/desktop/mnt/host/d/gt-context-proof/run_rehearsal_01.py `
  --name NAME --wheel wheels-59 --source SHA --scenario forced-interruption
```

All three host resource settings are required: host PID namespace, host cgroup
namespace, and the read-only host `/sys/fs/cgroup` mount. Without them prior
attempts failed before any model call. No privileged container is required.
Port 80 is used by the local transport; do not launch two rehearsals on it.

Reuse the verified Linux named asset volume. It contains 10,762 LSP/runtime
files, manifest SHA256
`1245a4b6d36130483260e8cc3ceb7201af9034c047ab5ec87bee58f4bf4e08cf`.
Repeated Windows-bind metadata scans were very slow; this volume avoids them.
Do not restage/download everything or touch global Docker/GCP state.

## Producer artifact identities

Pinned producer source tree: `65964ea2b5c6490ec8e119327e47ca44cb7a43ca`.
CI 34436549266 and static build 34436550584 PASSED for efa70e52.

- Wheel: `producer-wheels-06/groundtruth_mcp-1.0.0-py3-none-any.whl`
  SHA256 `cb73ff2ed55c11d7babbdc32ef55c72268e8617913cadfa94b323ea852ca0ce9`.
- Binary: `producer-build-06/gt-index-linux-amd64/gt-index-linux`
  SHA256 `9e2973ea1060fc2c9236b0e81ea39ebb957a0013903d45517e01d4b0b25ef91c`.
- Build info: sibling `gt-index-linux.build-info.json`
  SHA256 `39d00944df6a6ad7213140d9494d606a5f082d15989f3a85d9ce57e25faa44cd`.
- Review packet: `inbox/HAR-83/har83-context-plan-producer-efa70e52.json`
  SHA256 `13a9333a50e57ceff136107a37d84ef5a067feb9be2d853b6d16c5e90c81bba4`.

Use the existing provenance verifier and actual manifest; never declare a new
capability or change a pin before its producer source/build/review evidence exists.
