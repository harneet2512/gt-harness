# GT visible-context repair (2026-08-08)

## Problem

The 10-task smoke `31251650010` produced 308 applied effects, but only three
effects were classified as direct provider payloads. The separate context
frontier delivered 16 source frames. This was not evidence that the remaining
engine work was unused: most effects are deliberately controller-private.
However, the frontier compiler had a real visibility hole. It accepted only
definition, caller, and reference rows. A healthy, current graph could return a
ranked source anchor with a concrete path/line/symbol while returning no
structural role. That evidence was then accounted as `NO_FRONTIER`, so the
model never received the source fact.

## Repair

`gt_engine/context_frontier.py` now converts eligible ranked anchors into a
bounded fallback fact:

* `SYMBOL` when a symbol is present, otherwise `FILE`;
* positive source line and normalized authored path are required;
* semantic certainty and task retrieval relevance remain independent and must
  pass the existing 0.95 thresholds;
* the fact carries the source and graph revisions and the semantic relation
  `task_anchor`;
* structural roles win when they identify the same path/line/symbol;
* represented history, prior claim delivery, stale revisions, low precision,
  the three-fact/1,200-character call budget, and the 6,000-character task
  budget still suppress delivery exactly as before.

This is not a generic advice stream. It names only a certified source anchor;
it does not invent a definition, caller, reference, intent, or predicted
action. Unhealthy or incomplete graph evidence still produces no payload.

## Proof

The new red/green tests are
`tests/test_gt_intelligence_layer.py::test_frontier_delivers_ranked_anchor_when_no_structural_role_exists`
and
`tests/test_gt_central_agent.py::test_context_frontier_exposes_anchor_only_evidence_in_provider_request`.
The first initially failed with `NO_FRONTIER`; the second proves the rendered
`legacy.cob:42` frame appears in the exact first model request. Focused
provider-free results:

* intelligence + consumer proof: **63 passed**;
* frontier/preflight/central-agent focused set before the end-to-end addition:
  **38 passed**;
* combined frontier/preflight/consumer/central-agent selection after the
  addition: **94 passed**;
* `python -m compileall -q gt_engine eval scripts`: passed.

The broader readiness audit remains blocked by the existing vendored index
runtime substrate failure (`certified language parser coverage missing:
cobol=0 scheme=0`). That is not caused by this frontier patch and must be
repaired before any paid smoke. No paid smoke or 89-task run was started.

## Interpretation

The fix should increase model-visible context only for healthy anchor-only
repository evidence. It does not, and must not, turn all private GT effects
into prompt text. Direct feature payloads, context-frontier frames, engine
state, and audit-only receipts remain separate metrics. The `gpt2-codegolf`
substrate failure in the smoke still requires the independent Python-free
workspace capture/index refresh repair.
