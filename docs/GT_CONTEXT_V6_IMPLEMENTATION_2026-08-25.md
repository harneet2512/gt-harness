# GT context v6 implementation record

Status: implemented locally; exact-SHA Linux product certification is required
before paid benchmark dispatch. The canonical command is identical in a
Codespace or the registered prerelease GitHub Actions fallback.

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
6. every occurrence of the noun Ã¢â‚¬Å“testÃ¢â‚¬Â was typed as validation work;
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
delivery proof. The adopted lesson is the product behaviorÃ¢â‚¬â€compact
process/change/test answers delivered at the decision pointÃ¢â‚¬â€not GitNexus's
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

## First hosted certification finding

The first exact-SHA hosted run, `32911041329` at `7eaab4f`, correctly returned
`NOT_CERTIFIED`. Ten real-repository matrix cases, graph truth, graph
lifecycle, all six language lifecycles, dense model checksum, Go, lint, and the
failure campaign passed. The blockers were independently reproduced:

- the provider-free E2E witness discarded Mini-SWE's output `extra` metadata,
  then falsely reported that GT had failed to preserve its trajectory receipt;
- three historical assertions still demanded Mini-SWE 2.2.8 after the product
  and all canonical benchmark workflows moved to 2.4.6; and
- one assertion still demanded DeepSWE parallelism 4 after the canonical
  workflow moved to the frozen smoke requirement of 20.

The E2E witness now mirrors Mini-SWE 2.4.6 by copying output metadata into the
trajectory message, with direct regression coverage. Obsolete 2.2.8 and
parallelism-4 assertions were replaced with the current product contract, and
the unused 2.2.8 Pier adapter alias was removed. A later run must certify the
new exact SHA; the failed run is retained as evidence and is not relabeled.

## DeepSWE smoke delivery and grading defects

DeepSWE run `32913521485` at source SHA
`ca068e11a8ea000df38c5e776ef1c472a0372636` exposed two independent harness
defects. This run is retained as failed experimental evidence and is not a
valid solve-rate comparison.

First, twelve early tasks built query-ready exact-revision graphs but failed
before the first provider call with `FAILED:context_budget_too_small`. The
failure was deterministic context serialization, not provider or model
behavior. Reproduction on the exact ABS and aiomonitor repositories found four
causes:

1. every unresolved task facet was serialized even when no delivered fact
   referenced it;
2. compact process rows repeated their persisted per-edge proof ledger;
3. unqualified new APIs such as `delete_snapshot`,
   `format_snapshot_task_list`, and `task_id` promoted unrelated generic
   symbols named `delete`, `format`, and `task`; and
4. paragraph-level symbol facets duplicated sentence-level task-contract
   obligations, while rank-only inspection edges could inherit task authority.

The correction serializes only requirements referenced by delivered evidence,
keeps the full edge ledger in the persisted graph-projection receipt while
removing it from compact provider text, blocks generic unqualified prefix
analogs, deduplicates paragraph/sentence obligations, and requires an
integration edge to touch a primary task identity. Unscoped relationships are
not provider-visible facts. The exact local witnesses now produce:

- ABS Go task: `ACTIVE`, 448/500 bounded tokens, exact `BeginRepl` target,
  bounded process, and impact evidence;
- aiomonitor Python sparse path: `ACTIVE`, 439/500 tokens, no errors; and
- aiomonitor production `hybrid_required` path with the checksum-pinned
  Snowflake model: dense `READY`, `ACTIVE`, 490/500 tokens, with the real
  monitor target, package surface, CLI integration, certified import edge,
  value-flow fact, and bounded process.

Second, Pier completed two later tasks and wrote official rewards under its
canonical aggregate schema
`stats.evals.<agent/model/dataset>.metrics[].reward`, but GT's result
standardizer ignored that field and emitted `ERROR` verifier receipts. The
actual aggregate result gives `abs-stepped-slices` reward `1.0` and
`claude-code-by-agents-recursive-delegation` reward `0.0`. The standardizer now
reads the aggregate schema, rejects conflicting reward representations, and
has regression coverage for both behavior classes.

The setup-failure path also now preserves the normalized error reason, zeroed
usage, full treatment receipt, and transcript receipt. These changes remain
uncertified until a new exact-SHA Linux product certification passes. No paid
rerun may use them before that gate.

Trajectory inspection of the first two tasks that crossed the provider
boundary found a separate relevance defect. On
`claude-code-by-agents-recursive-delegation`, the old packet promoted an
unqualified `agent_id` prefix to a generic `Agent`/`agent` symbol, first
selecting an unrelated Swift model and later a generic chat-local variable.
The exact retrieval channel also classified natural file-name token overlap as
an Ã¢â‚¬Å“exact candidateÃ¢â‚¬Â; the context compiler then excluded that rank-only row
from both edit and inspection sets. Consequently the strongest deterministic
artifact match, `backend/handlers/multiAgentChat.ts`, disappeared.

