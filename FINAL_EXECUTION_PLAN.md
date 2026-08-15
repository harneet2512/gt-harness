# GroundTruth Final Execution Plan

## Proof hierarchy

GroundTruth must be evaluated at four separate layers:

1. **Retrieval:** certified repository evidence was found.
2. **Delivery:** selected evidence reached the exact next provider request.
3. **Reasoning utility:** the model's observable next action improved with that evidence.
4. **Product outcome:** the complete agent trajectory improved without harmful overhead.

Agent Retrieval Bench measures only layer 1. It is a necessary mechanism test,
not evidence that the model used the facts or that tasks were solved.

## Execution order

1. Freeze scope and verify P0/P1 defects against current code.
2. Prove runtime delivery, abstention, revision binding, and fail-open behavior.
3. Run a gold-isolated ARB adapter through the production retrieval path.
4. Allow at most one generalized retrieval repair if one repeated failure class is proven.
5. Evaluate paired model decision points: identical request with and without the exact GT evidence.
6. Freeze GT, model, harness, graph, thresholds, prompts, and containers.
7. Run DeepSWE v1.1 first through the frozen Mini-SWE/Pier workflow, using an
   exact matched control artifact (existing only if it passes every identity
   and uncensored-outcome gate).
8. Run Terminal-Bench 2.0 next as the frozen Mini-SWE product diagnostic,
   reporting all-task and source-applicable groups and explicitly making no
   Terminal-Bench 2.1 leaderboard claim.
9. Run a contemporaneous same-wrapper SWE-bench-Live A/B only if it remains
   necessary for the final product claim.
10. Write the final causal report and stop the project.

## Decision-point rule

Only first-visible-intervention points are eligible. The control request and
treatment request must have identical canonical provider bytes except for the
bounded production GT evidence. No marker or acknowledgement is added. The
next action is graded mechanically against certified paths, symbols, tests,
obligations, validation state, or known failures. Outcomes are beneficial,
harmful, equivalent, or indeterminate. Internal model acknowledgement is not
claimed.

The reasoning gate requires valid grounded/timely evidence, at least 20
gradable points when available, and more beneficial than harmful observable
changes. Underpowered evidence is reported as inconclusive rather than inflated
into proof.

## Non-negotiable boundaries

- Production preflight remains SHADOW.
- No new GT architecture, LLM call, planner, MCP layer, or speculative feature.
- No paid call without explicit authorization at that stage.
- No benchmark-specific heuristic or post-outcome tuning.
- No GT code changes after the final manifest is frozen.
