# Official comparison

## Sub-features

Outcome binding, pair identity, configuration equality, treatment validity,
positive/negative flips, confidence intervals, efficiency, and observed uptake.

## How to get to it (user POV)

Bind official results with `record-outcome` or `record-harbor-outcomes`, then run
`gt-harness compare --baseline <receipts> --treatment <receipts>`.

## Driving it with CLI

Use only terminal `gt.run_receipt.v1` documents with content-addressed official
evaluator bindings. Require comparison status `COMPLETE`.

## Gotchas

Different task revisions, scaffold versions, routes, budgets, or verifiers make
the comparison invalid. An errored task is not an ordinary unsolved task.