The follow-up correction makes identity authority explicit, blocks generic and
`*_id` unqualified prefix analogs, retains multi-token task/path matches as
inspection evidence only, and ranks stronger path agreement ahead of repeated
dense matches from an unrelated subsystem. A strong path match can scope
certified adjacent graph edges without becoming edit authority; unscoped
public, integration, and relationship facts remain hidden. Compaction retains
that causal starting file and up to two certified relationships before
secondary impact rows.

A single long distinctive artifact token is also sufficient inspection
evidence (for example `shorthand.js` or `multicolumn.ts`), while generic task
language remains excluded. This closes the same measured omission on the
CSSTree and KaTeX trajectories without promoting those files to edit authority.

The exact-revision production witness at repository SHA
`5e0a2247d446c49a9951a06bb83b6e956dc7eb41` now emits an `ACTIVE` 419-token
packet containing:

- `backend/handlers/multiAgentChat.ts` as explicitly non-edit-authoritative
  inspection evidence;
- `backend/app.ts` as its certified integration surface;
- the certified `app.ts -> multiAgentChat.ts` import;
- the certified `multiAgentChat.ts -> providers/registry.ts` import;
- bounded impact through the existing multi-agent test; and
- `backend/tests/handlers/multiAgentChat.test.ts` as an affected test.

Independent reference-patch inspection confirms that the actual change touches
`multiAgentChat.ts`, `providers/registry.ts`, `providers/anthropic.ts`, and
`providers/types.ts`. GT therefore now provides the correct starting subsystem
and one of its true changed dependencies without injecting or reading the
reference patch at runtime. The reference patch is audit evidence only.

## Completed invalid smoke and final budget-floor correction

Run `32913521485` eventually completed all 20 task jobs. Its canonical Pier
aggregates establish eight real graded agent executions: four solves and four
failures. Twelve other tasks never called the provider because GT rejected its
own initial context as `FAILED:context_budget_too_small`. The run's attestation
reported only eight graded tasks because the old reward binder also ignored
Pier's aggregate reward representation. It is therefore diagnostic evidence,
not an official GT-on solve-rate result.

On the eight provider-executed tasks, the same local Mini-SWE-Agent 2.4.6 Ox
Alpha baseline solved five. GT solved four: four were solved by both, three were
failed by both, and `anko-default-function-arguments` was baseline-only. GT used
804 provider calls versus 912, and 71,398,391 input-plus-output tokens versus
77,879,158. Those efficiency numbers apply only to the eight executed tasks;
they cannot redeem the twelve treatment failures or certify causal uplift.

The final task, `oxvg-structural-selector-preservation`, earned reward `0` in
both arms. GT's old context contained a real reference-patch file,
`crates/oxvg_ast/src/style.rs`, but omitted the two optimizer jobs that defined
the complete change surface. The model independently found and edited
`collapse_groups.rs` but missed `remove_empty_containers.rs`; two of six
fail-to-pass tests remained failing. This is consistent with the measured old
delivery weakness: real graph facts were present, but higher-order localization
was incomplete.

Exact-SHA provider-free certification `32918140989` at `790aaec` passed install,
doctor, Python, Go, lint, the ten-repository matrix, graph truth, graph
lifecycle, six-language lifecycle, the pinned dense model, and the failure
campaign. It correctly failed `harness_e2e`. Reproduction on the exact
itsdangerous revision proved that the renderer's minimum retained role set
could require 501 or more bounded tokens; it then failed the whole treatment
instead of discarding a weaker semantic row.

The renderer now drops weak semantic noise before any decision-grade floor,
bounds persisted process/impact text, and removes a redundant validation
command when an affected-test fact is already present. It continues to retain
the exact target, scoped public/integration boundaries, a certified relation,
process/impact, and affected test. The real Mini-SWE-Agent 2.4.6 E2E now passes
with a 468-token initial packet and 435-token post-edit packet, exact-revision
dense rebuild/reopen, same-observation delivery, raw-output preservation, and
claim reconciliation. The E2E campaign now derives the installed Mini-SWE
version and refuses to run unless it is exactly 2.4.6; it can no longer
hardcode a false scaffold receipt. Hosted exact-SHA certification remains the
next release gate before a replacement paid smoke.

