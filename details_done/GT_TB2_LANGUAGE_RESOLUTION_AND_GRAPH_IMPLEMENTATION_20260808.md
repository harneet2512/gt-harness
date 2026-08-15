# GT Terminal-Bench language resolution and graph implementation

Date: 2026-08-08
Branch: `inline-engine`
Implementation base: `6778c4b`
Status: local provider-free runtime proof passed; exact-commit CI proof pending

## Result

The graph substrate no longer assumes that a file suffix is a language
identity. GroundTruth now resolves source identity from path plus a bounded
content prefix, sends that typed language through indexing and retrieval, and
fails closed when identity or parsing is incomplete.

The critical correctness witness is `.v`. The official Terminal-Bench 2 task
`prove-plus-comm` uses `.v` for Coq, while the existing registry treated every
`.v` file as Verilog. The old behavior could build a syntactically valid but
semantically wrong graph. The repaired resolver selects Coq or Verilog only
when declarations prove one dialect. Conflict or insufficient evidence is
`AMBIGUOUS`; the file remains authored source, source coverage is incomplete,
and no speculative graph is promoted.

This is a repository-substrate repair. It is not a solve-rate, token, outcome,
or causal-efficiency result. No paid provider call was made for this work.

## Root causes

1. **Extension-only dispatch was unsound.** The Python registry and Go walker
   selected a single parser before reading source. Shared suffixes could not be
   represented.
2. **The closure gate was self-referential.** Registry/spec parity proved that
   two internal lists agreed; it did not prove that the benchmark's languages
   were covered.
3. **Several benchmark source families were absent.** Stan, SPARQL, Turtle,
   LaTeX, Vim, Nginx, G-code, and exact build/control filenames had no native
   graph path.
4. **A file hash could masquerade as semantic support.** Some non-empty source
   files could enter `file_hashes` while producing no node and no explicit
   parser failure.
5. **Parser failures were not a readiness invariant.** A structurally present
   graph could be accepted without checking the native parser-failure count.
6. **Language disappeared at retrieval.** Frontier facts did not expose their
   source language, so language resolution could not be audited through to
   provider accounting.
7. **Structured calls used the wrong index convention.** Bounded adapters
   emitted one-based caller indices into a zero-based native contract. Unit
   parse objects contained calls, but those calls did not become SQLite edges.
   COBOL also needed a grammar-backed sibling paragraph/`PERFORM` ownership
   pass rather than the generic nested-function-body path.
8. **Two older “supported” paths were semantically thin.** R named every
   assignment-bound function `function` instead of unwrapping the AST `lhs`.
   POV-Ray attributed an invocation to the invoked macro itself rather than an
   enclosing macro, creating decorative self-edges. Both now have concrete
   ownership tests and certified SQLite edge proof.

## Implemented architecture

```text
path + bounded content prefix
  -> candidate capabilities
  -> deterministic LanguageResolution
       RESOLVED | AMBIGUOUS | UNSUPPORTED | NON_SOURCE
  -> source coverage and source revision
  -> matching native Go ResolveSource
  -> parser or bounded structural adapter
  -> SQLite file_hashes/nodes/edges/project_meta
  -> repository roles with source/target language
  -> ContextFrontierFact.language
  -> per-call/per-task accounting
```

Both implementations default to abstention. Unknown input does not select a
destructive parser, invent a symbol, or produce a caller edge.

## Resolution contract

| Input | Deterministic rule | Failure behavior |
| --- | --- | --- |
| `.v` | Coq declarations versus Verilog module/package declarations | conflict or no signature is `AMBIGUOUS` |
| `.conf` | recognized Nginx directives select Nginx | otherwise generic non-structural configuration |
| exact build names | Makefile, Dockerfile/Containerfile, CMakeLists.txt, Meson, Autotools | unknown basename is not promoted |
| extensionless script | bounded shebang maps a recognized interpreter | unknown interpreter abstains |
| unique registered suffix | select its one capability | unsupported structural mode fails coverage |

