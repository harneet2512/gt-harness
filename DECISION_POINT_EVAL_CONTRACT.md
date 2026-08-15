# Decision-Point Model-Reasoning Evaluation Contract

## Claim being tested

GroundTruth may improve the model's next external action when the exact
production evidence is present in the immediate provider request. This is an
observable action claim, not a claim about hidden reasoning or acknowledgement.

## Paired requests

Each eligible case is a first-visible-intervention point. The control request
is the normal Mini-SWE provider request. The treatment request is byte-identical
except for the bounded, grounded GT evidence emitted by the production
compiler. Requests use the same model, prompt, tools, sampling, limits, and
environment. No marker, acknowledgement request, or chain-of-thought grader is
allowed.

## Case validity

The evaluator records both request hashes, source revision, evidence claim IDs,
provenance, prior visible GT count, response hashes, and the grading oracle.
Cases with prior visible GT context, stale evidence, duplicate facts, missing
responses, or non-identical non-GT request bytes are rejected.

## Mechanical grading

The next action is compared against certified repository/task facts: target
paths or symbols, caller/test obligations, known validation failures, and
submit safety. A result is `beneficial`, `harmful`, `equivalent`, or
`indeterminate`. The evaluator never treats textual acknowledgement or later
anchor similarity as proof of use.

At least 20 gradable first-intervention cases are required when available.
Beneficial outcomes must exceed harmful outcomes. An exact paired sign test is
reported; underpowered positive results remain inconclusive. A false, stale,
duplicated, or late payload is a mechanism failure regardless of the action
result.

## Cost and authorization

Existing replay-ready bundles are preferred. If they are unavailable, a
bounded SHADOW capture requires separate paid-run authorization. This contract
does not alter the production GT loop.
