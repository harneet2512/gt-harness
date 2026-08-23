# GT-Harness

GT-Harness is a model-agnostic benchmarking product for measuring whether deterministic GroundTruth repository intelligence helps coding agents. The prerelease product owns graph construction, readiness, delivery, run receipts, and paired comparison; it does not depend on a particular model or provider.

The last clean-Linux certified implementation is `3e2185d3f4ba0a228c740ab2a6d23a287cfc5380`, with verdict `CERTIFIED_WITH_DECLARED_LIMITATIONS`. The current prerelease branch adds the canonical decision-context compiler and must receive a new exact-SHA Codespaces certification before any paid run is authorized. Certified graph-language scope remains Python, JavaScript, TypeScript, Go, Rust, and Java. General competitive release remains `HOLD`.

## What is currently being built?

The current system combines:

- deterministic repository graph construction;
- exact symbol search and source-evidenced structural graph queries;
- persistent revision-bound dense retrieval fused with exact/BM25/lexical/structural
  retrieval by deterministic reciprocal-rank fusion;
- bounded decision packets containing edit targets, certified process paths, change surface, affected tests, validation facts, uncertainty, and revision-bound evidence;
- preflight and postflight command classification;
- exact source-revision tracking and fail-closed full graph convergence after edits;
- replayable receipts containing request hashes, evidence, timing, and token accounting.

The goal is not to force a model's answer. The goal is to give the model less context, but better-grounded context, at the moment it can use it.

The graph does not require an LSP or provider credential. The release benchmark
uses the pinned local Snowflake ONNX embedder and `hybrid_required`, so a missing,
stale, or corrupt dense index fails before provider use. Local exploratory runs
default to `hybrid_if_available` and explicitly receipt degradation when the model
is absent. Dense similarity only ranks inspection candidates; it never creates a
verified symbol, relationship, or edit target.

## Historical results (not product certification)

### Retrieval benchmark

On the 427-row Agent Retrieval Bench across 25 repositories:

| Metric | Result |
|---|---:|
| Ranked MRR | 0.4372 |
| Ranked Recall@20 | 0.7072 |
| Ranked BCY@8K | 0.5198 |
| Delivered-payload MRR | 0.4207 |

### Matched Mini-SWE smoke

In one ten-task matched smoke, GT-on matched the frozen GT-off baseline at **9/10 official tasks** on the common solved set, while using:

- **31.3% fewer tokens**;
- **51 fewer API calls**;
- **53 fewer assistant steps**;
- **103 fewer model actions**.

This is a single matched-smoke result, not a claim of causal solve-rate improvement. Provider-free tests establish implementation integrity; larger outcome claims require a separately authorized matched evaluation.

## Canonical local product path

```bash
pip install -e .
gt-harness doctor
gt-harness graph build --root /path/to/repository
gt-harness graph query definition Symbol --root /path/to/repository
gt-harness run "task" --model exact/provider-model --treatment bare
gt-harness run "task" --model exact/provider-model --treatment groundtruth
gt-harness record-harbor-outcomes --harbor-run-dir /path/to/job --output-dir /path/to/evaluated
gt-harness compare --baseline /path/to/bare/evaluated --treatment /path/to/gt/evaluated
gt-harness certify --receipt-dir /path/to/codespaces-campaign --expected-commit "$(git rev-parse HEAD)"
```

The GT arm fails before the first provider call when its exact-revision graph
or initial evidence packet is unavailable. A nominal GT task with zero
delivered evidence cannot enter a paired comparison. Evaluator outcomes are
derived from graded Harbor receipts and hash-bound to the run receipt; they are
not typed in by an operator.

`gt-harness run` is the sole coding-agent product boundary. GT Harness does not
ship an MCP server: benchmark treatments run through the pinned Mini-SWE-Agent
2.2.8 loop so graph delivery, trajectories, costs, and solve outcomes share one
auditable path.

The legacy file-keyed incremental indexer and historical benchmark/control paths remain in the repository for parity analysis, but they are not the canonical graph lifecycle. See `CANONICAL_ARCHITECTURE.md` for the authoritative boundary.

Provider-free product certification runs `pytest -m 'not external_evidence'`. The excluded class is explicit: it requires either the separately pinned official ARB evaluator or a hosted historical Finalstand artifact/API receipt. It is not silently counted as product coverage and runs only in its owning benchmark/evidence workflow.

The certification command verifies receipts produced by the real clean Linux campaign. It does not create evidence, accept a different checkout SHA, tolerate a dirty checkout, or convert an absent competitive benchmark into a product PASS.

## License

MIT.
