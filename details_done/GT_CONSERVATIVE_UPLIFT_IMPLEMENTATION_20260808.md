# GT conservative uplift implementation

Date: 2026-08-08
Branch: `inline-engine`
Status: implementation complete and provider-free verified. Promotion is
blocked on an exact clean pushed commit, an authorized paid component
experiment, and repeated outcome-first release evidence. No paid model run was
started.

## Decision

Workflow `31282615178` is not a promotion witness. It resolved 8/10 against the
frozen GT-off reference's 9/10. On the eight common solved tasks, treatment
resources increased by 24.54% tokens, 26.82% API calls/assistant steps, and
22.74% model actions. The apparent all-task token reduction was dominated by
different failed trajectories. It is not an efficiency win.

The repair does not claim that deterministic context makes a temperature-1
model deterministic. A provider-visible byte change can alter sampling. What
GT can make deterministic is the evidence and control boundary: whether a fact
is authoritative, current, needed, complete, timely, deduplicated, and applied.
Outcome causality requires repeated contemporaneous controls.

## Research basis

- Safe Policy Improvement with Baseline Bootstrapping motivates falling back
  to a baseline policy when evidence for an improvement is insufficient:
  <https://proceedings.mlr.press/v97/laroche19a.html>.
- Selective Classification via One-Sided Prediction supports an explicit
  abstention region rather than forcing a prediction/intervention on every
  input: <https://proceedings.mlr.press/v130/gangrade21a.html>.
- High Confidence Policy Improvement requires confidence-bounded evidence
  before promoting a policy: <https://proceedings.mlr.press/v37/thomas15.html>.
- On Randomness in Agentic Evals documents why single stochastic agent runs
  are not reliable causal estimates: <https://arxiv.org/abs/2602.07150>.
- SWE-agent shows that the agent-computer interface materially shapes coding
  behavior, so a context/control change is part of the treatment:
  <https://papers.neurips.cc/paper_files/paper/2024/file/5a7c947568c1b1328ccc5230172e1e7c-Paper-Conference.pdf>.
- Lost in the Middle shows that adding relevant context can still impair use
  of other context depending on placement and load:
  <https://aclanthology.org/2024.tacl-1.9/>.
- SWE-Explore and CodeMonkeys support repository exploration and parallel or
  staged evidence gathering, but neither justifies injecting unbounded graph
  retrieval into every model request: <https://arxiv.org/abs/2606.07297>,
  <https://arxiv.org/abs/2501.14723>.

The resulting product rule is conservative: correct-or-quiet evidence,
baseline-preserving provider history, explicit component ablations, and
repeated outcome-first release evidence.

## Implemented architecture

```text
Mini-SWE provider call
  -> model-selected Bash action
  -> typed ProposedAction
  -> deterministic preflight (paid workflow: SHADOW)
  -> literal host execution
  -> postflight, workspace/source revision, validation, graph update
  -> feature/controller candidate
  -> CertifiedOpportunity
       -> abstain / controller-only / one bounded provider delta / auto-submit
  -> exact stock provider history unless measured budget pressure requires a
     reasoning-preserving compaction epoch
  -> next provider call
```

### Common opportunity certification

`gt_engine/uplift_policy.py` introduces:

- `GTPolicyMode`: OFF, AUDIT, CERTIFIED_SHADOW, CERTIFIED_ACTIVE;
- evidence authority: MECHANICAL, CERTIFIED_STRUCTURAL, HEURISTIC, UNKNOWN;
- typed opportunity kinds and dispositions;
- stable opportunity IDs;
- conjunctive `certify_opportunity()` with a one-call delivery window.

The following all use this authority:

- ordinary feature guidance in `CentralFeatureRuntime.model_feedback()`;
- context-frontier graph facts;
- admitted preflight return-to-model decisions;
- completion-controller auto-submit.

Timeout, ambiguity, heuristic evidence, stale revision, a missing anchor,
represented evidence, missing decision need, and a late window abstain. No LLM
or learned score exists inside GT.

### Complete-or-quiet facts

`SemanticDecisionEngine.materialize()` no longer slices the first oversized
fact. `render_runtime_advisory()` no longer appends an ellipsis. A fact that
cannot fit completely stays private and expires with its original delivery
window. This prevents a true diagnostic from becoming a misleading fragment.

### Provider baseline shield

`ProviderViewSession` is exact before an actual provider-budget compaction
epoch. The prior soft character trigger is removed from the agent loop. The
current successful requested read/search observation remains exact. During a
real compaction epoch, only older tool bodies may be converted to bounded
hash/return-code receipts; assistant content and reasoning remain immutable.

Every provider call now records:

- stock provider characters and hash;
- feature-guidance characters;
- certified-graph characters;
- compaction removed and receipt characters;
- final provider characters and hash;
- exact changed message indices;
- whether and why the provider view changed.

The implementation deliberately does not store a complete cumulative request
snapshot for every call. That would duplicate an O(turns squared) history and
create the observer overhead the repair is meant to remove. The durable
trajectory, deterministic replay, exact hashes, and component/change-index
ledger are sufficient to reconstruct and compare provider boundaries.

### Graph authority and action-conditioned retrieval

Raw FTS/BM25 position is candidate ordering only; it now carries zero retrieval
relevance. `rank_graph_evidence()` alone assigns relevance from exact active
paths, typed task-resource paths, or distinctive exact subjects/symbols.

