# GT feature-applicability and repository-runtime repair

Date: 2026-08-06
Implementation status: provider-free verified; no new paid smoke
89-task status: blocked

## Conclusion

The sentence “all 17 features were enabled; 13 fired naturally because four
exact triggers were absent” was not sufficiently accurate for corrected smoke
`31136099371`.

The four absent IDs had two different causes:

| Feature | Smoke diagnosis | Correct classification |
| --- | --- | --- |
| `caller_contract` | the host repository index was unavailable | infrastructure/implementation miss, now repaired |
| `def_partition` | the host repository index was unavailable | infrastructure/implementation miss, now repaired |
| `recovery` | no repeated attributable failure at one source revision | correct trigger absence |
| `signature_delta` | no mechanically observed before/after callable-signature change | correct trigger absence |

Forcing all 17 IDs to fire in every paid task would be wrong. The repaired
criterion is: every eligible lifecycle opportunity fires once with valid
evidence; every ineligible opportunity records a reasoned abstention; a missing
substrate is reported as unavailable rather than relabelled as an absent task
event.

## Archived evidence that established the defect

The ten corrected receipts under `D:\gt_runs\31136099371\corrected` contain:

- 359 effects and 13 naturally fired feature IDs;
- 38 repository refresh attempts, all 38 recorded as `index_unavailable`;
- 100 combined `localization`/`GT_LOC_RESLOT` receipts, but only four with a
  concrete anchor payload;
- no `caller_contract` or `def_partition` receipt because the graph substrate
  never became available;
- no exact repeated-failure event for `recovery`; and
- no persisted callable-signature delta for `signature_delta`.

The old audit collapsed “substrate unavailable,” “evidence ambiguous,” and
“task event absent” into one missing-ID count. That is why the 13/17 statement
looked healthier than the implementation actually was.

## Repairs implemented

### 1. The paid host now contains and proves the repository substrate

Both jobs in `.github/workflows/tb2_miniswe_central.yml` now:

1. install the vendored GroundTruth wheel;
2. mark the pinned Linux `gt-index` binary executable;
3. export its exact path through `GT_INDEX_BINARY`; and
4. execute `scripts/verify_gt_index_runtime.py` before any provider work.

The verifier crosses the real boundary: it creates source, invokes the actual
binary through the installed runtime, opens the generated SQLite graph,
performs `PRAGMA quick_check`, and proves two definition nodes plus a directed
`CALLS` edge. Static import availability is no longer accepted as readiness.

`IndexBuildReceipt` records one of `available`, `no_supported_source`,
`missing_runtime`, `missing_binary`, `build_failed`, or `invalid_database`, plus
the graph revision, certified binary hash, latency, and bounded error type.

### 2. Search output no longer invents structural semantics

One `ProposedAction` and one shell parse now feed preflight and postflight.
Search observations explicitly distinguish workspace search, targeted search,
stdin filtering, external targets, and ambiguity.

- `grep` used as a pipeline filter is not repository localization.
- heredoc/interpreter source text is not scanned as shell intent.
- `path:line:text` output is accepted only for a known path.
- `line:text` output is accepted only when exactly one target path is proven.
- an unparseable or empty result creates an abstention receipt and emits no
  empty localization effect.
- plain non-definition search hits never become verified callers.

### 3. Definitions, references, and callers use graph roles

`RepositoryEvidence` now keeps definitions, references, and callers separate.
Definitions come from graph nodes. A caller is emitted only from a directed
`CALLS` edge whose confidence is at least 0.95, trust tier is `CERTIFIED`, and
candidate count is exactly one. Caller payloads retain source path/line,
target path/symbol, resolution method, confidence, trust tier, candidate
count, and evidence type.

`def_partition` requires both definition and reference rows.
`caller_contract` requires at least one certified direct caller. Missing rows
produce `correct_abstention`; regex text never substitutes for them.

### 4. Provider timing is first-window only

The stricter census found a separate old arbitration bug: a same-edit
`signature_delta` could lose to `syntax_result` and appear in the following
model call, one step late. The decision compiler now:

- selects each claim at most once per frame;
- coalesces compatible same-action facts into the bounded first-eligible
  frame;
- retains unselected facts as controller state; and
- explicitly suppresses any provider candidate that was not selected in its
  first eligible request.

No candidate may leak into call N+2. This also removed duplicate text caused by
selecting the same impact claim through two open needs.

### 5. Per-task applicability is now measurable

Each feature receives a replayable applicability status:

- `fired_when_eligible`;
- `correct_abstention`;
- `trigger_absent`;
- `ambiguous_evidence`;
- `substrate_unavailable`; or
- `missed_trigger`.

Every evidence evaluation records boundary, action ID, source revision, reason
code, evidence hash, and effect ID when emitted. Deep metrics now report the
IDs and counts for fired features, correct abstentions, absent triggers,
eligible misses, and false fires. This replaces the misleading enabled/fired
binary.

## Lifecycle placement of all 17 features

