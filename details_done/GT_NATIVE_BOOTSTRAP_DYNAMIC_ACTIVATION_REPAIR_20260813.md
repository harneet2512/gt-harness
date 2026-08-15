# GT native-bootstrap and dynamic-activation repair — 2026-08-13

## Result

This pass closes the executable defects exposed by Terminal-Bench workflow
`31734290105`; it does not claim an outcome or efficiency improvement. The
archived run remains invalid treatment evidence. A new exact-SHA Linux
provider-free certification and exact live bootstrap canary are still required
before any paid task smoke.

## What the rejected run actually showed

The exact 15-task arithmetic was 12/15 GT-on versus 13/15 in the historical
local GT-off cohort: one apparent gain, two losses, and one both-fail task. The
raw token totals are not a valid efficiency comparison because the historical
control used a different wrapper/provider-accounting path.

More importantly, the intended persistent execution state did not operate:

- 11 initially source-backed tasks built a repository substrate, attempted one
  bootstrap, and received `bootstrap_error:BadRequestError` before a valid
  selection or model-visible state frame;
- four tasks were initially `not_applicable_no_supported_source`; model-created
  source later became graph-visible on relevant trajectories, but the initial
  applicability label prevented persistent-state activation;
- authoritative progress delivery auditing rejected manual receipt rows that
  omitted plural claim IDs, provider-view hashes, and the before-query flag;
- a retained compaction epoch could change the provider view while reporting
  `provider_change_reason=none`.

The two observed losses (`torch-tensor-parallelism` and `video-processing`)
diverged before any persistent-state payload. They remain real outcome losses,
but the artifacts do not causally attribute them to a repository-state frame
that was never delivered.

## Root causes and minimal corrections

### 1. Native DeepSeek bootstrap incompatibility

The normal executor and the bootstrap have different contracts. The executor
needs DeepSeek's ordinary reasoning behavior; the bootstrap is one bounded
selection over certified catalog IDs and forces the Bash tool. DeepSeek V4
thinking mode rejects that forced `tool_choice` combination.

Primary-source basis (accessed 2026-08-13): DeepSeek documents that V4 thinking
defaults to enabled and is toggled with
`extra_body={"thinking":{"type":"disabled"}}`; its official coding-agent
integration notes state that thinking mode rejects `tool_choice`.

- <https://api-docs.deepseek.com/guides/thinking_mode>
- <https://api-docs.deepseek.com/quick_start/agent_integrations/oh_my_pi/>

`_bootstrap_provider_call_kwargs()` now constructs the exact bootstrap-only
envelope. It retains temperature 0, one direct physical call, no retries, the
bounded output limit, timeout, and forced Bash function. For a DeepSeek V4
model routed through native DeepSeek or TokenRouter, it merges existing
`extra_body` configuration and sets `thinking.type=disabled`. The executor
configuration is unchanged.

Bootstrap exceptions now include a non-secret typed provider receipt:
exception class, HTTP status when available, provider code, retryability, and
a SHA-256 of the message. Raw provider text and credentials are not persisted.

### 2. Generic paid preflight

The former Terminal-Bench preflight asked for the word `ok`. That could pass
while the actual forced-tool bootstrap failed. The new
`scripts.central_bootstrap_canary` constructs a fixed certified catalog and
calls `MiniSweCentralAgent._run_persistent_state_bootstrap()` itself. It accepts
only one valid selection, one response-received provider call, zero action
executions, the expected call contract, exact requested/served model identity,
nonempty provider identity, and complete SHA-256 request/provider/catalog hashes.

The workflow now:

1. resolves the requested ref to one immutable SHA;
2. runs the reusable source-built `central_provider_free.yml` against that SHA;
3. checks out the same SHA in plan, task, and merge jobs;
4. runs and uploads the exact bootstrap canary;
5. enumerates task jobs only after those prerequisites succeed.

This ordering prevents paid debugging and branch-move confounding.

### 3. Dynamic source-created activation

Task-start abstention is now provisional while the task has no supported
source. After an executed action creates a model-authored indexable source, the
existing incremental graph lifecycle must first produce a complete, current
GraphDB bound to the new source revision. Only then does the host:

