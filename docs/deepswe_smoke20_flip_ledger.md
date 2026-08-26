# DeepSWE smoke20 flip ledger

GT solved 10/20 vs baseline 15.

| Quadrant | Count | Tasks |
|---|---:|---|
| both_solved | 8 | abs-module-cache-flags, abs-stepped-slices, aiomonitor-task-snapshots-diff, anko-default-function-arguments, clack-async-autocomplete-options, fd-deterministic-multi-key-sorting, katex-multicolumn-array-spans, testem-per-launcher-reports |
| gt_only | 2 | adaptix-name-mapping-aliases, oxvg-structural-selector-preservation |
| baseline_only | 7 | actionlint-action-pinning-lint, arktype-json-schema-refs-dependencies, awilix-async-container-initialization, bandit-interprocedural-taint-checks, boa-hierarchical-evaluation-cancellation, pest-character-class-coalescing, testem-bail-on-test-failure |
| both_failed | 3 | bandit-incremental-cache-control, claude-code-by-agents-recursive-delegation, csstree-shorthand-expansion-compression |

## Per-task localization audit

| Task | Quadrant | Treatment | Edit targets | Precision vs oracle |
|---|---|---|---|---:|
| actionlint-action-pinning-lint | baseline_only | ACTIVE | — | n/a |
| arktype-json-schema-refs-dependencies | baseline_only | ACTIVE | ark/attest/assert/chainableAssertions.ts:254#type | 0.00 |
| awilix-async-container-initialization | baseline_only | ACTIVE | src/resolvers.ts:194#asClass | 1.00 |
| bandit-interprocedural-taint-checks | baseline_only | ACTIVE | bandit/core/issue.py:10#Cwe | 1.00 |
| boa-hierarchical-evaluation-cancellation | baseline_only | FAILED | core/engine/src/context/mod.rs:94#Context | 1.00 |
| pest-character-class-coalescing | baseline_only | ACTIVE | meta/src/optimizer/mod.rs:126#OptimizedExpr | 1.00 |
| testem-bail-on-test-failure | baseline_only | ACTIVE | lib/app.js:429#getExitCode | 1.00 |
| bandit-incremental-cache-control | both_failed | ACTIVE | bandit/core/config.py:1#config | 0.00 |
| claude-code-by-agents-recursive-delegation | both_failed | ACTIVE | — | n/a |
| csstree-shorthand-expansion-compression | both_failed | ACTIVE | — | n/a |
| abs-module-cache-flags | both_solved | ACTIVE | repl/repl.go:90#BeginRepl | 1.00 |
| abs-stepped-slices | both_solved | ACTIVE | — | n/a |
| aiomonitor-task-snapshots-diff | both_solved | ACTIVE | aiomonitor/monitor.py:236#format_running_task_list | 1.00 |
| anko-default-function-arguments | both_solved | ACTIVE | — | n/a |
| clack-async-autocomplete-options | both_solved | ACTIVE | packages/core/src/prompts/autocomplete.ts:63#AutocompletePrompt, packages/prompts/src/autocomplete.ts:241#autocompleteMultiselect | 1.00 |
| fd-deterministic-multi-key-sorting | both_solved | ACTIVE | src/dir_entry.rs:42#path | 0.00 |
| katex-multicolumn-array-spans | both_solved | ACTIVE | src/ParseError.ts:1#ParseError | 0.00 |
| testem-per-launcher-reports | both_solved | ACTIVE | examples/metadata_reporter/testem.js:1#testem | 0.00 |
| adaptix-name-mapping-aliases | gt_only | ACTIVE | src/adaptix/_internal/morphing/facade/provider.py:190#name_mapping | 1.00 |
| oxvg-structural-selector-preservation | gt_only | ACTIVE | — | n/a |

## Treatment failures

- `boa-hierarchical-evaluation-cancellation`: FAILED

Mean edit-target precision across audited tasks: 0.6429
