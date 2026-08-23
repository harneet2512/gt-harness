# GroundTruth Canonical Architecture

Status: canonical prerelease architecture. The last certified implementation is `3e2185d3f4ba0a228c740ab2a6d23a287cfc5380`; post-certification context-compiler changes require a new exact-SHA certification receipt.

## Product boundary

GroundTruth is a model-agnostic **benchmarking product**. The sole product executable is `gt-harness` (`gt_harness.cli:main`), and its primary product path is `gt-harness run`: the common coding-agent scaffold plus an auditable bare or GroundTruth treatment. Its supported production surfaces are:

- `gt-harness doctor`: verifies Python, Git, Go, and the content-addressed source build of `gt-index`.
- `gt-harness graph build|status|query`: operates the canonical repository graph.
- `gt-harness mcp`: optional interoperability adapter exposing the same canonical graph through stdio, SSE, or streamable HTTP. MCP is not the product identity and is not a benchmark substitute.
- `gt-harness run --treatment bare|groundtruth`: runs one common model-agnostic coding-agent scaffold. The two arms use the same prompt, tools, limits, provider adapter, and action semantics. The GroundTruth arm may only add bounded deterministic evidence and record observations.
- `gt-harness record-outcome|record-harbor-outcomes`: derives outcomes from independently graded evaluator receipts and content-binds them to run receipts.
- `gt-harness compare`: performs a provider-free, strictly paired comparison of completed evaluator receipts and rejects scaffold, repository, or treatment-delivery mismatches.
- `gt-harness certify`: fail-closed validation of the Linux product campaign, exact implementation SHA, clean checkout, provider-free status, required gate receipts, graph truth, lifecycle, language, MCP, and failure-campaign minima.

`certify` never runs a benchmark and never infers success from prose. It returns `NOT_CERTIFIED` for a missing, malformed, stale, wrong-SHA, dirty-tree, provider-using, or incomplete evidence bundle.

## Repository-to-agent execution path

```text
repository working tree
  -> RepositoryGraphService.compute_repository_identity
     (Git commit + hashes of actual graph inputs, including dirty/untracked state)
  -> checked-in vendor/gt-index-src
  -> content-addressed local Go build (gt_harness.indexer_setup)
  -> Git-authoritative discovery (tracked + non-ignored files)
  -> tree-sitter parse and deterministic relationship resolution
     (one explicit File anchor per parsed source; import-provenance binding;
      named re-export targets; explicit parser-recovery limitations)
  -> SQLite candidate graph + metadata/discovery receipt
  -> atomic graph/manifest publication
  -> .groundtruth/graph.db + graph-receipt.json
  -> RepositoryGraphService readiness and identity validation
  -> bounded HybridRepository projection from exact checkout bytes
  -> HybridRetriever
     (exact identity + BM25 + lexical + certified structural,
      deterministic reciprocal-rank fusion and budgeted selection)
  -> RepositoryContextCompiler
     (production-path ranking, exact symbol anchors, certified direct relationships,
      process/change/test/validation projection, uncertainty and evidence ledger)
  -> bounded SemanticGraph projection over the selected exact source spans
     (Python AST value/return/control flow, uniquely bound local call arguments,
      explicit tensor-shape assertions, and versioned library contracts;
      source-revision receipt, no model-authored evidence, no guessed method binding)
  -> compact gt.agent_context.v3 provider packet
  -> GroundTruthTreatment in gt-harness run
     (or direct CLI query / optional MCP adapter)
  -> pinned Mini-SWE-Agent 2.2.8 action loop
     (ordered action batches continue unless failed validation precedes submit,
      a must-be-absent precondition is invalidated, or revision change is unexplained)
```

No provider credential or provider call is required to build, validate, update, persist, or query the graph.

Before the first provider request, the GroundTruth treatment must have a
query-ready exact-revision graph and at least one revision-bound evidence
claim. Missing graph, compiler failure, initial abstention, or an impossible
context budget produces `NOT_APPLICABLE` or `FAILED` and a zero-provider-call
run receipt. It never silently becomes the bare arm.

After tool actions, only real repository paths and recognizable diagnostics
dirty the context state. A dirty checkout makes the graph `STALE`; the next
provider boundary rebuilds atomically and recompiles against the new source
revision. Ordinary output containing words such as `error` does not trigger a
diagnostic frame, and unrelated relationships from an anchored file are not
admitted unless their exact endpoint symbol is anchored.

