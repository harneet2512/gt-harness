# DeepSWE 10-task smoke — post-run audit (2026-08-11)

## Scope and commit

The smoke was dispatched from GitHub Actions run `31550154123` at commit
`6918c27` using the current central in-process agent
`eval.gt_central_agent:MiniSweCentralAgent`. It selected ten pinned DeepSWE
`v1.0.0` tasks (two each from Go, Python, TypeScript, JavaScript, and Rust),
Mini-SWE-Agent `2.2.8`, Harbor `0.20.0`, model
`openrouter/xiaomi/mimo-v2.5-pro`, `temperature=1`, one trial per task, and
the Snowflake Arctic Embed M ONNX backend. The later merge-reader correction is
on commit `78a3ec4`; it was not part of the paid task execution.

Local artifacts are retained under:

`D:\\tmp\\deepswe-31550154123-f44152e7b84c49fe875b55bb6943e0ac`

## Pre-run audit result

The provider-free gate was independently run before dispatch using a source-
built `gt-index` binary. The direct and module censuses, repository substrate
fixtures, language contract, readiness audit, strict release tests, and
`central_pre_smoke_gate.py` all passed and emitted `SMOKE_APPROVED`. The
workflow also proved the pinned Snowflake ONNX model locally with zero network
calls. The paid workflow was configured as ACTIVE + certified policy + SHADOW
preflight, so no command could be rewritten or suppressed by GT.

Earlier workflow attempts did not run model tasks: they failed in setup,
dependency installation, Harbor path selection, or result merging. They are
not benchmark evidence. The task jobs in `31550154123` did run successfully;
the merge step failed only because Harbor stores an aggregate `result.json`
alongside the nested per-trial result. Commit `78a3ec4` now selects the nested
trial result by the presence of `verifier_result`/`exception_info`.

## Observed task outcome

All ten Harbor jobs completed, but the verifier reward was `0.0` for every
task: **0/10 solved**. This is not a valid GT quality comparison because the
retrieval substrate was unusable or globally abstained, as shown below.

| Measure | Total |
| --- | ---: |
| tasks | 10 |
| verifier reward 1 | 0 |
| model/provider calls | 1,123 |
| model steps | 1,115 |
| model actions | 3,201 |
| input/output tokens reported by artifacts | 68,708,026 |
| GT provider-visible legacy deliveries | 13 |
| delivery audit failures | 0 |
| late deliveries | 0 |
| predictive deliveries | 0 |
| preemptive retrieval selected calls | 0 |
| preemptive retrieval deliveries | 0 |
| dense backend available during GT retrieval | 0/10 |
| graph substrate failures | 4/10 |

The 13 legacy delivery receipts were grounded and arrived in the first
eligible provider request. That proves timing/receipt integrity only; it does
not prove the model used them or that preemptive retrieval helped. The
preemptive retrieval channel produced no ranked files or selected evidence in
any task.

## Root causes proven by the receipts and code

### P1 — A non-fatal span truncation is treated as whole-corpus incompleteness

In `gt_engine/hybrid_repository.py:332-343`, a bounded source span is kept,
but `chunk_character_limit` is appended to the repository's global
`reason_codes`. At `:535`, `complete` is computed as `not reason_codes`. The
central agent then refuses to instantiate the hybrid retriever at
`eval/gt_central_agent.py:2014-2018` whenever `complete` is false.

The graph-passed tasks therefore abstained on every preemptive call with
`reason_codes=["chunk_character_limit"]`, despite having usable graph nodes
and source text. This is a retrieval availability bug, not a legitimate
abstention: a bounded document is present and can be ranked with explicit
truncation provenance. It also explains why the dense backend was never
called; dense availability is recorded as zero because retriever construction
was skipped, not because the ONNX model failed. The independent
`dense-backend-proof.json` artifact reports `available=true`, the pinned
Snowflake identity, and zero network/provider calls.

### P1 — Four tasks failed the required graph substrate gate

`aiomonitor-task-snapshots-diff`, `arktype-json-schema-refs-dependencies`,
`boa-hierarchical-evaluation-cancellation`, and
`fd-deterministic-multi-key-sorting` recorded source-backed substrate failure
(`graph_missing`, `source_revision_missing`, `graph_not_current`, and
`repository_intelligence_invalid`; the first three also recorded an index
`TimeoutError`/`RuntimeError`, and fd additionally recorded
`mirror_incomplete`). The contract correctly fails closed for these tasks;
they must not be counted as successful repository-intelligence treatment.

The failures are task-specific index/mirror construction failures in
`RepositorySession.refresh`/`ensure_index_with_receipt`, not evidence that
the graph was healthy but retrieval ranked poorly. Until the exact index
failure is repaired and proven on these four repositories, an end-to-end
score is invalid for a graph-required GT treatment.

## What the smoke does and does not prove

**Proven:** workflow checkout and dependency setup after the fixes; central
agent execution; exact receipt/request hashing for emitted legacy payloads;
grounded first-eligible timing; SHADOW non-intervention; Snowflake ONNX model
availability in the job; and fail-closed handling of missing graph substrate.

**Not proven:** hybrid retrieval quality; dense/lexical/BM25/structural fusion
in a live agent; preemptive payload delivery; solve-rate uplift; efficiency;
or absence of regression. The `0/10` result must not be presented as a GT
quality result because GT's intended retrieval mechanism was effectively
disabled by the two P1 substrate/availability defects.

## Required next gate (no paid rerun yet)

1. Add a provider-free regression test showing a truncated but otherwise
   certified source document remains retrievable and carries explicit
   truncation provenance; reserve `complete=false` for fatal substrate
   reasons (missing graph, unsafe/unreadable source, schema/coverage/link
   limits, or query failure).
2. Reproduce and fix the four task-specific index/mirror failures in a
   provider-free checkout fixture or task-container diagnostic. Do not weaken
   `require_graph_ready`; a source-backed task with no current graph remains a
   failed gate.
3. Re-run the central census, readiness audit, and exact pre-smoke gate at the
   repaired commit. Verify the Snowflake backend is actually constructed in a
   retrieval call and that ranked/selected evidence is nonzero on a controlled
   source-backed fixture.
4. Only after those gates pass, dispatch another explicitly authorized ten-task
   DeepSWE smoke. The corrected merge reader in `78a3ec4` will produce the
   merged result artifact without rereading the aggregate Harbor result.

Until steps 1–3 pass, the 89-task benchmark and any claim of GT efficiency or
retrieval superiority remain blocked.
