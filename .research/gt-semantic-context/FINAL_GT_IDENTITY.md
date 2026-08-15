# Final GroundTruth identity

**Date:** 2026-08-14  
**Architecture:** D — thin deterministic evidence compiler  
**Status:** implemented in this tree

## What GroundTruth is

GT compiles **certified ∧ novel ∧ material** evidence for a stock Mini-SWE coding agent.

- At most one bootstrap `select_catalog` call (IDs only).
- After that, zero GT generative calls.
- Private persistent execution state is updated at every eligible host boundary.
- Provider text is emitted only when a fact is decision-relevant.
- Unknown evidence abstains. Adjacent graph relations stay private.

Executable contract: `gt_engine/thin_compiler.py`.

## What GroundTruth is not

- A second coding agent, planner, or critic
- A localization product
- A rich semantic-requirement / ontology graph
- A scavenger hunt for one-off task packets

## Provider-visible material classes

- Certified `CALLS` / `ASSERTED_BY` / `verified_closure` to preexisting files
- Unresolved task obligations
- Declared validation status changes and attributable failures
- Syntax failures
- Repeat-failure evidence (not strategy coaching)
- Signature deltas **with** certified preexisting callers
- Declared-check validation debt

## Never provider-visible

- `imports` / `imported_by` / `implements` / `inherits` / `references` adjacency
- Model-authored change-surface self-echo (`newfile_precedent`, caller-less `signature_delta`)
- Task-start localization / ranked-anchor coaching
- Bootstrap-ordered catalog dumps
- Hidden REF / oracle contents
- Plans, hypotheses-as-advice, or “change your approach” coaching

The 17 historical feature IDs remain the census registry. Their **delivery policy** is the thin compiler, not 17 competing model-visible surfaces.

## What this does not claim

This is the product identity and delivery contract. It does not claim solve-rate uplift, efficiency wins, or a positive-flip exhibit. Those still require a matched authorized evaluation.
