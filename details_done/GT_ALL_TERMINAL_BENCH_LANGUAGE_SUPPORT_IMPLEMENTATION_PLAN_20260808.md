# GT complete Terminal-Bench language support plan

Date: 2026-08-08  
Branch: `inline-engine`  
Implementation base: `6778c4b` (verification commit pending)

## Implementation status (2026-08-08)

The original suffix inventory was incomplete and its `.v = Verilog`
assumption was wrong. The official pinned 89-task tree uses `.v` for Coq, while
the existing general registry used the same suffix for Verilog. The
implementation now resolves language from path plus bounded content, fails
closed on ambiguous dialects, and extends the native graph to every source
family observed in the checked-in benchmark contract. Local provider-free
proof and archived policy replay are complete; exact-pushed-commit CI remains
the release authority. No paid smoke or 89-task run has been authorized from
this change.

## Objective

GT must not silently ignore a code-like file in Terminal-Bench. Every task
must end in one of two truthful states:

1. **Certified graph-supported:** source revision, workspace capture, syntax
   validation, parser, graph refresh, retrieval, and delivery are all proven
   for that language; or
2. **Explicitly unsupported:** the file is recognized as validation-relevant,
   source coverage records it, and the graph gate blocks promotion until a
   certified implementation exists.

The final goal is state 1 for every language that can affect a TB task. An
extension-list entry alone is not support.

## Inventory findings

The Terminal-Bench 2 repository visibly contains source-like files in:

- Python (`.py`), JavaScript (`.js`), C (`.c`, `.h`), C++ (`.cpp`), Rust
  (`.rs`), COBOL (`.cbl`), Scheme (`.scm`), shell (`.sh`), and OCaml (`.ml`);
- R (`.r`), Coq (`.v`), Redcode (`.red`), POV-Ray (`.pov`),
  Stan (`.stan`), SPARQL (`.sparql`), Turtle (`.ttl`), LaTeX (`.tex`), Vim
  (`.vim`), Nginx (`.conf`), and G-code (`.gcode`);
- exact build/control names including Makefile, Dockerfile/Containerfile,
  CMakeLists.txt, Meson, and Autotools files, plus extensionless interpreters;
- structured/configuration formats such as Markdown, TOML, YAML, JSON, SQL,
  XML, HTML, protobuf, and data artifacts.

Verilog is not a live witness in the pinned 89-task tree; it is retained as a
general capability and as the collision oracle for content-aware `.v`
resolution. The vendored `gt-index` retains native Tree-sitter R and Verilog and bounded
Redcode/POV-Ray adapters. It now adds conservative structural adapters for Coq,
Stan, SPARQL, Turtle, LaTeX, Vim, Nginx, G-code, Make, Dockerfile, CMake,
Meson, and Autotools. The Python and Go resolvers both disambiguate `.v` from
bounded declarations. The adapters emit only fixture-proven structure and
never infer a general-purpose call graph from arbitrary text.

The official Tree-sitter parser inventory lists a Verilog grammar, and the
Posit/R community publishes a Tree-sitter R grammar. I found no comparable
maintained Redcode or POV-Ray grammar in that inventory. This motivated the
existing native R/Verilog integrations. Languages without a selected pinned
grammar use deliberately bounded adapters with hand-checked oracles;
unsupported constructs abstain instead of being guessed.

## Non-negotiable support contract

For every language capability, one registry row must drive:

| Surface | Required proof |
| --- | --- |
| Source classification | Authored paths advance source revision; derived/data artifacts do not |
| Capture | Python-independent bounded source capture works in task images |
| Syntax | A task-image-available probe is explicit, bounded, and fail-open |
| Parser | Pinned grammar and ABI are part of the vendored build |
| Graph | Definitions/references/call edges have language fixtures |
| Refresh | Incremental source edits update graph at exact source revision |
| Retrieval | Task-conditioned facts have concrete path/line/symbol anchors |
| Delivery | First eligible provider request, deduplicated and source-bound |
| Failure | Missing parser/coverage is `unsupported_language` or `incomplete_source_coverage`, never `EMPTY` |