Only the first 64 KiB is used for language identity. No LLM, repository-wide
guess, task ID, benchmark-specific command, or provider text participates.

## Added structural graph support

| Language/control file | Structural facts allowed |
| --- | --- |
| Coq | theorem/lemma/definition nodes, imports, local references |
| Stan | functions and named model blocks, calls from parsed expressions |
| SPARQL | query/file structure and prefix/import anchors |
| Turtle | prefix declarations and concrete resource subjects |
| LaTeX | commands/environments/includes and local command use |
| Vim | functions/commands/registers and local calls |
| Nginx | named server/location/upstream structure and includes |
| G-code | program labels and mechanically linked subprogram calls |
| Make | targets, includes, and declared target dependencies |
| Dockerfile | stages and stage references |
| CMake | functions/macros/targets and recognized invocations |
| Meson | projects/targets/subdirectories |
| Autotools | macros, output declarations, and includes |

The adapters are bounded lexical/statement parsers with hand-checked fixtures.
They are not marketed as complete language grammars. Unrecognized constructs
produce no relationship. A non-empty supported structured source with no
recognized declaration receives a concrete `File` node so graph presence is
truthful without inventing a symbol.

Existing native Tree-sitter R and Verilog support remains intact. Existing
COBOL, Scheme, Red, and POV-Ray proof remains in the same runtime fixture.

## Benchmark-closure gate

`config/terminal_bench_2_language_contract.json` pins:

- repository: `harbor-framework/terminal-bench-2`;
- commit: `2fd12b88aafdd04a52c298e3940bcb189f9766d6`;
- expected task directories: 89;
- 14 concrete language/task witnesses; and
- 22 source-like suffix families observed in task instructions.

`scripts/verify_tb2_language_contract.py` has two modes:

1. the static mode proves every declared witness resolves to a structural
   capability with the promised symbol/caller level;
2. the dataset mode verifies the exact Git commit and task count, proves that
   every witness task/instruction exists, independently extracts suffixes from
   the instructions, and rejects every registry-recognized structural family
   absent from the contract or unsupported by the runtime.

The provider-free GitHub workflow clones only that exact revision and runs the
dataset form. The paid workflow and pre-smoke/readiness gates run the static
form so a benchmark update cannot silently widen the experiment.

The pinned dataset proof observed these instruction counts:

```text
.c=17 .cbl=4 .conf=3 .cpp=6 .gcode=1 .html=16 .js=7 .pov=3
.proto=1 .py=64 .r=5 .red=14 .rs=3 .scm=15 .sparql=1 .sql=2
.stan=2 .tex=3 .toml=3 .ttl=1 .v=2 .vim=3
```

The two `.v` mentions belong to the Coq task instruction; the dataset gate
does not falsely claim a live Verilog benchmark witness. Verilog remains a
general runtime capability proven by its own provider-free fixture.

## Observability and release invariants

Repository and deep-metrics receipts now include:

- resolved, ambiguous, and unsupported source counts;
- `language_file_counts` and `resolution_reason_counts`;
- parser-failure count;
- frontier candidate and delivered language counts;
- source and target language on structural relationships; and
- the existing graph revision, source revision, schema, FTS, node, edge,
  definition, call, binary-hash, and provider-accounting fields.

`coverage_complete` requires all of the following: no missing source file, no
unsupported path, no ambiguous path, no parser failure, valid schema, and an
FTS-capable current graph. A missing `parse_failures` metadata row is itself a
failure rather than an assumed zero.

## Provider-free proof completed locally

The FTS-enabled native Windows build and runtime fixture passed:

```text
REPOSITORY_SUBSTRATE_PROVEN
source_files=48
indexable_files=48
node_count=37
edge_count=14
parser_failures=0
```

