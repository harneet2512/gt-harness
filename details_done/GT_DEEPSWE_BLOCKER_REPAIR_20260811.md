# DeepSWE blocker repair — 2026-08-11

## Scope

This repair addresses only defects reproduced in the DeepSWE ten-task smoke
(`31550154123`). That smoke used `openrouter/xiaomi/mimo-v2.5-pro`; it is not
the intended DeepSeek V4 measurement. The active task workflow now defaults to
the established bare `deepseek-v4-flash` model ID. The upstream repository
does not publish a `v1.1` tag, so the workflow uses immutable main commit
`435ee89ec2f2e2289f33b0da4f992f0b7b7266b9`, whose 113 task IDs match the
published v1.1 catalog. The public website's v1.1 artifacts are therefore
represented by a reproducible task snapshot, not by a fabricated tag.

## Reproduced defects and fixes

### 1. Large-workspace manifest falsely degraded the source mirror

The manifest command used `find | sort | head -n 50001` under `set -o
pipefail`. `head` closed the pipe after the bound, so `find` could terminate
with status 141. The sensor treated that non-zero status as an unhealthy
snapshot. This caused `arktype-json-schema-refs-dependencies` to start without
a usable mirror and invalidated the later refresh for
`fd-deterministic-multi-key-sorting`.

The command now uses `awk 'NR <= 50001'`. `awk` still bounds the emitted rows
but consumes the complete sorted stream, so `find` is not killed by SIGPIPE.

### 2. Bounded source text falsely invalidated a usable graph

The hybrid repository builder recorded `chunk_character_limit` whenever a
graph span exceeded the bounded retrieval-text budget. It then set
`complete=False` for every reason, causing the agent to refuse construction of
the hybrid retriever even when the graph, source revision, and file were
valid. The DeepSWE smoke consequently showed graph-passed tasks with zero
retrieval candidates.

`chunk_character_limit` is now a non-fatal corpus-bound reason. The returned
document remains explicitly marked with `bounded_source_span` provenance and
is still bounded. All substrate failures (missing/invalid graph, source
read failure, unsafe path, incomplete links, etc.) remain fail-closed.

### 3. Initial graph indexing was aborted at 15 seconds

`MiniSweCentralAgent._start_repository_session` wrapped the initial refresh in
`asyncio.wait_for(..., timeout=15)`. The smoke measured initial indexing at
approximately 15.6s and 16.2s for two otherwise valid repositories, so the
host wrapper—not the indexer—aborted them.

The timeout is now a constructor setting,
`repository_initial_index_timeout_sec`, defaulting to 60 seconds and clamped
to at least one second. The DeepSWE workflow sets the same value explicitly
(`--ak repository_initial_index_timeout_sec=60`) and the value is written to
the component configuration receipt.

## Verification

Focused RED-to-GREEN checks:

```text
python -m pytest tests/test_gt_central_runtime.py::test_large_manifest_bound_does_not_turn_sort_limit_into_sigpipe_failure tests/test_hybrid_repository.py tests/test_hybrid_retrieval.py tests/test_gt_central_agent.py::test_deepswe_workflow_sets_a_nontrivial_initial_index_timeout -q
40 passed
```

Static checks:

```text
ruff check <all changed Python files>       PASS
python -m py_compile <all changed files>    PASS
git diff --check                            PASS
```

The broader GT subsystem suite passed all non-census tests. Three census
assertions remain red locally because the checked-in Windows binary does not
contain the registered `objective_c` parser. The provider-free workflow builds
`vendor/gt-index-src` from source on its runner; this local binary mismatch is
intentionally not hidden by changing the census gate.

The source-built verification then passed on the repaired commit:

```text
workflow: 31554230078
commit:   7bd17564d3c3832a7bb29275b7bde07e041c1475
result:   success
```

Its log proves the parser-complete repository substrate, `READY`, all 17
producer/consumer/timing/accounting census gates, strict lifecycle tests,
`SMOKE_APPROVED`, and static checks. The receipt and log are retained at
`artifacts/deepswe_provider_free_31554230078/` and
`artifacts/deepswe_provider_free_31554230078.log`.

After switching the task snapshot to the v1.1 catalog-compatible commit, the
same source-built gate was rerun on final commit `3b9b1150e2fa798f7b08582702821145c956cc76`:

```text
workflow: 31554933728
result:   success
READY:    yes
SMOKE_APPROVED: yes
```

The final log is retained at
`artifacts/deepswe_provider_free_31554933728.log`.

## DeepSWE data captured locally

Fetched with `curl.exe` from `https://deepswe.datacurve.ai/`:

```text
artifacts/deepswe_leaderboard/index.html
artifacts/deepswe_leaderboard/leaderboard-live-v1.1.json
artifacts/deepswe_leaderboard/data-v1.1.html
artifacts/deepswe_leaderboard/tasks-v1.1.html
artifacts/deepswe_leaderboard/trials-v1.1.html
artifacts/deepswe_leaderboard/tasks-v1.1.json
artifacts/deepswe_leaderboard/local-smoke-task-comparison.json
artifacts/deepswe_leaderboard/local-smoke-task-comparison.csv
```