## Readiness invariant

Graph-derived evidence is available only when all of the following are true:

```text
current commit == receipt commit
current graph-input revision == receipt source revision
current builder == receipt builder
SQLite checksum and quick_check are valid
every graph component reports success
discovery + skipped == repository files seen by the indexer
parsed + parse failures == files attempted
file hashes + hash failures == files attempted
status in {READY, READY_WITH_DECLARED_LIMITATIONS}
query_ready == true
```

The explicit non-ready states are `ABSENT`, `BUILDING`, `DEGRADED`, `FAILED`, and `STALE`. A stale or partial graph cannot be queried through the canonical service.

The canonical query modes are `definition`, `search`, `callers`, `callees`,
`imports`, `importers`, `reexports`, `exporters`, `implementations`, `subclasses`,
`references`, `impact`, and `tests`. Hierarchy queries resolve only type-like
anchors, so a same-named constructor cannot make a class query ambiguous.

## Lifecycle

- Cold build publishes a candidate database and receipt atomically.
- Warm start verifies Git HEAD/status and scans the complete tracked plus non-ignored graph-input inventory. Files whose filesystem fingerprints and Git state are unchanged reuse stored content hashes; changed candidates are rehashed. The database checksum and SQLite integrity seal are reused only while its path, size, modification time, and expected digest remain unchanged in that process.
- Additions, modifications, deletions, renames, and commit changes currently use atomic full publication. The file-keyed indexer is retained but is not canonical because parity for whole-repository relationship passes has not been proven.
- An interrupted build leaves `BUILDING` and cannot be queried; the next build repairs it.
- Publication is serialized with a cross-process lock and journaled rollback.

## Canonical and non-canonical code

| Area | Classification | Disposition |
|---|---|---|
| `gt_harness/` | PRODUCTION | Canonical benchmarking CLI, treatment, comparison/certification, optional MCP adapter, and source provisioning |
| `gt_harness/miniswe_runner.py` | PRODUCTION | Sole in-process coding-agent runner; pinned Mini-SWE-Agent loop with the GT treatment seam and credential-isolated repository shell |
| `gt_engine/semantic_graph.py` | PRODUCTION | Deterministic, source-receipted semantic facts admitted by the context compiler; unsupported/ambiguous cases abstain or declare limitations |
| `gt_engine/batch_continuation.py` | PRODUCTION | Dependency-aware Mini-SWE batch continuation policy; never cancels later mutations merely because an earlier ordered mutation changed the checkout |
| `gt_engine/repository_graph_service.py` | PRODUCTION | Sole graph readiness/lifecycle/query boundary |
| `vendor/gt-index-src/` | PRODUCTION | Source-built graph writer; upstream provenance plus audited overlay |
| `src/groundtruth/` | PRODUCTION SUPPORT / MIGRATION SOURCE | First-party GT capabilities retained; only code reached from the canonical service is production until migration finishes |
| `eval/miniswe_agent.py` | BENCHMARK | Official Harbor Mini-SWE adapter and result/trajectory boundary |
| `eval/gt_central_agent.py` | BENCHMARK / RESEARCH | Mini-SWE-compatible deterministic treatment laboratory; not a separate agent scaffold |
| `gt_engine/indexer.py:refresh_index_files`, `gt_engine/bridge.py`, central runtime and historical control layers | LEGACY / RESEARCH pending parity audit | File-keyed refresh and older control paths are not the official CLI/MCP lifecycle; do not delete until consumers and unique behavior are classified |
| `.github/workflows/tb2_miniswe_*.yml` | BENCHMARK | Mini-SWE-only Harbor paths; fail-closed authorization, source install, exact treatment receipts, and hash-bound grader outcomes |
| historical central workflows and `gt_finalstand/` | LEGACY evidence | Cannot certify the prerelease and are not authorized paid treatment paths |
| generated head-to-head outputs, historical run artifacts, broken `artifact_deepswe` configs | DELETE (completed) | Removed after zero production consumers and missing referenced modules were verified; frozen tag retains recovery history |
| vendored wheel and prebuilt Linux binary | DELETE (completed) | Removed; frozen tag retains recovery history |

There is one canonical graph database: `.groundtruth/graph.db`. The separate historical `index.db`/SymbolStore, central runner, and legacy MCP servers are not production-authoritative. `gt_engine.context_composer` remains only for the optional compatibility adapter; it is not the `gt-harness run` treatment compiler.
