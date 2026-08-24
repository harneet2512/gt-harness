# GroundTruth Canonical Architecture

Status: canonical prerelease architecture. The current implementation is
`2bab25973fd1e4e90372aac30231bbbe3009b863`; its localization-v5 delta receipt is
`audit/receipts/localization-v5-2bab259.json`. The preceding complete clean-Linux
campaign remains under `audit/receipts/codespaces-8931876/`; all applicable
provider-free gates were rerun on the current implementation.

## Product boundary

GroundTruth is a model-agnostic **benchmarking product**. The sole product executable is `gt-harness` (`gt_harness.cli:main`), and its primary product path is `gt-harness run`: the common coding-agent scaffold plus an auditable bare or GroundTruth treatment. Its supported production surfaces are:

- `gt-harness doctor`: verifies Python, Git, Go, and the content-addressed source build of `gt-index`.
- `gt-harness graph build|status|query`: operates the canonical repository graph.
- `gt-harness run`: the only coding-agent product boundary. It runs the pinned
  Mini-SWE-Agent 2.2.8 loop and attaches deterministic GT evidence to tool
  observations. The former product MCP adapter was removed because GT Harness
  is a benchmark treatment, not an MCP product.
- `gt-harness run --treatment bare|groundtruth`: runs one common model-agnostic coding-agent scaffold. The two arms use the same prompt, tools, limits, provider adapter, and action semantics. The GroundTruth arm may only add bounded deterministic evidence and record observations.
- `gt-harness record-outcome|record-harbor-outcomes`: derives outcomes from independently graded evaluator receipts and content-binds them to run receipts.
- `gt-harness compare`: performs a provider-free, strictly paired comparison of completed evaluator receipts and rejects scaffold, repository, or treatment-delivery mismatches.
- `gt-harness certify`: fail-closed validation of the Linux product campaign, exact implementation SHA, clean checkout, provider-free status, required gate receipts, graph truth, lifecycle, language, Mini-SWE product E2E, and failure-campaign minima.

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
  -> persistent repository-wide Snowflake ONNX dense index
     (exact source/model/checksum identity; release mode fails closed)
  -> bounded HybridRepository projection from exact checkout bytes
     (FTS/BM25 candidates plus direct read-only SQLite identity seeding for
      syntax-marked owners and existing API prefixes)
  -> HybridRetriever + dense/sparse file fusion
     (exact identity + BM25 + lexical + certified structural + dense ranks,
      deterministic reciprocal-rank fusion and budgeted selection; dense
      candidates are inspection hints and never edit authority)
  -> RepositoryContextCompiler
     (task facets; owner-scoped exact symbols; bounded set cover; distinct EDIT,
      PUBLIC_SURFACE, INTEGRATION, VALIDATION, and UNCERTAIN roles; production-path
      ranking; certified direct relationships; persisted bounded CALLS processes;
      exact impact/change/test projections; uncertainty and evidence ledger)
  -> bounded SemanticGraph projection over the selected exact source spans
     (Python AST value/return/control flow, uniquely bound local call arguments,
      explicit tensor-shape assertions, and versioned library contracts;
      source-revision receipt, no model-authored evidence, no guessed method binding)
  -> compact gt.agent_context.v5 provider packet
  -> GroundTruthTreatment in gt-harness run
  -> pinned Mini-SWE-Agent 2.2.8 action loop
     (initial context accompanies the task; subsequent context is appended to
      the exact tool observation that triggered it; raw output is preserved;
      model and shell operations are bounded by actual remaining GT time)
  -> Harbor adapter supervision
     (task.toml remains the outer authority; GT reserves a 90-second shutdown gap;
      process exit/cancellation terminalizes only a durable RUNNING checkpoint)
