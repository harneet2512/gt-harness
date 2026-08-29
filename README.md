# GT-Harness

GT-Harness is a host-owned repository-intelligence layer for coding agents. The active benchmark runtime augments Mini-SWE-Agent with bounded, source-grounded repository context instead of asking the model to rediscover the repository from scratch.

## Active agent and benchmark scaffold

The current GT-on benchmark path uses **Mini-SWE-Agent 2.4.6**, not NanoHarness.

- `eval.gt_central_agent:MiniSweCentralAgent` is the host-owned agent implementation. It uses Mini-SWE-Agent's model configuration, Bash tool contract, interruption flow, and model accounting while GT owns repository intelligence, delivery, controls, and receipts.
- Terminal-Bench uses the same central agent through Harbor 0.20.0.
- DeepSWE uses `eval.pier_gt_adapter:PierMiniSweCentralAgent` through DataCurve Pier 0.3.1; Pier supplies the Harbor-compatible task and verifier lifecycle.
- The active treatment is `central_relational_v3`. Planning is graph-first: repository indexing and hybrid retrieval complete before the generative bootstrap selection and first solver request.

The repository still retains a package distribution named `nano-harness` and legacy NanoHarness adapters for historical compatibility. Those paths are not the active v3 benchmark scaffold, and their results must not be mixed with Mini-SWE central results.

## What is currently being built?

The current system combines:

- deterministic repository graph construction;
- hybrid retrieval across exact paths, lexical search, BM25, local embeddings, and certified graph structure;
- bounded evidence delivery at the provider boundary;
- preflight and postflight command classification;
- source-revision tracking and incremental graph refresh;
- replayable receipts containing request hashes, evidence, timing, and token accounting.

The goal is not to force a model's answer. The goal is to give the model less context, but better-grounded context, at the moment it can use it.

## Measured results

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

## Run locally

```bash
pip install -e '.[dev,miniswe,eval,retrieval]'
pytest
python -m scripts.central_feature_census
python scripts/central_readiness_audit.py
```

The repository contains the Mini-SWE-Agent integration, GT runtime, retrieval engine, graph/indexer integration, Harbor/Pier evaluation adapters, and tests. Do not place API keys in source files; provide them through the runtime environment.

## License

MIT.
