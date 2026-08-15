# GT trajectory audit — workflow 31297108410

Date: 2026-08-09  
Run root: `D:\gt_runs\31297108410\artifacts`  
Audit implementation: `scripts/central_trajectory_audit.py`  
Audit output: `D:\gt_runs\31297108410\trajectory_audit.json`

## Certification boundary

This is a provider-free, read-only audit of the nine task trajectory/receipt
pairs available from the workflow. The portfolio task failed before producing
a central receipt, so it is not silently counted as a tenth task.

The audit certifies only deterministic properties observable in the archived
artifacts:

* every archived model action count agrees with the central receipt;
* every effect has a unique ID, a known disposition, and no late/predictive
  timing flag;
* every model-call context has both request hashes and complete fact accounting;
* every visible frontier/guidance delivery has concrete anchors, a request
  hash, an exact provider-view hash (from the delivery or its model-call
  context), and arrived in its first eligible request.

It does **not** call `anchor_followed`, `same_response`, or later action
similarity causal proof. Temperature-1 model causality is
`UNIDENTIFIABLE` for this archive because it contains hashes, not provider
request bodies plus model sampling/checkpoint state. A causal claim requires a
counterfactual replay with those artifacts.

## Result

```
task_count=9
audit_status=DETERMINISTIC_AUDIT_CERTIFIED
deterministic_integrity=CERTIFIED
model_causality=UNIDENTIFIABLE
replay_state_available=False
failures=0
```

The audit counted 200 effects:

| Disposition | Count | Meaning |
|---|---:|---|
| `engine_internal_state` | 135 | deterministic controller state/update work; not model text |
| `audit_only` | 56 | receipt-only accounting; no downstream consumer recorded |
| `existing_engine_actuation` | 6 | an existing controller consumer read/acted on the effect |
| `provider_payload` | 3 | effect linked to a confirmed model-visible delivery |

There were 21 confirmed visible deliveries. Their observed semantic relation
was 9 `same_response`, 5 `deferred`, 6 `stale_source`, and 1 `no_match`.
Those labels describe trajectory alignment only; they do not establish that
the model used the fact or that the fact improved the outcome.

## Per-task audit

| Task | Actions | Effects | Visible deliveries | Deterministic status |
|---|---:|---:|---:|---|
| break-filter-js-from-html | 21 | 16 | 1 | certified |
| cobol-modernization | 30 | 10 | 0 | certified |
| fix-code-vulnerability | 36 | 34 | 2 | certified |
| gpt2-codegolf | 35 | 3 | 0 | certified |
| headless-terminal | 32 | 56 | 7 | certified |
| llm-inference-batching-scheduler | 34 | 6 | 1 | certified |
| modernize-scientific-stack | 20 | 11 | 2 | certified |
| schemelike-metacircular-eval | 99 | 61 | 8 | certified |
| write-compressor | 30 | 3 | 0 | certified |

The zero-delivery tasks are not evidence that GT was inactive: their receipts
contain private engine state and/or existing controller actuation. They are
also not evidence of model benefit. The audit keeps those claims separate.

## What this proves and what remains

Proven: the archived GT engine produced grounded, timely, hash-accounted
deliveries and recorded the fate of every effect without a duplicate or
unaccounted context fact.

Not proven: that a visible fact changed a model decision, prevented a failure,
or reduced tokens. A valid next step is an explicitly captured counterfactual
replay bundle (provider-prepared message bodies, model sampling state, and
checkpointed workspace/controller state). Until that exists, the release gate
must report model causality as `UNIDENTIFIABLE` and must not use behavioral
anchor-following as a substitute.

The new audit test is included in the central provider-free workflow and its
readiness audit. No paid smoke or 89-task run was started for this change.