```

No provider credential or provider call is required to build, validate, update, persist, or query the graph.

Before the first provider request, the GroundTruth treatment must have a
query-ready exact-revision graph and at least one revision-bound evidence
claim. Missing graph, compiler failure, initial abstention, or an impossible
context budget produces `NOT_APPLICABLE` or `FAILED` and a zero-provider-call
run receipt. It never silently becomes the bare arm.

After tool actions, only real repository paths, repository changes, and
recognizable diagnostics dirty the context state. The treatment rebuilds the
stale graph before augmenting that same observation; `before_model_call` is an
integrity barrier and never performs late context injection. Ordinary output
containing words such as `error` does not trigger a diagnostic frame, and
unrelated relationships from an anchored file are not admitted unless their
exact endpoint symbol is anchored.

Localization lifecycle states are content-attributable. Reading an advised file
can move a feature to `FOLLOWED`; only a content change to an attributed path can
move it to `EDITED`; and only applicable passing validation after such a change
can move it to `VALIDATED`. Unrelated dirty files and read-only test runs cannot
manufacture progress.

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
| `gt_harness/` | PRODUCTION | Canonical benchmarking CLI, Mini-SWE treatment, comparison/certification, and source provisioning; no product MCP |
| `gt_harness/miniswe_runner.py` | PRODUCTION | Sole in-process coding-agent runner; pinned Mini-SWE-Agent loop with the GT treatment seam and credential-isolated repository shell |
| `gt_engine/semantic_graph.py` | PRODUCTION | Deterministic, source-receipted semantic facts admitted by the context compiler; unsupported/ambiguous cases abstain or declare limitations |
| `gt_engine/batch_continuation.py` | PRODUCTION | Dependency-aware Mini-SWE batch continuation policy; never cancels later mutations merely because an earlier ordered mutation changed the checkout |
| `gt_engine/repository_graph_service.py` | PRODUCTION | Sole graph readiness/lifecycle/query boundary |
| `vendor/gt-index-src/` | PRODUCTION | Source-built graph writer; upstream provenance plus audited overlay |
| `src/groundtruth/` | PRODUCTION SUPPORT / MIGRATION SOURCE | First-party GT capabilities retained; only code reached from the canonical service is production until migration finishes |
| `eval/harbor_gt_harness_adapter.py` | PRODUCTION BENCHMARK ADAPTER | Canonical Harbor adapter; uploads the exact checkout and pinned dense model, installs Mini-SWE 2.2.8, and receipts the product SHA |
| `eval/miniswe_agent.py` | LEGACY BENCHMARK | Historical Harbor integration; not used by the canonical final workflow |
| `eval/gt_central_agent.py` | BENCHMARK / RESEARCH | Mini-SWE-compatible deterministic treatment laboratory; not a separate agent scaffold |
| `gt_engine/indexer.py:refresh_index_files`, `gt_engine/bridge.py`, central runtime and historical control layers | LEGACY / RESEARCH pending parity audit | File-keyed refresh and older control paths are not the official product lifecycle; retain as research evidence until unique behavior is classified |
| `.github/workflows/tb2_miniswe_product.yml` | PRODUCTION BENCHMARK WORKFLOW | Sole final paid-run path; Mini-SWE 2.2.8, exact repair20 set, exact source SHA, one attempt, full trajectories, and hybrid-required dense retrieval |
| other `.github/workflows/tb2_miniswe_*.yml` | LEGACY BENCHMARK EVIDENCE | Not authorized for the final run and cannot certify the prerelease |
| historical central workflows and `gt_finalstand/` | LEGACY evidence | Cannot certify the prerelease and are not authorized paid treatment paths |
| generated head-to-head outputs, historical run artifacts, broken `artifact_deepswe` configs | DELETE (completed) | Removed after zero production consumers and missing referenced modules were verified; frozen tag retains recovery history |
| vendored wheel and prebuilt Linux binary | DELETE (completed) | Removed; frozen tag retains recovery history |

There is one canonical graph database: `.groundtruth/graph.db`. The separate historical `index.db`/SymbolStore, central runner, and legacy MCP research sources are not production-authoritative. `gt_engine.context_composer` is historical compatibility code; it is not the `gt-harness run` treatment compiler.
