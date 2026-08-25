# GT context v6 implementation record

Status: implemented locally; exact-SHA Codespaces verification is required
before paid benchmark dispatch.

## Defects demonstrated

The official DeepSWE GT artifacts from run `32892235130` contained 73
provider-visible GT packets. Forty-three packets contained an
`EXACT_EDIT_TARGET`, but none contained `SEMANTIC_FACT`, `BOUNDED_PROCESS`,
`BOUNDED_IMPACT`, `AFFECTED_TEST`, or `VALIDATE`. Internal projection receipts
nevertheless recorded process and impact IDs as delivered. The treatment was
therefore both weak at the model boundary and incorrect in its delivery ledger.

The deterministic causes were:

1. provider compaction deleted semantic/process/impact/test/validation facts
   before low-authority candidate and uncertainty rows;
2. projection IDs were registered even when the corresponding line was absent
   from serialized provider context;
3. a treatment update was generated once per shell action rather than once per
   assistant/provider turn;
4. generic directory listings dirtied localization state;
5. candidate paths were eligible for `FOLLOWED`, and a repository-wide test
   pass could manufacture feature-level `VALIDATED`;
6. every occurrence of the noun “test” was typed as validation work;
7. backticks were treated as symbol authority, including configuration values;
8. dense retrieval embedded the entire task as one query instead of preserving
   independent requirements; and
9. the 350-token update ceiling discarded a real changed-revision packet whose
   measured compact representation required fewer than 500 tokens.

## Implemented production changes

- `gt.agent_context.v6` retains decision-grade facts before candidate noise.
- Every role-bearing target carries a task-requirement binding; requirement
  rows retain `EDIT`, `PUBLIC_SURFACE`, `INTEGRATION`, or `VALIDATION` authority.
- Export-only work no longer promotes the underlying definition to an edit
  target. Public/integration tasks with explicit implementation verbs receive
  a separate edit responsibility rather than collapsing both roles.
- Quoted configuration literals remain retrieval terms and cannot become exact
  symbol authority merely because a same-named symbol exists.
- Dense retrieval runs once per bounded task obligation and fuses paths with
  deterministic reciprocal-rank fusion. Dense results remain inspection/rank
  evidence, never edit authority.
- Mini-SWE executes all actions in an assistant response, then permits at most
  one same-observation GT augmentation on the final observation.
- Provider call timing is recorded independently from shell action count.
- Every serialized packet records its full claim IDs, visible feature counts,
  provider call, context hash, token count, and source revision. Finalization
  fails reconciliation when the receipt union differs from GT's delivery set.
- Candidate-only paths cannot advance behavioral lifecycle state. Validation
  requires overlap with a delivered feature path; a broad passing test command
  alone cannot certify unrelated GT advice.
- The update ceiling is 500 bounded tokens. This is the smallest configured
  ceiling that passed the real add/modify/rebuild/reproject witness without
  silently dropping the update.

## Deliberate limits

The source-semantic fact compiler remains Python-AST-specific. TypeScript,
JavaScript, Go, Rust, and other supported graph languages still receive exact
tree-sitter symbol/relationship facts plus persisted process and impact
projections, but they are not represented as having Python-style value-flow
facts. This is explicit limitation behavior, not silent semantic support.

No GitNexus code was copied. GT retains its own exact-revision graph, pinned
local dense index, fail-closed readiness, lifecycle refresh, and claim-level
delivery proof. The adopted lesson is the product behavior—compact
process/change/test answers delivered at the decision point—not GitNexus's
implementation or its weaker stale/delivery accounting.

## Verification contract

Provider-free verification must prove all of the following at the exact
candidate SHA before any paid smoke:

1. task role/literal authority regression tests pass;
2. semantic, process, impact, affected-test, validation, and relationship facts
   survive realistic compaction;
3. hidden projection IDs never appear in delivery receipts;
4. multiple tool actions produce at most one augmentation;
5. delivery-call numbering is provider-call numbering, not action numbering;
6. serialized claim union equals final delivered claim union;
7. a real repository edit rebuilds the graph, changes source revision, emits an
   update within 500 tokens, and reopens query-ready;
8. localization replay, benchmark product contract, Harbor/Pier adapters, and
   all provider-free product tests pass in GitHub Codespaces; and
9. the workflow installs Mini-SWE-Agent 2.4.6 and runs only `gt-harness run`.

The subsequent 20-task smoke is evaluation, not implementation proof. It is
valid only after these checks pass and only against an identical 2.4.6
baseline/task/model/budget/verifier set.