After an executed typed READ, SEARCH, EDIT, or CREATE names a real
validation-relevant source path, `RepositorySession.query()` re-ranks the
already-current graph for that exact path. It does not rebuild the index and it
caches identical source-revision/path queries. This makes graph intelligence
available at the actual Mini-SWE decision boundary without predicting intent.
Delivery still requires both certainty and relevance >=0.95 plus an exact
path/symbol in provider-visible history.

Frontier claim IDs no longer include line numbers or source/graph revisions.
Line movement cannot reopen a delivery. Versioned fact IDs still preserve
provenance.

### Policy and component arms

The host has one policy switch and one central engine. The paid workflows now
offer:

| Arm | Provider evidence | Graph frontier | Compaction | Completion/progress/adaptive controls | Preflight |
| --- | --- | --- | --- | --- | --- |
| `off` | no | no | no | no | OFF |
| `audit` | no | no | no | no | SHADOW |
| `certified_context` | certified feature facts | yes | no | no | SHADOW |
| `certified_controllers` | no | no | no | yes | SHADOW |
| `certified_full` | certified feature facts | yes | measured-budget only | yes | SHADOW |

Default workflow arm is `audit`. Assistive preflight is implemented and
provider-free tested, but it is not enabled in a paid arm without a separate
authorization and matched experiment.

### Repeated release evaluator

`gt_engine/experiment.py` adds balanced deterministic ABBA/BAAB assignment and
a repeated OFF versus certified-full release gate. It requires:

- identical task sets;
- at least two trials per arm per task;
- balanced arms and unique trial IDs;
- tasks as the top-level bootstrap unit and within-task repeat resampling;
- failure-capped tokens, actions, calls, assistant steps, effective task
  actions, and wall time;
- a solve-rate lower confidence bound;
- resource-ratio upper confidence bounds;
- treatment-only and control-only solve reporting.

The historical frozen baseline remains useful for descriptive deltas. It is no
longer sufficient release evidence.

## Measurement semantics

The engine hierarchy remains:

1. a feature receipt proves observation;
2. an applied effect proves controller consumption;
3. a certified opportunity proves eligibility for a consequence;
4. a provider hash/message index proves exact visibility;
5. next-command anchor alignment is a behavioral proxy;
6. repeated matched arms are required for causal outcome/resource claims.

Zero *added* context is not automatically zero GT work. A fact may be already
represented in stock Mini-SWE history or may correctly remain controller-only.
Conversely, private production alone is not model help. Every candidate must
have one explicit disposition; forcing text solely to avoid a zero is a false
intervention.

## Files changed

- `gt_engine/uplift_policy.py`
- `gt_engine/central_runtime.py`
- `gt_engine/context_frontier.py`
- `gt_engine/semantic_decisions.py`
- `gt_engine/provider_view.py`
- `gt_engine/graph_context.py`
- `gt_engine/repository_intelligence.py`
- `gt_engine/experiment.py`
- `gt_engine/deep_metrics.py`
- `eval/gt_central_agent.py`
- `.github/workflows/tb2_miniswe_central.yml`
- `.github/workflows/tb2_miniswe_engine.yml`
- `.github/workflows/central_provider_free.yml`
- readiness/census/pre-smoke scripts and focused tests.

## Release state and remaining work

### Provider-free verification completed

The final local implementation passed:

- 376 tests across the central runtime, agent loop, preflight, repository
  intelligence, provider view, replay/run-diff, deep metrics, all-17 consumer
  proof, completion/progress, uplift policy, and repeated experiment gate;
- the exact 161-test lifecycle selection used by
  `central_pre_smoke_gate.py`;
- both direct and module feature-census entrypoints, including
  `CERTIFIED_OPPORTUNITY_POLICY_PROVEN`,
  `PROVIDER_BASELINE_SHIELD_PROVEN`, and
  `REPEATED_CONTROL_GATE_PROVEN`;
- the real vendored index-runtime gate: 48/48 fixture source files indexed,
  zero parser failures, valid SQLite, definition nodes, and certified directed
  caller edges including COBOL and Scheme;
- `central_readiness_audit.py` with `READY`, including the action-conditioned
  graph query before feature postflight;
- archived ten-task policy replay in both direct and module invocation forms
  with `REPLAY_OK`;
- archived 10/10-versus-8/10 run diff with ten tasks and complete accounting;
- the pinned benchmark-language witness contract;
- full workflow-scope Ruff, Python compilation, YAML parsing, and
  `git diff --check`.

The archived run-diff audit also found and repaired an observability-gate bug:
archives may contain a byte-identical task pair in both `partial` and
`corrected` extraction trees. The comparator now deterministically collapses
only byte-identical trajectory/receipt pairs and still fails closed on a
conflicting duplicate.

### Promotion work still required

No paid run has been started for this implementation. Before any paid smoke:

1. review this local diff, then commit and push the exact implementation;
2. require `central_pre_smoke_gate.py` to print `SMOKE_APPROVED` at that exact
   pushed commit;
3. obtain separate authorization for paid experiments;
4. run the staged component arms before interpreting the full treatment;
5. run balanced fresh OFF/full repeats and apply the repeated release gate.

The 89-task run remains blocked until the repeated outcome-first gate passes.