Certification run `32920899809` passed every provider-free gate at exact SHA
`72f9d8c472465ddccab3cc690729b1a9e7144073`. Receipt inspection then found a
separate benchmark-verifier drift before paid dispatch: the DeepSWE and TB2
attestations still rejected provider deliveries above 350 tokens even though
the canonical treatment, product certifier, and this implementation record all
set the proven delivery ceiling to 500. Harbor and Pier do not impose the stale
350-token value. Both workflow attestations now enforce 500 and report the
accurate `delivery_context_budget_exceeded` error. Regression tests bind both
workflows to that product contract. Because this changes the benchmark source
SHA, another exact-SHA hosted certification is required before dispatch.

The next dispatch, run `32922757431`, was cancelled as soon as its first task
proved one remaining pre-provider overflow on the multi-obligation Go task
`abs-module-cache-flags`. The graph was real and query-ready (164 discovered
repository files, 83 graph inputs, 41/41 dense documents, no failed dense
files), but no provider call occurred because the minimum serialized packet
was 531 tokens. Direct stage instrumentation showed repeated opaque task-facet
hashes consumed the final 31-token excess; the graph facts themselves were not
the problem.

The provider view now assigns deterministic packet-local requirement aliases
(`R1`, `R2`, and so on), retains an explicit `+N` count when a fact binds more
requirements than fit, and preserves the complete original facet ledger in the
packet receipt. Process truncation now stops on a complete graph hop and marks
`truncated=true` instead of cutting a symbol or path mid-token. A provider-free
reproduction on the exact ABS commit `cb1b3b671d0ee9fa9da9f7b02f86967953ffd10a`
and original task text produces an `ACTIVE` 457-token packet with the exact
`BeginRepl` edit target, `Environment` inspection boundary, `main` integration
boundary, verified import edge, bounded process, impact, and uncovered cache
API facet. The dense index is `READY`, the graph is
`READY_WITH_DECLARED_LIMITATIONS`, and provider calls remain zero.

## Awilix multi-obligation overflow and semantic compaction

Exact-SHA certification run `32924707649` passed every provider-free product
gate at commit `170792cc350f8acd9a4ee13956b93ece10873566`. The replacement
DeepSWE smoke, run `32925444016`, was then cancelled after its first terminal
task exposed another pre-provider overflow. Task
`awilix-async-container-initialization` built a real `READY` graph at repository
commit `82ac179c1de4c216c4e333093044fac643303f0c` (69 discovered files, 62
graph inputs) and a `READY` dense index (48/48 documents, zero failures), but
the treatment failed with `FAILED:context_budget_too_small`. Mini-SWE-Agent was
the required version `2.4.6`; provider calls and provider tokens were both
zero. The task is therefore an invalid treatment execution, not an agent loss.

The exact task text, repository revision, checked-in graph indexer, pinned
Snowflake ONNX model, and `hybrid_required` mode reproduce the failure locally.
After the ABS correction, the Awilix packet's final decision-grade floor was
still 564 bounded tokens. The residual excess had four deterministic causes:

- equivalent natural-language obligations serialized identical requirements
  more than once;
- facts could reference aliases whose requirement definition was not visible;
- long process/impact claim IDs and an opaque uncovered-facet ID consumed
  provider tokens despite being fully persisted in receipts; and
- impact text could be cut in the middle of a token.

The provider serializer now groups equivalent requirements by their readable
role/symbol signature and binds duplicate facets to one packet-local alias.
Only declared aliases can appear in provider facts; omitted distinct
requirements are represented as `+N`. Full task-facet and projection claim IDs
remain in the persisted treatment receipt, while the provider sees unambiguous
packet-local prefixes. Uncovered facets expose their actionable role and
unresolved symbols without implying that an uncovered obligation was satisfied
by a delivered requirement. Process and impact truncation both end on a
complete boundary and state `truncated=true`. If the hard ceiling is still
exceeded, phrase/rank-only inspection is removed before verified edit,
public-surface, integration, relationship, process, impact, or affected-test
facts.

The exact real Awilix reproduction now returns `ACTIVE`, one delivery, zero
errors, and a 499/500-token packet. It retains `src/resolvers.ts#asClass` as the
edit target, `src/awilix.ts` as the public surface, `src/container.ts#container`
as the integration boundary, the verified import of
`AwilixResolutionError`, a bounded process, bounded impact, an affected test,
and an explicit uncovered initialization facet. Regression coverage reproduces
the duplicate-obligation and dense-role shape, verifies that every referenced
alias is declared, preserves full projection claims in the receipt, and rejects
mid-token projection truncation. The complete Python suite, Ruff, and all Go
module packages pass locally. This correction remains uncertified until it is
committed and a new exact-SHA hosted product certification succeeds; no
replacement paid smoke may start before that gate.

## Run 32928374228 mislocalization regressions and the localization truth gate