The model loop must remain unchanged for unsupported languages: ordinary
Mini-SWE execution continues fail-open, but the paid treatment merge fails
closed. No task may be counted as valid GT evidence with a dead graph.

## Phase 0 — Inventory and gate (implemented in `4fdda8e`)

1. Extract every file suffix from the pinned Terminal-Bench task tree.
2. Classify each suffix as source, structured text, or derived artifact.
3. Compare the suffix set with `language_registry.py` and vendored specs.
4. Add explicit validation-relevant rows for `.r`, `.v`, `.red`, and `.pov`.
5. Add regression tests proving those suffixes are counted as source and fail
   closed instead of returning `NO_SUPPORTED_SOURCE`.
6. Keep the existing portable `base64` source-capture fallback so parser input
   is available even when a task image lacks Python.

Exit gate: source coverage reports every code-like suffix, and no unknown code
suffix can be silently omitted.

## Phase 1 — Parser capability manifest

Create one generated, checked-in manifest from the vendored Go specs containing:

- canonical language name and aliases;
- suffixes and case policy;
- grammar repository and pinned commit;
- Tree-sitter ABI and Go binding version;
- parser semantic level: `syntax_only`, `definitions`, `references`, `calls`;
- fixture paths and expected node/edge counts;
- supported syntax probe;
- known limitations and fail-closed rules.

Add a provider-free parity test that compares this manifest with the Python
registry and fails if a registry language has no actual binary spec or if a
binary spec is not represented in the host registry. Shell's canonical graph
name (`bash`) must be represented as an explicit host alias (`shell`) rather
than relying on an accidental string mismatch.

## Phase 2 — R and Verilog native parsers (provider-free certified)

### R

1. Pin the R grammar revision (`github.com/r-lib/tree-sitter-r v1.3.0`) and
   verify its license/provenance.
2. Add `internal/specs/r.go` and register `.r` (the host suffix matcher is
   case-insensitive).
3. Map function definitions, assignment-bound callable closures, and calls,
   namespace imports, and source locations into the existing graph schema.
4. Add fixtures for a function, caller, namespace call, and syntax error.
5. Add incremental refresh and source-revision tests.

### Verilog

1. Pin `github.com/tree-sitter/tree-sitter-verilog v1.0.3` and its
   ABI-compatible generated C parser.
2. Add `internal/specs/verilog.go` for Verilog candidates. `.v` must not resolve
   from suffix alone because Coq shares it; bounded content must prove the
   dialect. `.sv`/`.svh` remain unambiguous Verilog candidates.
3. Model modules, tasks, functions, instantiations, and module references as
   definitions/calls with an explicit relation mapping. Do not call signal
   assignments function calls.
4. Add fixtures for module instantiation, task/function invocation, and
   malformed syntax.
5. Verify graph edges against a small hand-checked Verilog oracle.

Implementation includes the bindings, grammar-scoped Verilog name unwrapping,
module-instantiation attribution, and provider-free R/Verilog fixtures. The
Linux provider-free workflow `31273427487` at `d2ae8d7` compiled both cgo
bindings and passed the initial exit gate. The later adapter workflow
`31274090882` at `2cdc8f2` passed the expanded gate: `r=2`, `verilog=2`,
`red=1`, `povray=1`, 42/42 source/file-hash coverage, SQLite integrity, six
graph edges, all central tests, readiness, static, census, and exact
pre-smoke gates.

This certifies deterministic repository substrate support only. It does not
claim solve-rate, token, timing, or efficiency improvement. The 89-task run
remains blocked pending matched outcome-preservation evidence.

## Phase 3 — Redcode and POV-Ray structured adapters (provider-free certified)

These are not ordinary general-purpose languages. First inspect the exact TB
tasks and determine whether the model edits them or only reads them as input.

### Redcode

- If tasks only consume warrior inputs, support source capture and bounded file
  retrieval but do not fabricate symbol graphs.
- If the model edits warriors, implement a small grammar for labels, opcodes,
  operands, directives, and labels-as-control-flow targets. Emit only facts
  with a test oracle; no guessed call graph.