| Feature | Evidence-correct trigger | Placement | Correct absence/abstention |
| --- | --- | --- | --- |
| `obligations` | non-empty task contract | task start | empty/unextractable contract |
| `localization` | graph-ranked task anchor or parsed repository-search anchor | task start/post-search | no linked graph anchor or unanchored search output |
| `GT_LOC_RESLOT` | bounded ranked anchors | task start/post-search | no concrete anchors |
| `def_partition` | graph definitions and distinct graph references | task start | incomplete partition |
| `caller_contract` | certified directed caller edge | task start, later consumed on signature edit | no certified direct caller |
| `newfile_precedent` | authored source creation plus non-empty related sibling | post-edit | artifact/output/empty or unrelated sibling |
| `GT_CHANGE_SURFACE` | actual authored-source workspace delta | post-edit | no material source change |
| `GT_PATCH_DELTA` | actual patch/signature delta | post-edit | no concrete delta |
| `signature_delta` | before/after callable signatures differ | post-edit | no persisted signature change |
| `syntax_result` | changed-file syntax check with attributable result | post-edit | no changed source or no attributable checker result |
| `GT_EDIT_CHECK` | changed source creates a concrete validation obligation | post-edit | no grounded changed path/check |
| `covering_red` | structurally attributable validator failure | post-validation | pass, unknown, or unattributed pipeline |
| `GT_HYPOTHESIS` | concrete failure/change evidence updates hypothesis state | post-validation/edit | no grounded discriminator |
| `recovery` | same failure repeats at unchanged source revision | post-validation | no exact repeat |
| `submit_refusal` | fresh source-bound failing required check | post-validation/pre-submit state | no current blocker |
| `GT_SS_SUBMIT_RED` | the same grounded submission-risk state | post-validation/pre-submit state | no current blocker |
| `GT_CERT_DELIVERY` | submit boundary with current sensor/check state | submit | no submit event |

The five evidence-correct postflight-only features remain
`GT_CHANGE_SURFACE`, `signature_delta`, `GT_PATCH_DELTA`, `syntax_result`, and
`covering_red`. The repair does not move them earlier to inflate firing counts.

## Provider-free acceptance results

The repaired direct and module census now print:

```text
ALL_17_PRODUCERS_PROVEN
ALL_17_CONSUMERS_PROVEN
ALL_EFFECTS_TIMING_VALID
ALL_PAYLOADS_GROUNDED
ALL_17_CONSUMER_PATHS_PROVEN
ALL_17_TRIGGERS_PROVEN
ALL_17_PAYLOADS_CONCRETE
ALL_17_CONSUMERS_APPLIED
ALL_VISIBLE_PAYLOADS_IN_FIRST_ELIGIBLE_REQUEST
NO_ACTIONS_BLOCKED
ALL_EFFECTS_CONTEXT_ACCOUNTED
ALL_FEATURE_OPPORTUNITIES_ACCOUNTED
NO_ELIGIBLE_TRIGGER_MISSES
NO_FALSE_FEATURE_FIRES
NO_EMPTY_LOCALIZATION_EFFECTS
NO_UNVERIFIED_CALLERS
NO_DUPLICATE_FRAME_EVIDENCE
REPOSITORY_SUBSTRATE_PROVEN
```

The real local substrate proof returned `available`, a non-empty graph
revision and binary SHA-256, two definition nodes, one certified directed call
edge, and SQLite integrity `ok`.

Final local verification:

- full repository suite: 994 collected, 991 passed, 3 platform skips, exit 0;
- changed-boundary/accounting suite: 124 passed;
- Ruff on every changed Python file: passed;
- `compileall`: passed;
- `git diff --check`: passed;
- direct census: passed;
- module census: passed;
- repository substrate verifier: passed; and
- readiness audit: `READY`.

## Research basis

- SWE-agent’s primary ACI work shows that the host/tool interface materially
  changes agent behavior. This supports fixing the action and evidence
  boundary rather than adding prompt prose:
  https://papers.nips.cc/paper_files/paper/2024/file/5a7c947568c1b1328ccc5230172e1e7c-Paper-Conference.pdf
- Tree-sitter’s code-navigation contract explicitly separates entity role
  (definition/reference) from kind (class/function/call). This is why a grep
  line cannot certify either role:
  https://tree-sitter.github.io/tree-sitter/4-code-navigation.html
- Repoformer reports that unconditional retrieval can be unhelpful or harmful
  and motivates selective retrieval. This supports correct abstention and
  bounded delivery rather than maximizing trigger count:
  https://arxiv.org/abs/2403.10059
- OpenHands detects repeated action-observation and action-error cycles as
  stuck states. This supports exact repeated-failure recovery while rejecting
  generic recovery payloads when the event is absent:
  https://docs.openhands.dev/sdk/guides/agent-stuck-detector

## What is and is not proved

Proved provider-free:

- the paid workflow contains the runtime substrate and fails before provider
  spend if the fixture cannot be indexed;
- all 17 producer and consumer paths are reachable on their exact events;
- graph structural features cannot be fabricated from grep prose;
- each evaluated opportunity is emitted or classified;
- visible payloads are grounded, deduplicated, and first-eligible; and
- default/failure behavior remains fail-open and non-blocking.

Not yet proved:

- that a fresh paid smoke will naturally fire any particular count out of 17;
- that the new graph facts improve solve rate or resource efficiency;
- that stochastic outcome regressions are eliminated; or
- that the 89-task treatment is ready.

The next paid step, only after authorization and an exact pushed-commit gate,
is one matched ten-task smoke. Its receipts must report per task: fired IDs,
correct abstentions, absent triggers, substrate failures, eligible misses,
false fires, first-eligible delivery, duplicate evidence, calls/actions/tokens,
wall time, reward, and uncensored resolution. The 89-task run remains blocked.