The completed DeepSWE smoke at exact SHA eac111b graded 20/20 tasks with
every treatment mechanically valid, yet produced 10 solves against the frozen
baseline's 15: eight both-solve tasks, two positive flips
(daptix-name-mapping-aliases, oxvg-structural-selector-preservation),
three both-fail tasks, and seven baseline-only regressions. Post-run artifact
audits plus a provider-free replay of all twenty cohort tasks at their exact
revisions attributed the losses:

- rktype-json-schema-refs-dependencies: the quoted prose noun 'type'
  bound as an exact symbol and promoted an unrelated attest assertion helper;
- andit-interprocedural-taint-checks: the qualified acronym owner in
  CWE.SQL_INJECTION case-matched a same-named repository class and granted
  it edit authority;
- oa-hierarchical-evaluation-cancellation: treatment timeout at the task
  wall clock (empty patch), not mislocalization;
- 	estem-bail-on-test-failure, wilix-async-container-initialization,
  pest-character-class-coalescing, ctionlint-action-pinning-lint:
  correct or honestly-absent localization; implementation-depth losses.

The compiler now blocks generic lowercase prose nouns from symbol identity,
requires exact-case matches for short ALL-CAPS tokens, demotes entry-file
self-named symbols to inspection evidence, keeps throw/raise exception cues
as retrieval vocabulary only, demotes globally unscoped cross-file name
collisions unless certified export structure connects them, grants zero-
facet exact-path rows edit authority only when the task cites the file, and
orders decision-grade roles ahead of rank-only rows during compaction.

The standing pre-dispatch gate is now
[scripts/localization_truth_gate.py](../../scripts/localization_truth_gate.py)
verifying the committed, fingerprint-bound
[smoke20 truth report](deepswe_smoke20_localization_truth.json): after the
repairs the provider-free cohort replay delivers 20/20 treatments, zero wrong
edit targets, and mean edit-target precision 1.0. The typed flip ledger for
the paid run is [deepswe_smoke20_flip_ledger.json](deepswe_smoke20_flip_ledger.json).
## Latent-regression sweep and benchmark-readiness gates (2026-08-26)

A provider-free sweep of the compiler, task contract, delivery layer, test
suite, and workflows surfaced latent major-regression risks beyond the
localization recall gap. Repairs landed in this batch:

1. **Obligation dedup** (gt_engine/task_contract.py): substring dedup
   dropped a distinct obligation whose normalized key contained an earlier
   key (Create foo.txt.bak behind Create foo.txt). Dedup is now exact-key
   only, so every distinct obligation becomes a facet.
2. **Directive coverage**: _DIRECTIVE_RE missed the edit family
   (ix/update/patch/refactor/bug); non-bullet prose obligations such as
   Fix NPE in Foo when config is null never entered extract_task_contract
   and thus never produced a facet. The directive set now covers them.
3. **Path-citation false positive**: _task_cites_path matched a bare
   extensionless filename token (config) against the prose word in
   Fix config handling, granting a wrong zero-facet edit target. The bare
   filename now requires a word boundary; full normalized paths still match
   as substrings.
4. **Dense file-anchor poisoning**: a dense-inspection candidate could seed
   graph-expansion ile_anchors, so certified RE_EXPORTS/CALLS from a
   semantically-similar-but-irrelevant file promoted spurious
   public-surface/integration rows. Dense-only file anchors are now excluded
   from ile_anchors.
5. **Truncation honesty**: packet 	runcated ignored repository-side
   branch/expansion truncation, so a high-fan-out graph could claim
   	runcated=false with a partial process/impact view. Repository
   truncation reasons now propagate to the packet.
6. **Ambiguity demotion correctness** (from the prior localization batch):
   the per-symbol-group demotion previously demoted owner-module-scoped
   members and kept globally-unscoped collisions as edit targets; it now
   demotes per-row members with any globally unscoped facet match unless a
   certified export edge connects the files (facade).
7. **Non-vacuous tests**: every localization regression test now carries a
   positive control proving the guarded binding occurs before asserting its
   absence, so an empty/broken compiler cannot pass.

### Benchmark-readiness gates

scripts/localization_truth_gate.py now enforces both precision (>= 0.7)
and recall (>= 0.5) on the fingerprint-bound smoke20 truth report, with
scripts/replay_smoke20_localization.py reporting mean_edit_target_recall
in the summary. The gate is a hosted certification step via
scripts/codespaces_product_certification.sh.

Current measured state: mean edit-target precision 1.0, mean recall 0.0845,
12/20 zero-target tasks. Recall remains red against the 0.5 floor and is the
remaining blocker to benchmark-ready status. The recall work is the
decision-point delivery of bounded process/impact/test answers on file-read
observations (the GitNexus 
ative_augment lesson), typed
AMBIGUOUS_IDENTITY rows, inspection structural-relevance filtering, and
truth-report regeneration under hybrid_required. No paid benchmark was
run; readiness is proven by the provider-free gates above.