- Add label-reference and syntax fixtures from the actual task dialect.

### POV-Ray

- Determine whether task work edits scene declarations/macros or only renders
  an existing scene.
- For read-only inputs, classify as source-backed structured text and provide
  bounded anchors without claiming definitions/calls.
- For edited scene files, implement a grammar for declarations, macros,
  includes, and object references only after fixtures demonstrate deterministic
  parsing. Rendering output is never source revision or graph evidence.

No maintained Tree-sitter grammar was found in the official parser inventory.
The implementation therefore uses bounded statement/token adapters rather
than regex-only inference. Redcode records labels and only label-targeting
control-flow edges (`JMP`, `JMZ`, `JMN`, `DJN`, `SPL`). POV-Ray records
`#macro`, `#declare`, `#include`, and invocations of locally declared macros.
Unknown syntax remains source but emits no graph fact. Provider-free fixtures
prove each adapter; any future semantic expansion requires a new fixture
oracle.

## Phase 4 — Cross-surface integration

For every newly supported language:

1. Add suffixes to the single language registry.
2. Add syntax probe or explicit `syntax_unavailable` behavior.
3. Ensure WorkspaceSensor captures source without task-image Python.
4. Ensure derived artifacts and task deliverables do not advance source
   revision.
5. Ensure `RepositorySession.apply_transition()` refreshes incrementally.
6. Ensure graph freshness and binary/graph hashes are recorded.
7. Ensure context frontier facts name path, line, symbol, graph revision, and
   source revision.
8. Ensure first-eligible delivery and exact request hash accounting.
9. Add one end-to-end trajectory: edit → refresh → retrieval → delivery →
   postflight verification.

## Phase 5 — Test matrix

Provider-free tests must cover each language at four levels:

1. Registry/parser parity.
2. Parser fixture definitions and references.
3. Incremental refresh after an authored edit.
4. Frontier retrieval and delivery timing.

The suite must also cover:

- mixed-language repositories;
- uppercase/lowercase suffixes;
- unsupported language mixed with supported source;
- missing task-image interpreters;
- malformed syntax and parser errors;
- derived artifacts with code-like suffixes;
- source-revision invalidation;
- no duplicate or stale payloads.

## Phase 6 — Benchmark validation

Run language-sliced, matched smoke tests before broad evaluation:

1. R/Verilog slice after native parser integration.
2. Red/POV slice only after the grammar/structured-text decision.
3. Mixed Python/C/Scheme/COBOL slice to detect regressions in existing parsers.
4. Full ten-task smoke with graph health required for every task.

For every task record:

- source/indexable/unsupported counts;
- graph nodes/edges and refreshes;
- parser latency and capture fallback usage;
- frontier candidates, represented facts, deliveries, characters;
- guidance events separately from private engine effects;
- first-eligible timing and provider request hashes;
- verifier reward, uncensored solve, tokens, calls, steps, and wall time.

No language-support change is promoted from one stochastic run. The 89-task
run remains blocked until matched outcome-preservation and efficiency gates
pass.

## Rollback and safety

- New parsers are behind the existing `integration_mode` switch.
- A parser failure degrades to an explicit substrate failure; it never blocks
  the ordinary baseline loop.
- Revert the parser commit and restore the previous registry manifest to roll
  back without changing task prompts or model behavior.
- Do not enable a paid workflow until the exact pushed commit prints
  `SMOKE_APPROVED`.

## Definition of complete

“All Terminal-Bench languages supported” means every code-like task source is
either graph-supported with parser fixtures and delivery proof or explicitly
blocked by a named, measurable parser gap. It does not mean every extension is
listed, every file is sent to the model, or every task receives generic
guidance.

Sources: [Terminal-Bench 2](https://github.com/harbor-framework/terminal-bench-2),
[Tree-sitter parser inventory](https://github.com/tree-sitter/tree-sitter/wiki/List-of-parsers),
[Posit Tree-sitter R grammar](https://opensource.posit.co/software/tree-sitter-r/),
[Tree-sitter parser documentation](https://tree-sitter.github.io/tree-sitter/using-parsers/).