The v1.1 artifact reports 113 tasks, 91 repositories, five languages, and a
generation timestamp of 2026-08-07. The ten-task local smoke maps to ten
catalog tasks across Go (2), Python (2), TypeScript (2), Rust (2), and
JavaScript (2). Its treatment reward was 0/10; this is diagnostic evidence and
is not a leaderboard comparison. The public v1.1 artifact's DeepSeek V4 Flash
reference row is `mini-swe-agent`, max effort, 241/452 passed attempts,
pass@1 `0.5331858407`, pass@4 `0.8053097345`, across 113 tasks and four runs.
That is an external aggregate reference, not a GT-on row for this smoke.

The active DeepSWE workflow is pinned to that immutable snapshot in both
checkout steps, verifies the commit, and requires exactly 113 task manifests.
Unrelated Terminal-Bench release comments mentioning `v1.1.0` are not
DeepSWE task inputs and were not changed.

## v1.0.0 versus the published v1.1 protocol

The task-ID equality is real but is not the release identity.  The upstream
repository exposes tag `v1.0.0` at
`79a508a908998690c6ceb773ae2dbcc23f55e434e` (peeled commit
`c33fa70e68d11d85f9e58abcd5d78643705e916e`) and no `v1.1` tag.  Its current
`main` commit `435ee89ec2f2e2289f33b0da4f992f0b7b7266b9` has the same 113 task
IDs as the public v1.1 catalog, but the task execution contract is materially
different:

| Boundary | v1.0.0 | current main / published v1.1 |
|---|---|---|
| task schema | `1.1` | `1.3` |
| grading topology | agent and verifier shared task environment | separate no-network verifier environment |
| agent completion | changes left in the agent workspace | agent commits work; verifier consumes a collected patch |
| artifact contract | no declared patch artifact | `model.patch` from a `[[verifier.collect]]` hook |
| verifier | task `test.sh`/`test.patch` flow | `grader.py prepare/grade`, CTRF report, `reward.json`, `run.log`, reports |
| isolation | `allow_internet=false` task setting | explicit agent/verifier network and environment modes plus resource limits |
| image | base `...:<ext_id>` image | `...:<ext_id>-v1.1` image |
| task instruction | no mandatory branch/commit instruction | requires a new branch from main and a commit |

These differences are visible in the version-controlled task TOMLs,
environment Dockerfiles, instructions, and test harnesses; they are not an
inference from the leaderboard.  Therefore a direct `harbor run` against the
main snapshot was not a valid v1.1 execution even though its IDs matched.

The workflow is now corrected to install pinned `datacurve-pier==0.3.1` and
invoke `pier run --include-task-name`.  Pier is the Harbor-compatible runner
that implements the v1.1 separate-verifier and collect-hook lifecycle.  Its
only integration point is the thin
`eval.pier_gt_adapter:PierMiniSweCentralAgent` boundary adapter; the central
runtime remains runner-neutral and is still the single GT implementation.  The
workflow keeps the exact GT receipt, provider-delivery audit, and merged
outcome artifacts.

The contract is guarded by
`test_deepswe_workflow_uses_pier_v11_verifier_protocol`, which rejects a direct
Harbor invocation and requires the pinned Pier version and task selector.

The exact-head provider-free certification was rerun after this workflow
change:

```text
workflow: 31555714691
commit:   9805b81bce28f73dfc96af554f13141589cbc9f1
result:   success
READY:    yes
SMOKE_APPROVED: yes
provider calls: 0
```

The downloaded log and receipt are retained at
`artifacts/deepswe_provider_free_31555714691/` and
`artifacts/deepswe_provider_free_31555714691/run.log`.

## First Pier smoke retry and compatibility repair

The first v1.1/Pier dispatch was `31555872660`. It is invalid as benchmark
evidence: all ten tasks stopped before environment creation because the
provider preflight still used a bare model without the gateway route. The
failure was `litellm.BadRequestError: LLM Provider NOT provided`, not a task
failure.

After the gateway fix, dispatch `31556237120` passed provider preflight and
reached Pier, but all ten tasks stopped before execution with
`AttributeError: 'MiniSweCentralAgent' object has no attribute 'install_spec'`.
Pier 0.3.1 requires `install_spec()` and `network_allowlist()` on custom
agents. The external `eval.pier_gt_adapter` supplies both: it declares no
in-container install and returns an empty Pier allowlist because provider calls
are host-owned. The Harbor-only central runtime remains completely free of
Pier imports. The focused isolation test is green; the exact source-built gate
must be rerun on this commit before another paid dispatch.

The next dispatch, `31556650765`, passed both provider routing and the install/
allowlist boundary, then exposed a final runner-model boundary: Pier rejected
Harbor's `AgentInfo` instance during result construction. The external adapter
now serializes its identity with Pier's equivalent `AgentInfo`/`ModelInfo`;
the central agent retains Harbor's native type. That fix is covered by a
focused isolation test and the direct Pier import witness.

## Remaining gate

The new runner contract and the exact source-built provider-free gate are now
green.  The authorized repaired ten-task DeepSWE smoke is now dispatchable from
commit `9805b81`, using DeepSeek V4 Flash, the v1.1 main snapshot, and Pier's
separate-verifier lifecycle.  Its result must still be treated as a diagnostic
GT-on smoke until a matched baseline and receipt/outcome audit are complete.
