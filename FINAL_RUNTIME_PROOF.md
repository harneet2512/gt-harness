# GroundTruth final runtime proof

Date: 2026-08-10

Status: **implementation-integrity proven; retrieval quality not yet measured**.

## Frozen claim boundary

The implemented mechanism is an optional, deterministic retrieval stage inside
`MiniSweCentralAgent`.  It compiles trajectory state after the most recently
observed action, retrieves repository evidence before the next provider call,
and can append one bounded `PreemptiveFrame` to that same call.  It does not
predict the model's next action, execute another tool, call another model,
rewrite a command, or suppress an action.

The feature is disabled by default.  GT `OFF`, `AUDIT`, and certified-shadow
configuration force it off.  Missing/stale graph state, an unavailable dense
backend, a timeout, a duplicate claim, or an exhausted character budget causes
an explicit abstention/fail-open path.

## Shared retrieval path

Both the ARB adapter and the optional Mini-SWE stage use:

1. `HybridRepository` constructed from the exact checkout plus GraphDB.
2. A typed `RetrievalState` containing the task, current retrieval intent,
   previous action, active/changed paths, diagnostics, validation state, and
   source revision.
3. Independent exact, lexical, BM25, dense, and structural channels.
4. Rank fusion by equal reciprocal-rank fusion with `k=60`.
5. Unique-file aggregation, stale-revision rejection, active/changed-path
   exclusion, complete-span packing, deduplication, and abstention.

The dense channel is the official local
`Snowflake/snowflake-arctic-embed-m` ONNX artifact at repository revision
`7802add0519e4bf94c46ef23552176697c7a1ac7`.  The model SHA-256 is pinned to
`564e6c65ee0c739a486702e9e3e9b33c3f697c19c34dbe886bce9eec497ce971`.
Queries use Snowflake's published retrieval prefix; documents do not.  The
backend uses CLS pooling, L2 normalization, deterministic CPU ONNX Runtime,
and content-addressed query/document caches.  It makes no inference-provider
or embedding-API calls.

The repository's older optional embedding implementation used Alibaba GTE
ModernBERT/e5.  It was not a Snowflake backend.  The Snowflake backend in this
change is new and separately identified in every dense-channel receipt.

## Exact next-request proof

The end-to-end central-agent witness proves this order:

```text
repository/session state
-> hybrid retrieval
-> bounded frame compilation
-> provider-prepared message list
-> exact request hash
-> model.query()
```

The witness asserts:

- the selected source evidence is present in model call 1;
- the receipt binds its claim IDs, source revision, message index, request
  SHA-256, evidence action, and eligible call;
- the delivery is neither predictive nor one step late;
- no extra agent action or provider call is introduced;
- disabled, stale, timed-out, duplicate, and over-budget cases preserve the
  normal provider view.

## Regression found during audit

The broad regression suite initially exposed a shared GraphDB failure.  A
projection change referenced the assertion-only variable `target_path` while
emitting ordinary node properties.  Repositories containing property rows
therefore failed closed with `build_failed/NameError`, which removed task-start
localization, context-frontier delivery, and post-edit graph freshness at once.

The fix restores property values to their own row, places target-path
provenance on assertion evidence, and adds both property and assertion
regression tests.  The three central-runtime failures and the direct repository
intelligence witness pass after the repair.

## Verification evidence

- Changed-surface regression suite: **195 passed**.
- Targeted graph/runtime repair set: **9 passed**.
- Ruff over every changed Python source/test: **passed**.
- `git diff --check`: **passed** (line-ending notices only).
- `scripts/central_readiness_audit.py`: **READY**.
- `python -m scripts.central_feature_census`: all required all-17, timing,
  grounding, consumption, context-accounting, substrate, frontier, and
  non-blocking markers present.

The ARB checkout runner now emits flushed per-shard, per-group, and per-sample
progress lines. This makes long-running checkout/index/embedding work visible
without changing prediction output or evaluation metrics.

The runner also prepares each immutable checkout snapshot once: the certified
index receipt and hybrid graph/source corpus are reused across rows with the
same source revision, while query-conditioned retrieval remains per sample.
This removes repeated preparation work without changing the ranked or delivered
payloads.

## Final live-retrieval parity proof (2026-08-11)

The earlier limitations above are superseded. Complete ARB workflow
`31517629497` evaluated 427/427 rows and is reported in
`RETRIEVAL_BENCH_RESULTS.md`. The live central agent and ARB now import one
frozen retrieval profile: channel limit 100, top-K 20, selection limit 8,
1,200 tokens, 12,000 task characters, and a 32-span dense pool.

The actual pinned Snowflake ONNX model and tokenizer were executed through the
central agent locally and in GitHub provider-free workflow `31526751148` at
commit `e4eab72`. Both SHA-256 values passed. The local two-turn witness
measured cold retrieval at 4.9–6.5 seconds under the 30-second cold gate and a
cached next-turn retrieval at 303 ms under the two-second steady-state gate.
The first evidence was present in the exact first eligible provider request;
late/predictive delivery and extra model calls/actions were zero.

All provider surfaces now pass through the typed contribution compiler. The
GitHub run proved the real dense test, graph/index fixture, pinned language
contract, contribution accounting, component registry, all-17 census,
structural readiness (`READY`), exact pushed commit, strict lifecycle tests,
and pre-smoke release gate (`SMOKE_APPROVED`). Static checks also passed.

## What remains unproven

- ARB and runtime integrity do not prove that a frontier model uses the
  evidence beneficially.
- Paired decision-point utility has not yet been evaluated on the frozen live
  payload.
- GT-on DeepSWE, Terminal-Bench 2.0, solve uplift, and outcome/resource
  non-regression remain unproven.
- No paid benchmark was launched by this implementation pass.
