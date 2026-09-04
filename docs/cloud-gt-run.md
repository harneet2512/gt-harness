# Cloud GT run — GroundTruth indexing on the Codespaces deployment

Date: 2026-09-04. Branch `cloud/internal-harness`. Codespace
`gt-cloud-agent-wvrqp4rqpjp42gvp7` (4 cpu / 15 GB, cgroup v2), stack from
`cloud/docker-compose.yml`. Model `nvidia/nemotron-3-super-120b-a12b:free`
via OpenRouter. All calls through the public URL with a Bearer JWT.

## 1. Certified producer fails on an arbitrary repo

Session on `https://github.com/pallets/click@main`, `gt_mode=advisory`, image
built with the vendored certified binary (`0aadb1b9`, build
2026-09-02T06:51:07Z).

Lifecycle: `creating → cloning → indexing → gt_unavailable`
(`RuntimeError: index status build_failed: nonzero_exit`). Index receipt
`.gt_state/<id>/index-failure-resource.json`, exit code 1, `stderr_tail`:

```
Pass 1: discovering files ... Found 131 source files
Pass 2: parsing 131 files (2 workers)... Parsed 131/131 files in 1.029s
  Inserted 1361 nodes ... Extracted 1361 definitions, 580 imports
Pass 3: resolving 5587 call references... Resolved 2757/5587 calls in 325ms
... attach graph-native resolution evidence: callsite [redacted] candidate 0:
    variable_type_flow requires typed source or propagation facts
```

Source: `gt-index/internal/store/sqlite.go:390` (`validateCandidateDerivation`)
reached from `AttachResolutionGraphTx`; `cmd/gt-index/main.go:947` turns the
error into `abortStagedBuild`. The invariant has no environment gate. The
session still went `idle` with an import-only graph (166 nodes / 156 edges,
`gt: false`).

## 2. Fix: build the producer from source with a one-hunk patch

`cloud/producer/0001-skip-invalid-candidates.patch` (against the same commit)
logs and skips a derivation-invalid candidate instead of returning the error.
`cloud/Dockerfile` stage 1 fetches the pinned commit, applies the patch and
builds with CI's exact recipe (static CGO, `sqlite_fts5`, provenance ldflags),
stamping `main.commitSHA=0aadb1b9…+cloud.1` so the binary can never be
mistaken for the certified one. The certified benchmark producer is unchanged.

`gt-index -build-info` in the running image:

```
{"schema":"gt-index.build.v1","complete":true,
 "git_commit":"0aadb1b9111f70f3c6b8874e1b8eff927397d22b+cloud.1",
 "build_time_utc":"2026-09-04T22:45:44Z","go_toolchain":"go1.22.5",
 "build_tags":"sqlite_fts5","graph_schema_version":"v15.2-trust-tier", ...}
```

Harness acceptance: `_binary_certification()` only fails closed when
`GT_PRODUCER_ARTIFACT` is set; `BenchmarkGraphRequired` needs
`GT_TASK_ID` + `GT_PRODUCT_SOURCE_SHA`. None are set for the cloud service, so
no harness change was needed (documented in `cloud/.env.example`).

## 3. Result with the patched producer

| repo | gt_status | `/graph` gt | nodes | edges | kinds |
|---|---|---|---|---|---|
| pallets/click@main | ready | true | 166 | 550 | gt_call 285, gt_import 100, gt_ref 9, import 156 |
| psf/requests@main | ready | true | 130 | 229 | gt_call 82, gt_import 27, gt_ref 5, import 115 |

click's `index-resource.json` `stderr_tail`:
`[WARN] 27 resolution candidates skipped as abstentions` … `Done in 16.758s /
Files: 131 / Nodes: 62839 / Edges: 79216`, exit code 0. The first of those 27
candidates previously aborted the whole graph.

## 4. A real turn on the GT-ready session

Message: "Which module defines the Command class and what calls its invoke
method? Answer briefly." Reply (18 steps, one turn): "The Command class is
defined in `src/click/core.py`. Its `invoke` method is called by
`Command.main` (and indirectly via `Command.__call__`)." — correct.

Transcript (`.gt_state/transcript.json`) shows GT wired and firing:
`gt.evidence_artifact.v1` ×2, `gt.interception_decision.v1` ×2,
`exact_literal_search` ×13, tool `groundtruth`, `gt_typed_action: true`.
**Both GT actions abstained** (`returncode 2`, `typed evidence incomplete`,
`matches: []`, reason codes `SEMANTICS_NOT_EXACT / COVERAGE_NOT_COMPLETE /
EVIDENCE_HAS_OMISSIONS`) for `exact_literal_search "class Command"` scoped to
`src/click/**`; the agent answered from plain file reads. Filed as HAR-85.

Both sessions were closed afterwards (`/close`).