The fixture observed all added structural languages in `file_hashes`, concrete
nodes for all required adapters, certified SQLite integrity and three FTS
tables, 10 named target/caller definitions, directed calls in 14 caller-capable
languages, and four frontier anchors. Resolution
included Coq, Nginx, and Verilog content-signature decisions rather than
extension-only dispatch.

The exact pinned dataset contract passed with `TB2_LANGUAGE_CONTRACT_PROVEN`.
The Python resolver/coverage tests and all vendored Go tests passed. The final
exact-pushed-commit GitHub provider-free workflow remains the authoritative
cross-platform release receipt.

The archived ten-task policy artifacts also passed
`REPLAY_OK`, `ARCHIVED_EFFICIENCY_REPLAY_OK`, and
`ARCHIVED_REGRESSION_REPLAY_OK`. The efficiency replay removed zero assistant
reasoning characters; it is a deterministic compatibility witness, not a new
paid outcome or efficiency result.

## Files changed

- `gt_engine/language_registry.py`: typed content-aware language authority.
- `gt_engine/indexer.py`: fail-closed coverage and parser telemetry.
- `gt_engine/central_runtime.py`: resolver-backed source and precedent paths.
- `gt_engine/repository_intelligence.py`: language-bearing graph roles.
- `gt_engine/context_frontier.py`: language-bearing facts and claim identity.
- `eval/gt_central_agent.py`, `gt_engine/deep_metrics.py`: receipts/metrics.
- `vendor/gt-index-src/internal/specs/*`: native candidate resolution and new
  capabilities.
- `vendor/gt-index-src/internal/walker/walker.go`: read-before-dispatch.
- `vendor/gt-index-src/internal/parser/*`: structural adapters and file-node
  fallback, zero-based caller ownership, R assignment names, COBOL paragraph
  calls, and POV-Ray macro ownership.
- `scripts/verify_gt_index_runtime.py`: actual FTS/index/semantic proof.
- `scripts/verify_tb2_language_contract.py`: independent benchmark gate.
- provider-free, paid-workflow, pre-smoke, readiness, Python, and Go tests.

## Remaining boundaries before a complete benchmark run

1. **Exact-commit CI:** the provider-free GitHub workflow must pass after the
   implementation commit is pushed. A local binary is not the release gate.
2. **Matched paid evidence:** after separate authorization, a matched smoke
   must prove repository health per task, correct retrieval/delivery, outcome
   preservation, and efficiency. Parser success alone does none of those.
3. **Full 89:** remains blocked until the matched gate passes. It must not be
   started from this implementation proof.

The two previously identified capture gaps are now implemented. The sensor
accepts only explicitly named `/etc/nginx/**` and `/var/log/nginx/**` paths,
records them in the source/workspace revision, and mirrors authored Nginx
configuration under a safe `__external__/` graph prefix. It also probes a
bounded number of extensionless regular files and promotes one only when its
captured content proves a recognized shebang language. No arbitrary `/etc`,
`/var`, or filesystem-wide scan is enabled.

## Risks and rollback

- False language selection is contained by content signatures and ambiguity
  abstention. Adding a dialect requires a collision fixture before registry
  expansion.
- Structural adapters may under-report syntax. Under-reporting remains an
  explicit bounded limitation; speculative edges are forbidden.
- Runtime overhead is bounded by one prefix read already required for parsing
  and explicit metrics. A regression is visible in index latency and refresh
  receipts.
- Operational execution remains fail-open on substrate failure so ordinary
  Mini-SWE can continue. Experiment promotion remains fail-closed.
- Rollback is the implementation commit. The existing `integration_mode=off`
  switch continues to preserve the GT-off behavior, and no task instruction or
  selected model command is rewritten by this change.

## Stop state

The in-workspace language-resolution and graph implementation is locally
provider-free proven. It becomes release-certified only after the exact commit
passes GitHub provider-free CI. It is not yet a complete-benchmark approval,
and it is not permission to run a paid smoke or the 89-task workflow.