1. build the shared `HybridRepository`;
2. run the shared five-channel `HybridRetriever` and seed the live cache;
3. build the certified bootstrap catalog;
4. initialize one `PersistentExecutionStateEngine`;
5. make the one bootstrap call;
6. commit the already-executed source-creating action at the new certified
   revision;
7. expose the bounded initial state in the first next executor request.

The action-revision rebind is restricted to this post-execution activation
boundary. Ordinary stale proposals remain rejected. Any dynamic activation
error leaves Mini-SWE running but makes the treatment release-invalid.

Receipts now record initial/current applicability, ever-applicable state,
activation action/call/source/graph revisions, abstention correctness, and
reason codes. Denominator exclusion applies only if the task never becomes
source-applicable.

### 4. Activation-aware release accounting

The release gate now evaluates context compilations beginning at the recorded
activation call, preflight projections after the activation action, and
postflight commits beginning with the source-creating action. For a task that
never becomes applicable, the 17+1 census requires configured PES, zero
bootstrap, zero PES exercise, and `correctly_abstained=true` rather than
fabricating activity.

### 5. Visible-delivery and provider-view accounting

Progress deliveries now use the authoritative schema: `fact_ids`, exact
provider-view and request hashes, changed provider-message index, and explicit
before-query timing. The canonical delivery audit, not a guidance-only count,
accepts the synthetic live path.

When an existing compaction epoch remains active, subsequent provider-view
differences now record `retained_compaction_epoch`; no changed request is
reported with an unexplained `none` reason.

## Verification

Focused RED/GREEN witnesses:

- native DeepSeek raw bootstrap originally failed the new thinking-mode
  assertion; the repaired envelope passes;
- dynamic activation originally left `state.files_modified=[]` because the
  creating action was discarded as stale; the repaired run records
  `app.py` and delivers the next-request state frame;
- progress originally failed authoritative delivery audit due to missing
  receipt fields; the repaired receipt passes;
- the source-less release fixture originally failed the 18-mechanism census;
  the explicit correct-abstention contract passes.

Integrated local verification:

```text
pytest test_gt_central_agent + persistent_execution_state + delivery_audit
       + central_release_gate + gt_preflight
246 PASS, 1 SKIP (pinned ONNX asset absent locally)

pytest tests
1,541 PASS, 5 environment SKIPS, 6 EXPECTED LOCAL FAILURES
All six failures reach the same fail-closed substrate witness: the checked-in
Windows `gt-index.exe` lacks current Objective-C coverage. No failed assertion
was in the repaired bootstrap, activation, delivery, release, or workflow path.

ruff changed runtime/canary/gate/tests
PASS

workflow YAML parse
PASS

git diff --check
PASS (line-ending warnings only)
```

The exact-SHA Linux workflow result remains pending and is not inferred from
these local results.

## Remaining stop gates

1. Commit only the intended runtime, gate, workflow, tests, and documentation.
2. Push the exact SHA.
3. Run `central_provider_free.yml` for that SHA and require source-built index,
   pinned ONNX, all 17 producer/consumer paths, PES tests, `READY`,
   `SMOKE_APPROVED`, static checks, and `provider_calls: 0`.
4. Only after gate 3 succeeds may the workflow run the exact live bootstrap
   canary. A canary failure stops before task enumeration.
5. Inspect canary receipt: valid selection, one physical call, zero executed
   actions, DeepSeek bootstrap thinking disabled, exact hashes, expected
   provider identity, and no retry/marker error.
6. Obtain explicit authorization before any paid task cohort. Do not start an
   89-task benchmark from this repair pass.

## Claims allowed now

- The four integration defects have executable local regression tests.
- The dynamic applicability lifecycle is represented and release-gated.
- The exact production bootstrap seam replaces the generic provider preflight.

## Claims forbidden now

- GT solves more tasks than baseline.
- GT is more token/call/step efficient than baseline.
- The repaired live provider accepts the bootstrap.
- All 18 mechanisms operated in a stochastic task run.
- Terminal-Bench, DeepSWE, or SWE-Live is ready to publish.
