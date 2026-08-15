# Mini-SWE + GroundTruth Improvement Plan

Status: implementation in progress; core stable-anchor and attribution slices verified locally  
Program: mini-SWE-agent on SWE-bench Live Lite with GroundTruth (GT)  
Implementation repository: `hbali-stack/groundtruth`  
Isolated worktree: `D:\gt_runs\swe_live_lite_fix_20260730\groundtruth`  
Development branch: `gt-lifecycle-attribution-20260730`  
Last completed GT smoke: GitHub Actions run `30603432233`  
Model: `deepseek/deepseek-v4-flash`  
Confidence: high on the diagnosed runtime, timing, attribution, and context
defects; low on outcome improvement until a new matched live run is audited.

## 2026-07-31 implementation decision

This revision supersedes the earlier assumption that closing attribution alone
would make GT useful. Run `30603432233` proved the instrumentation is broad,
but GT did not improve the five-task outcome:

| Metric | Frozen GT-off | GT-on `30603432233` | Delta |
|---|---:|---:|---:|
| Reward | 1/5 | 1/5 | 0 |
| Agent steps | 190 | 209 | +10.0% |
| Input tokens | 2,529,002 | 6,851,592 | +170.9% |
| Output tokens | 33,212 | 94,498 | +184.5% |
| Cost | $0.026317 | $0.070372 | +167.4% |
| Measured loop steps | 6 | 0 | -100% |
| Wasted views | 12 | 23 | +91.7% |

The run delivered 90 provider-bound capsules containing 30,349 characters.
Eighty-six evidence occurrences were `obligations`. The repeated obligation
generations solved an internal coalition-completeness problem but created a
larger product problem: GT repeatedly retransmitted an immutable task contract
at `SOURCE_UNDERSTANDING` and `PATCH_CONSTRUCTION` even though mini-SWE's native
request still contained the original task.

The next implementation therefore has four non-negotiable goals:

1. stop immutable obligation rematerialization without starving other evidence;
2. make truth, authority, freshness, timing, and intended action measurable for
   every delivered FACT and CAP owner;
3. evaluate all 17 identities at their real mini-SWE lifecycle opportunities,
   with a named terminal outcome for every opportunity; and
4. require a real installed mini-SWE entry-point replay before any paid run.

This is not a request to make all 17 identities emit bytes. A feature works when
its real trigger is evaluated and ends in a truthful terminal state. For
mistake-gated features, correct quiet is success.

## Research basis and what transfers

The plan uses the following findings, but ports principles rather than code:

- Upstream mini-SWE is intentionally a minimal loop with a linear message
  history: each model response and shell observation is appended, and the
  resulting trajectory is the model history. GT must preserve that debuggable
  architecture rather than introduce nano's unrelated transcript machinery.
- SWE-agent's Agent-Computer Interface research shows that tool and observation
  shape materially changes repair performance. GT must therefore integrate at
  mini-SWE's real task, provider, action, result, and submit seams, not at a
  synthetic parallel lifecycle.
- Agentless demonstrates the value of a bounded
  localization -> repair -> validation workflow. GT should improve those
  decisions with deterministic evidence, not add open-ended reasoning.
- RepoGraph shows that repository graphs can help, but graph evidence must be a
  targeted repository slice. GT must deliver a decision-linked file/symbol/
  caller/test fact, never an inventory dump.
- OpenAI's Codex loop analysis shows why repeated context is especially
  expensive in a stateless agent loop: history growth can become quadratic,
  while a stable early prefix enables provider prompt caching. This supports
  one durable task anchor plus sparse later deltas.
- OpenAI's harness-engineering account argues for a compact map, progressive
  disclosure, and mechanically enforced invariants. GT should expose the
  smallest decision-linked graph/test receipt and enforce terminal completeness
  in code rather than expand the standing prompt.
- Anthropic's context-engineering guidance favors the smallest high-signal
  context and compaction that preserves decisions and unresolved state while
  dropping redundant tool output. In mini-SWE, GT can obtain that benefit
  without replacing the native linear trajectory: semantic state lives in GT,
  while only changed state is injected.
- Aider's repository-map implementation ranks graph context into a hard token
  budget and excludes files already present in chat. GT should likewise exclude
  already-acquired graph facts and fit ranked evidence to its existing capsule
  budget.
- LocAgent and ARISE strengthen the graph design: hierarchical entity/
  dependency navigation and targeted statement-level slices outperform flat
  inventory dumps. These findings justify bounded caller/definition/test slices,
  not a general graph transcript.
- SWT-Bench and VRpilot show that test generation/coverage and compiler or test
  feedback are useful only when tied to the repair and validation loop. GT must
  carry attributable failure and coverage receipts rather than generic “run
  tests” advice.
- Anthropic's agent-evaluation guidance separates outcome grading from
  trajectory analysis and calls for repeated trials because agent outcomes are
  stochastic. A single smoke can prove wiring and expose behavior, but cannot
  establish a stable causal advantage.
- The saved five-task run is stronger evidence than generic architecture
  advice: repeated obligations reduced neither reward nor total work, while
  caller/definition deliveries lacked complete truth and authority receipts.

Research sources:

- `https://github.com/SWE-agent/mini-swe-agent`
- `https://openai.com/index/unrolling-the-codex-agent-loop/`
- `https://openai.com/index/harness-engineering/`
- `https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents`
- `https://www.anthropic.com/engineering/writing-tools-for-agents`
- `https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents`
- `https://github.com/Aider-AI/aider/blob/main/aider/repomap.py`
- `https://arxiv.org/abs/2405.15793`
- `https://arxiv.org/abs/2407.01489`
- `https://arxiv.org/abs/2410.14684`
- `https://arxiv.org/abs/2503.09089`
- `https://arxiv.org/abs/2605.03117`
- `https://arxiv.org/abs/2406.12952`
- `https://arxiv.org/abs/2405.15690`

## 2026-07-31 implementation record

Implemented in the isolated mini-SWE worktree:

Local implementation commits (not pushed):

- `009b891e142f8e5a030cd6914ca60ca0be631623`
- `283321cfbda82c67714e645e4032dbd27fb9d3e7` (exact
  feature-fire/provider-delivery join hardening)

- added provider-acknowledged `established_roles` to the active-decision model;
- made an immutable issue-derived obligation a stable
  `BEHAVIORAL_CONTRACT` anchor only after its root evidence reaches
  `ACTIVE`/`SATISFIED`;
- stopped per-window rematerialization for that stable root while retaining a
  bounded recovery generation for an abandoned `RELEASED`/`DELIVERED` provider
  path;
- proved a later `caller_contract` delta can complete and deliver without
  retransmitting task prose;
- passed the exact task text from `install_canonical_runtime` into the real
  provider boundary and recorded its hash, character count, presence, and JSON
  path in the provider-final payload;
- added audit-only semantic receipts for every evidence member, including
  feature/candidate/fact identity, authorized CAP owners, subject, claim and
  intended action, provenance hashes, authority, grade, revisions,
  dependencies, substrates, lifecycle stage, and state-vector hash;
- bumped the evidence journal to backward-readable `gt.evidence_record.v2` so
  the actual runtime producer identity survives envelope conversion; v1 rows
  remain readable with an explicitly unknown producer, while current semantic
  proof fails closed rather than substituting a feature name;
- made the 17-feature reporter validate current semantic receipts before
  awarding FACT or CAP `FIRED` credit; a forged semantic claim with intact GT
  marker and delivery seal now earns zero credit;
- preserved explicit legacy-unmeasured reading for old saved artifacts rather
  than rewriting their historical result;
- normalized each valid lifecycle opportunity to exactly one of `DELIVERED`,
  `APPLIED_QUIET`, `INELIGIBLE`, `SUPPRESSED`, `FAULT`, or
  `DELIVERY_FAILURE`, with delivery requiring the exact feature-fire candidate
  to join the canonical evidence lineage; and
- exposed normalized terminal counts, semantic-receipt integrity, opportunity
  counts, and terminal counts in the JSON report.

RED-first proof was captured for both principal defects:

1. the stable-anchor test originally failed because `ActiveDecision` had no
   established-role model; and
2. the provider-boundary receipt test originally failed because the boundary
   accepted neither task-anchor nor explicit receipt-sink inputs.

The focused real-runtime/provider/lifecycle regression set is green:
`101 passed`. The source-entry, canonical replay, byte-identity-off,
post-edit-freshness, repository-delta, evidence-schema, and envelope-conversion
set is also green: `81 passed`. Static lint and `git diff --check` are green.

The monolithic repository command starts a separate 3,000-test engine process
and did not return a trustworthy parent summary inside the shell's execution
window. That is not counted as a pass. A bounded authoritative suite command or
CI audit remains blocking before `GO_LIVE`; no paid run has been dispatched by
this implementation step.

### Frozen-run deep re-audit with the new reader

The five exact task artifacts from GitHub Actions run `30603432233` were
downloaded read-only to:

`D:\gt_runs\30603432233_replay_20260731`

The current reporter reproduces the historical mechanical verdict:

- 6 `FIRED`;
- 4 `TRIGGER-ABSENT`;
- 7 `ARBITRATED`;
- 976/976 lifecycle opportunities terminal; and
- zero invalid, orphan, or unterminated lifecycle rows.

The stronger joins expose what the old aggregate concealed:

- all 90 canonical deliveries are `legacy-unmeasured` for semantic receipts,
  because those artifacts predate this implementation;
- 96 FACT/CAP feature instances are named by canonical delivery lineage;
- only 11 instances join the same feature, observation, and sealed
  candidate/evidence identity;
- 85 delivered instances are unjoined;
- 81 of the unjoined instances are repeated `obligations`;
- five obligation instances join correctly, exactly one task-start anchor per
  task;
- the four other unjoined instances are one `def_partition` instance plus the
  historical false-submit family (`submit_refusal`, `GT_CERT_DELIVERY`,
  `GT_SS_SUBMIT_RED`), which post-run commit `665ba353` addressed.

The normalized historical opportunity terminals are:

- 499 `INELIGIBLE`;
- 359 `APPLIED_QUIET`;
- 105 `SUPPRESSED`;
- 13 `DELIVERED`; and
- zero `FAULT` or `DELIVERY_FAILURE`.

This is descriptive replay/audit of old artifacts, not proof of new runtime
behavior. It is nevertheless the exact quantitative target for the stable
anchor change: preserve the five legitimate task-start obligation anchors and
remove the 81 unjoined obligation re-doses. The generated machine-readable
report is:

`D:\gt_runs\30603432233_replay_20260731\gt_feature_verdicts_current_reader.json`

## Explicit non-port decisions

The following nano mechanisms will not be copied into mini-SWE:

- nano's active-provider-view reconstruction and checkpoint format;
- Terminal-Bench task roles, commands, timeout policy, and workflow gates;
- nano-specific shell persistence and shell-death handling;
- nano task IDs, predicates, graph query adapters, or delivery thresholds;
- any rule that treats feature-fire count as an objective; and
- any test that proves behavior only by monkeypatching the function under test.

Mini-SWE already keeps the original task in its native linear request history.
That task is the durable behavioral-contract anchor. GT should send the parsed
task contract once at task start, then send only a compact unresolved-state
delta when the obligation state actually changes or a fresh positive submit
blocker exists. The provider receipt must prove the original task anchor is
still present; GT must not assume it.

## Canonical mini-SWE stage transaction

Every real boundary executes one transaction:

```text
canonical observation/proposal
  -> enumerate applicable 17-feature opportunities
  -> snapshot repository, graph, patch, test, and obligation revisions
  -> run feature-specific producer
  -> assign exactly one producer disposition
  -> rank admissible evidence through the one-dose arbiter
  -> seal at most one capsule
  -> bind exact bytes to the provider-final request
  -> record terminal provider response
  -> link the next action and next canonical result
  -> classify consequence and expire/supersede the dose
```

The normalized terminal states are:

- `DELIVERED`: authorized bytes reached the provider-final request and received
  a terminal provider response;
- `APPLIED_QUIET`: the feature executed and proved no intervention was needed;
- `INELIGIBLE`: its feature-specific trigger condition was absent;
- `SUPPRESSED`: evidence existed but freshness, relevance, dose, or a higher
  priority intervention withheld it;
- `FAULT`: a required dependency or proof chain failed; and
- `DELIVERY_FAILURE`: eligible sealed evidence failed before provider-terminal
  delivery.

Existing low-level dispositions such as `produced`, `available`, `abstained`,
`permitted`, `deferred`, `withheld`, and `blocked` remain useful detail, but the
auditor must deterministically map each to one terminal state. Missing
instrumentation is never mapped to quiet.

## Mini-SWE lifecycle and exact feature timing

| Stage | Mini-SWE boundary | Features evaluated | Correct timing |
|---|---|---|---|
| Orient/plan | `task_start`, before provider iteration 1 | `obligations`, `localization`, `GT_LOC_RESLOT` | Same provider request as the original task; no later than iteration 1 |
| Research | `search_result`, `failed_search` | `localization`, `def_partition`, `newfile_precedent`, `GT_LOC_RESLOT`, `GT_CHANGE_SURFACE` | Before the next search/view/edit choice |
| Understand | `file_view` | `caller_contract` | After the viewed bytes exist, before an edit to that subject or dependent |
| Pre-edit | `edit_proposed`, `file_create_proposed` | `obligations`, `localization`, `def_partition`, `syntax_result`, `signature_delta`, and bound CAP owners | Before mini-SWE executes the proposed mutation |
| Post-edit | `edit_result` | `caller_contract`, `def_partition`, `syntax_result`, `signature_delta`, `covering_red`, and bound CAP owners | After exact before/after state is known, before the next provider decision |
| Test/recovery | `test_result`, `failure_obs` | `covering_red`, `recovery`, `GT_HYPOTHESIS` | In the first request after the fresh result; never from stale or environmental failure |
| Verify | `verification_horizon` | obligation state, `syntax_result`, `covering_red`, recovery eligibility | Before submission is allowed |
| Submit | `submit_proposed` | `submit_refusal`, `GT_SS_SUBMIT_RED`, `GT_CERT_DELIVERY` | Before physical submit; only a fresh attributable positive blocker may interrupt |

The lifecycle table is generated from the canonical FACT registry and CAP
bindings. A second hand-maintained runtime feature list is prohibited. Import
time must fail if the generated universe is not exactly 10 FACT identities plus
7 authorized CAP byte owners.

## Semantic context contract for mini-SWE

GT owns semantic state; mini-SWE owns transcript transport.

### Stable task contract

- Deliver the complete parsed task contract once at `task_start`.
- Record its issue hash, obligation digest, provider request, and terminal
  response.
- On every later provider delivery, prove that the exact original task remains
  in the provider-final payload.
- Treat the provider-proven task anchor as satisfying the standing
  `BEHAVIORAL_CONTRACT` role without retransmitting the full obligation block.
- Never mint a new obligation evidence generation merely because the decision
  window changed.

### Obligation delta

An obligation may resurface only when at least one of these changes:

- unresolved/verified/stale/RED state vector;
- repository revision affecting the obligation;
- a new attributable test or verification receipt;
- a fresh positive submit blocker; or
- an explicit bounded recovery escalation.

The delta contains obligation IDs and the smallest exact next check. It does
not repeat issue prose. Each task receives:

- one task-start full-contract dose;
- at most one unchanged-state reminder, only at the verification horizon; and
- any number of genuinely new state deltas, each with a different state-vector
  hash.

An unchanged state-vector delivery is a gate failure.

### Decision-linked graph evidence

Every graph-derived delivery must name:

- current graph and repository revision;
- graph surface and proof rows;
- active task obligation, viewed/edited subject, or fresh failure it answers;
- intended next decision;
- confidence and authority;
- exact file/symbol/test consequence; and
- expiry boundary.

`caller_contract` must carry a directional caller edge or an independently
verified lexical/AST fallback. `def_partition` must carry exact definition
locations and the search identity that selected them. A provider delivery
without these receipts is mechanically delivered but semantically unproven.

## Machine-verifiable proof levels

Each feature/task row has four independent proof columns:

1. **Opportunity:** the real feature-specific trigger/window was evaluated.
2. **Computation:** the producer ended in a named result with input revision,
   substrate, authority, and output/correct-quiet reason.
3. **Delivery/timing:** if selected, exact sealed bytes reached the final
   provider request inside the declared lifecycle window and received a
   terminal response.
4. **Consequence:** the linked next action and next state transition were
   measured; benefit, harm, no effect, and unknown remain distinct.

The canonical provider-delivery row must include one semantic receipt per
evidence member:

```text
feature_id
candidate_id
producer_id
fact_class
authorized_cap_owners
subject
claim_hash
provenance_hash
observed_substrates
authority
grade
repository_revision
graph_revision
revision_dependencies
fresh
lifecycle_stage
intended_action
state_vector_hash
```

These receipts are audit data, not model-facing prose. Their addition must not
change capsule bytes.

## One-dose arbitration

The one-dose priority is:

1. fresh attributable correctness RED or submit blocker;
2. exact post-edit syntax/signature/caller consequence;
3. recovery from repeated unchanged source failure;
4. pre-edit caller/precedent evidence;
5. research localization/definition evidence;
6. unchanged obligation reminder; and
7. quiet.

Admission is deterministic and receipted:

```text
utility =
    evidence_strength
  + unresolved_relevance
  + actionability
  + expected_information_gain
  + timing_value
  - repetition_cost
  - token_cost
  - interruption_cost
  - false_positive_risk
```

Shadow-score this formula on the saved trajectories before it changes delivery
behavior. The plan does not invent weights from intuition: replay must show the
ranking keeps known useful caller/definition facts, removes immutable
obligation repetitions, and suppresses the historical false submit refusal.

## Executable completion and submit authority

Completion state is derived only from fresh attributable receipts:

- syntax/build/import receipt for affected languages and paths;
- targeted test receipt linked to the changed surface;
- covering RED/GREEN receipt where graph or explicit test selection supports
  the link;
- exact patch revision after the latest edit; and
- unresolved task obligation state.

A generic passing command cannot certify an unrelated obligation. A failing
command cannot block submit when its failure is unattributed to the submitted
patch. An unchanged refusal may be delivered once; a second identical refusal
without new evidence is a gate failure.

## No-monkeypatch proof policy

Monkeypatch-based tests may remain for isolated pure helper compatibility, but
they do not count toward implementation acceptance. No acceptance claim may
depend on replacing a producer, provider boundary, classifier, ledger writer,
or submit gate with a fake return value.

Required proof layers:

1. pure deterministic contract tests using real values and constructors;
2. real `AttemptReasoningRuntime` plus SQLite journal;
3. real `MiniSweProviderBoundary` with a local recording transport and literal
   final payload construction;
4. installed `gt_mini_patch` integration against the pinned mini-SWE package;
5. immutable replay of all five saved trajectories and ledgers;
6. container/image smoke proving the installed source and commit identity; and
7. one real DeepSeek GT-on smoke only after every local gate passes.

Tests must assert externally visible state: ledger rows, SQLite history,
provider payload structure, action execution ordering, repository diff, and
report output. Asserting that a mocked function was called is not proof that GT
works in mini-SWE.

## Dependency-ordered implementation

### Gate 0: Freeze and reproduce

- Freeze run `30603432233`, commit `dd195143`, post-run fix `665ba353`, and the
  frozen GT-off artifacts.
- Reproduce the 90 deliveries, 86 obligation occurrences, five-task reward,
  steps, tokens, cost, loop, test, and wasted-view totals.
- Record configuration incompatibilities between the frozen arms; do not hide
  them in aggregate deltas.

Exit: replay accounts for every provider delivery and at least 99% of provider
calls/tokens.

### Gate 1: RED tests for the proven product defects

Add real-runtime tests that fail before implementation for:

- a provider-proven immutable task contract being retransmitted in a new
  `SOURCE_UNDERSTANDING` or `PATCH_CONSTRUCTION` window;
- a new non-obligation fact being starved when the task contract is already
  provider-proven;
- a canonical caller/definition delivery lacking semantic truth/authority
  receipts;
- an unchanged obligation state-vector receiving a second dose;
- stage-order inversions at pre-edit, post-edit, and submit; and
- a false/unattributed verification failure becoming a submit refusal.

Exit: each test is demonstrated RED for the intended reason.

### Gate 2: Stable contract anchor and bounded deltas

- Replace decision-window obligation rematerialization with a provider-proven
  stable contract anchor.
- Let the proven anchor satisfy the standing behavioral-contract role without
  joining repeated bytes to a new capsule.
- Add state-vector hashing and obligation-delta admission.
- Add unchanged-state suppression and a per-task dose receipt.

Exit: the saved five-task replay predicts five full task-contract deliveries,
zero immutable repeats, and only genuinely changed deltas.

### Gate 3: Semantic receipts and stage transaction

- Emit semantic receipts without changing provider bytes.
- Normalize every feature opportunity to exactly one terminal state.
- Enforce opportunity -> computation -> arbitration -> provider ordering.
- Validate graph/repository freshness at both selection and provider binding.

Exit: `caller_contract` and `def_partition` are no longer
truth/authority-unmeasured; stale facts cannot be delivered.

### Gate 4: Trigger completeness across mini-SWE tool shapes

- Exercise structured and shell search/view/edit/create/test/submit shapes.
- Preserve safe dynamic-root and staged-copy normalization.
- Record explicit unclassified actions.
- Require proposal interception before mutation and result observation after
  exact repository state exists.

Exit: all real source actions in the five saved trajectories are classified or
carry a named correct-quiet reason.

### Gate 5: Verification and recovery

- Couple fresh patch/test state to executable completion.
- Keep recovery in shadow mode until saved replay has zero false steers.
- Permit one bounded recovery only after repeated equivalent source failure.
- Require attributable positive evidence for submit refusal.

Exit: historical false submit refusal remains impossible; improvement suppresses
recovery; fresh required RED invalidates readiness immediately.

### Gate 6: Local acceptance

Run, without paid provider calls:

- focused RED/GREEN tests;
- complete runtime suite;
- artifact-deepswe suite;
- SWE-bench reporters and metric integrity suites;
- immutable five-trajectory replay;
- Python compilation and configured lint/static checks;
- `git diff --check`;
- workflow/task/model/substrate parity audit; and
- secret/project hygiene checks.

The audit emits exactly `GO_LIVE`, `NO_GO_CODE`, or `NO_GO_EXPERIMENT`.

### Gate 7: Live proof

Only after `GO_LIVE` and explicit dispatch authorization:

- build the substrate from the exact tested commit;
- verify digest/commit parity;
- run the frozen five tasks with `deepseek/deepseek-v4-flash`;
- keep timeout, concurrency, step limit, prompt, template, thinking mode, and
  grader inputs explicit; and
- monitor to terminal before grading.

No GCP operation is part of this plan. The existing GT-off run remains frozen
and is not rerun.

## Live acceptance report

The final report must include, per task:

- official reward and patch status;
- provider calls, steps, input/output/cache tokens, cost, and wall time;
- time to first gold view/edit/test and verify-to-submit gap;
- tests, loops, repeated commands, wasted views, and harmful tool outcomes;
- all 17 feature opportunities and terminal states;
- semantic truth/authority receipts for every delivery;
- exact lifecycle timing and provider-final request join;
- next action and next state transition after every GT delivery;
- full-contract versus delta obligation bytes;
- false intervention and unchanged-state repetition counts; and
- comparison with the frozen GT-off row.

Promotion requires:

- no task-level correctness regression hidden by an aggregate;
- no false submit refusal or recovery steer;
- zero missing opportunity terminals;
- zero stale/unattributed deliveries;
- exactly one full obligation contract per task;
- materially fewer predicted and observed GT-induced bytes/work;
- reward higher than the frozen baseline, or equal reward with materially lower
  compatible cost; and
- explicit separation of descriptive comparison from causal inference where
  frozen configuration differs.

## Scope

This document is for **mini-SWE + GT**, not nano-harness and not
Terminal-Bench.

The nano plan in `gt_improve.md` is a source of general engineering lessons:

- measure opportunities and terminal outcomes, not raw fire counts;
- bind every delivery to the exact provider-final request;
- keep evidence fresh across repository and graph revisions;
- make abstention visible;
- bound context and intervention frequency;
- separate wiring, attribution, behavior, efficiency, and outcome claims; and
- use matched experiments before claiming causality.

Nano-specific code, role packs, task IDs, workflows, shell behavior, and
Terminal-Bench acceptance rules are not copied into mini-SWE. Mini-SWE has a
different action model, provider seam, task substrate, trajectory schema,
grader, and failure surface.

## Decision

GT is the deterministic evidence and control engine attached to mini-SWE. It
does not replace the coding model's ideation or code generation. Its job is to
improve the model's next decision throughout the software-development
lifecycle:

1. task start and planning;
2. repository search and localization;
3. source understanding;
4. pre-edit commitment;
5. post-edit propagation and structural validation;
6. test selection and failure recovery; and
7. pre-submit verification and refusal.

The goal is not to force all 17 identities to emit bytes on every task.
Mistake-gated features must stay quiet when their trigger condition is absent.
The goal is:

> Every real lifecycle opportunity has a feature-specific terminal outcome,
> every delivered byte is provider-final attributable and correctly timed, and
> matched runs show higher reward or non-worse reward at materially lower cost.

## Mini-SWE architecture of record

```text
SWE-bench issue
    |
    v
task-start brief and typed evidence
    |
    v
mini-SWE model response
    |
    v
commitment boundary --------> proposal lifecycle census
    |
    v
mini-SWE action execution
    |
    v
canonical result observer --> result lifecycle census
    |                              |
    v                              v
Gateway and reactive producers -> per-feature disposition
    |
    v
AttemptReasoningRuntime
    |
    v
one staged canonical capsule
    |
    v
MiniSweProviderBoundary
    |
    v
exact provider-final request -> terminal provider response
    |
    v
canonical delivery ledger, evidence lineage, CAP owners, and action linkage
```

Primary implementation surfaces:

- `artifact_deepswe/gt_mini_patch.py`: installed mini-SWE action/result seam,
  commitment integration, canonical attachment, and host telemetry;
- `src/groundtruth/runtime/reasoning_runtime.py`: evidence contracts,
  lifecycle, reasoning state, capsule compilation, and delivery state;
- `src/groundtruth/runtime/miniswe_provider_boundary.py`: exact provider
  payload binding and terminal canonical delivery;
- `src/groundtruth/runtime/fact_registry.py`: FACT registrations and lifecycle
  windows;
- `src/groundtruth/runtime/trigger_opportunity.py`: physical and lifecycle
  opportunity identities;
- `artifact_deepswe/gt_integration/gt_ae_block.sh`: variables crossing pier's
  agent-environment boundary;
- `scripts/swebench/gt_feature_verdicts.py`: 17-feature audit;
- `scripts/swebench/gt_feature_metrics.py` and
  `scripts/swebench/gt_run_metrics.py`: publishability and run aggregation; and
- `.github/workflows/deepswe_full.yml`: paid mini-SWE workflow.

## Evidence from the first five-task smoke

Run `30581286663` used:

- commit `dba6ccb54fe094f8c27ff00f773e91574fbab830`;
- `deepseek/deepseek-v4-flash`;
- timeout multiplier `1.0`;
- a pinned GT substrate;
- the frozen five-task SWE Live Lite slice:
  - `aiogram__aiogram-1594`;
  - `amoffat__sh-744`;
  - `arviz-devs__arviz-2413`;
  - `aws-cloudformation__cfn-lint-3749`;
  - `aws-cloudformation__cfn-lint-3764`.

Observed task result:

- GT resolved `amoffat__sh-744`;
- the frozen offline baseline resolved none of the same five tasks;
- no task regressed from resolved baseline to unresolved GT;
- all provider-bound task jobs and evaluators completed; and
- after correcting a post-run classifier defect, all 58 mandatory performance
  metrics and the exact 129-metric inventory were complete and publishable.

This is a positive observation, not a general causal claim. The frozen baseline
must not be rerun merely to make a new comparison look cleaner.

## Defects proven by the saved live artifacts

### 1. Result lifecycle opportunities were unterminated

The result funnel wrote terminal dispositions before emitting result
opportunities and reconstructed proposal IDs instead of result IDs.

Measured in the saved ledgers:

- 440 lifecycle opportunity IDs;
- 166 terminal IDs;
- 274 unterminated IDs;
- proposal boundaries terminated;
- result boundaries did not.

Required correction:

- emit result opportunities before the funnel;
- use the canonical result observation ID;
- terminate proposal opportunities at commitment control;
- terminate result opportunities at the producer funnel; and
- validate exactly one terminal disposition per feature-fire ID.

### 2. One row-level `produced` flag falsely credited multiple features

A result observation can evaluate several DIRECT features while producing one
FACT. A single broad `disposition="produced"` cannot say which feature worked.

Required correction:

- write `feature_dispositions[]`;
- bind each entry to `feature_fire_id`, `feature_id`, `fact_class`, lifecycle
  boundary, disposition, and candidate IDs;
- distinguish `produced`, `available`, `abstained`, `permitted`, `deferred`,
  `withheld`, `blocked`, and `delivered`; and
- reject missing, duplicate, orphaned, or conflicting terminal entries.

### 3. Fail-closed embedder identity did not cross into the task agent

The host pretask index used forced ONNX proof settings. Pier drops ambient host
environment variables, so the runtime localizer inside mini-SWE used a
different identity and correctly rejected its result:

```text
same_embedder_identity FAILED
```

Required correction:

- forward the model root and every fail-closed proof requirement through
  `GT_AE_ARGS`;
- test the canonical AE block, not only workflow host exports; and
- fail preflight if pretask and runtime proof identities differ.

### 4. Dynamic repository-root prefixes hid real views and edits

The live harness uses commands shaped like:

```text
cd $(cat /tmp/gt_root.txt) && <real command>
```

Search parsing stripped this prefix, but primary view/edit classification did
not. This starved canonical subjects, caller-contract evidence,
edit-before/after reconstruction, patch-delta production, and result lifecycle
events.

Required correction:

- reuse the existing strict, flag-gated `cd` prefix parser in `_classify`;
- preserve correct-or-quiet rejection for unsafe separators or nested dynamic
  prefixes;
- cover live `cat` and Python write shapes; and
- keep the widening enabled only in the intended GT profile.

### 5. Step-0 timing used two unrelated observation identities

The lifecycle census used a task-text hash while compilation and provider
delivery used `<attempt_id>:task`. Both chains were individually valid but
could not prove that task-start evidence was delivered inside the task-start
window.

Required correction:

- use the exact compilation observation ID for task-start census, compilation,
  provider delivery, and audit joining.

### 6. Canonical delivery timing was judged with the wrong vocabulary

Canonical provider rows use `canonical_provider_delivery`; lifecycle contracts
use events such as `file_view`, `edit_result`, and `submit_proposed`. String
comparison manufactures false timing failures.

Required correction:

- join delivery to lifecycle opportunity by canonical observation identity and
  feature identity;
- treat a matching registered lifecycle row as timing authority;
- do not infer timing from free-form ledger `event_type`; and
- keep physical `submit` interception distinct from the
  `submit_proposed` decision window.

### 7. Offline edit metrics split one physical edit into two

The saved cfn-lint trajectory used a dynamic-root Python write. One metrics
reader found the submitted patch while another missed the command. After
classification was widened, runtime and command paths such as `./pkg/mod.py`
and `pkg/mod.py` could still become duplicate edit attempts.

Required correction:

- reuse the shared shell classifier;
- preserve relative paths when a dynamic root cannot be resolved offline;
- reconcile suffix-equivalent runtime and command paths as one edit; and
- retain the authoritative ledger spelling without duplicating attempts.

## The 17 DIRECT identities and lifecycle coverage

Ten FACT identities:

| FACT | Earliest shaping | Deliver-by | Corrective/assurance |
|---|---|---|---|
| `obligations` | `task_start` | `edit_proposed` | `submit_proposed` |
| `localization` | `task_start` | `edit_proposed` | `edit_proposed` |
| `def_partition` | `search_result` | `edit_proposed` | `edit_result` |
| `caller_contract` | `search_result` | `file_view` | `edit_result` |
| `syntax_result` | `edit_proposed` | `edit_result` | `submit_proposed` |
| `signature_delta` | `edit_proposed` | `edit_result` | `submit_proposed` |
| `covering_red` | `edit_result` | `test_result` | `submit_proposed` |
| `submit_refusal` | `submit_proposed` | `submit_proposed` | `submit_proposed` |
| `newfile_precedent` | `failed_search` | `file_create_proposed` | `edit_result` |
| `recovery` | `failure_obs` | `test_result` | `submit_proposed` |

Seven authorized CAP byte owners:

| CAP | Bound FACT |
|---|---|
| `GT_CHANGE_SURFACE` | `newfile_precedent` |
| `GT_PATCH_DELTA` | `signature_delta` |
| `GT_LOC_RESLOT` | `localization` |
| `GT_SS_SUBMIT_RED` | `submit_refusal` |
| `GT_EDIT_CHECK` | `syntax_result` |
| `GT_HYPOTHESIS` | `recovery` |
| `GT_CERT_DELIVERY` | `submit_refusal` |

For every feature and task the audit must report one of:

- `FIRED`: authorized bytes reached the provider-final request;
- `TRIGGER-ABSENT`: the lifecycle window was evaluated and the
  feature-specific producer correctly abstained;
- `ARBITRATED`: evidence existed but commitment or dose control withheld it;
- `DELIVERY-FAILURE`: evidence existed but no valid provider delivery followed;
- `DEPENDENCY-FAILURE`: a required runtime substrate failed; or
- `NO-INSTRUMENTATION`: the proof chain is missing or invalid.

`NO-INSTRUMENTATION` is never interpreted as correct quiet.

## Improvement workstreams

### Workstream 1: Close lifecycle and attribution accounting

Deliver:

- per-feature dispositions;
- proposal and result terminal integrity;
- task-start identity alignment;
- canonical observation timing joins;
- FACT and CAP-specific delivery attribution; and
- orphan, duplicate, and conflict rejection.

Exit:

- every opportunity has exactly one terminal outcome;
- no delivery is credited from a layer name or substring alone;
- CAP bytes are credited only from authorized owner lineage; and
- all timing claims derive from registered lifecycle identities.

### Workstream 2: Make mini-SWE action sensing complete

Deliver:

- structured-editor-first classification;
- safe shell-prefix normalization;
- canonical file subjects for views and edits;
- edit-before/after bridge completeness;
- semantic events for search, view, edit, test, failure, and submit; and
- explicit unclassified-action telemetry.

Exit:

- replay classifies every source view/edit in the five saved trajectories or
  names why it cannot;
- dynamic-root commands no longer starve producers; and
- unsafe compound commands remain correct-quiet.

### Workstream 3: Make runtime substrate identity fail closed

Deliver:

- AE forwarding for full-stack, FTS5, embedder, LSP, graph-build, and model-root
  proof settings;
- pretask/runtime embedder identity receipts;
- graph revision and repository revision checks; and
- pre-spend workflow abort on mismatch.

Exit:

- ranked localization cannot be rejected because the host and task agent
  silently selected different proof modes;
- a mismatch aborts before provider spend; and
- every graph-derived delivery names the current revision.

### Workstream 4: Make correct quiet measurable

Deliver:

- feature-specific abstention reasons;
- dependency-failure reasons;
- producer invocation and result joins;
- physical-trigger and lifecycle-window separation; and
- reporter verdicts that do not inherit a FACT delivery as proof of a CAP.

Exit:

- every eligible feature is `FIRED`, `TRIGGER-ABSENT`, `ARBITRATED`, or a named
  failure;
- no feature is called working because another feature produced bytes in the
  same observation; and
- no quiet mistake-gated feature is mislabeled broken.

### Workstream 5: Improve SDLC timing and trigger width

The current lifecycle covers the right stages, but an event is useful only if
mini-SWE's actual tool shapes reach it.

Deliver:

- task-start obligations and bounded localization;
- search-result definition partitioning and localization re-slotting;
- pre-edit evidence evaluation before commitment;
- post-view caller contracts;
- post-edit syntax and signature consequences;
- test-result covering evidence and recovery;
- verification-horizon participation; and
- submit-proposed certification/refusal.

Exit:

- planning, search, write, verify, and submit stages all appear in replay when
  the model reaches them;
- delivery occurs before the decision it can change;
- repeated standing obligations are generation-attributed and bounded; and
- clean edits/tests remain quiet.

### Workstream 6: Bound context persistence and intervention utility

The nano plan correctly identifies repeated provider exposure as a possible
efficiency defect, but mini-SWE must be measured independently.

Deliver:

- per-capsule unique bytes and exposure count;
- evidence generation, supersession, and expiry receipts;
- provider-request structural block accounting;
- repetition and token cost in arbitration telemetry; and
- shadow utility scoring before changing delivery behavior.

Exit:

- repeated GT input bytes are measured per task and capsule;
- stale evidence cannot survive repository/graph revision invalidation;
- one-dose policy is proven at the provider boundary; and
- any future utility threshold is calibrated from replay, not invented.

### Workstream 7: Strengthen progress and recovery without false steering

Deliver:

- deterministic progress fingerprints from patch, failure, test, evidence, and
  decision state;
- shadow stall and contradiction candidates;
- separation of environment/provider failures from source failures;
- one bounded escalation only after an attributable ineffective recovery; and
- provider response and next-action linkage.

Exit:

- false recovery steers remain zero on the saved five-task slice;
- repeated unchanged failures after relevant edits are detectable;
- improvement suppresses recovery; and
- recovery effectiveness is measurable from the next action and state change.

### Workstream 8: Make completion evidence executable

Mini-SWE tasks are code-repair tasks, so completion proof must be tied to the
affected repository surface.

Deliver:

- exact issue obligations with revision-aware states;
- targeted test/build/import/syntax receipts;
- invalidation after relevant edits;
- covering-test attribution where graph truth supports it; and
- minimal unresolved submit evidence.

Exit:

- a generic clean command cannot verify an unrelated obligation;
- stale passes do not certify a later patch;
- submit refusal uses observed unresolved RED or missing required proof; and
- verifier-only hidden information never enters the model context.

### Workstream 9: Establish matched improvement evidence

Use the existing frozen offline baseline; do not rerun it.

For the next candidate smoke, freeze:

- the exact five tasks and order;
- `deepseek/deepseek-v4-flash`;
- explicit model settings;
- timeout multiplier `1.0`;
- iteration limit and concurrency;
- mini-SWE/provider adapter versions;
- GT commit and substrate digest;
- workflow and grader versions; and
- secrets/gateway configuration class.

Primary outcomes:

- reward per task;
- resolved-task vector;
- input tokens per resolved task;
- iterations per resolved task; and
- wall time per resolved task.

Secondary outcomes:

- provider calls and tool errors;
- views, edits, tests, and submit attempts;
- feature opportunities and terminal states;
- delivered and repeated GT bytes;
- action consistency after delivery;
- false interventions; and
- regressions versus the frozen baseline.

One run can prove wiring and provide a candidate outcome observation. It cannot
prove general superiority. A broader causal claim requires matched repeated
runs or a sufficiently large locked task set.

## Dependency-ordered execution plan

### Phase 0: Preserve evidence

- Keep run `30581286663` and the regraded artifacts immutable.
- Keep the frozen baseline immutable.
- Record the exact current code, substrate, workflow, task slice, and model.

Exit: both arms remain auditable without rerunning either.

### Phase 1: Close the defects already proven live

- fix dynamic-root view/edit classification;
- forward fail-closed proof environment;
- terminate proposal and result opportunities correctly;
- add per-feature dispositions;
- align task-start identity;
- join canonical delivery timing by observation identity; and
- reconcile dynamic-root edit metrics.

Exit: focused unit/integration tests and saved-artifact regrade are green.

### Phase 2: Replay the five saved trajectories

Run the new reporter and require:

- complete opportunity/terminal integrity;
- no orphan or conflicting dispositions;
- honest FACT/CAP attribution;
- correct timing joins;
- correct-quiet classification;
- provider-final delivery and terminal-response proof; and
- complete mandatory metrics.

Exit: no remaining `NO-INSTRUMENTATION` for an eligible path unless a named
legacy-artifact limitation makes the old run incapable of carrying the new
schema. Such a limitation requires a new smoke; it is not papered over.

### Phase 3: Full local and workflow audit

Run:

- syntax/compile checks;
- focused lifecycle, AE, classifier, provider, and metrics tests;
- artifact-deepswe suite;
- runtime suite;
- SWE-bench metrics/audit suite;
- remaining repository tests in bounded groups;
- static/format checks configured by the repository;
- workflow dispatch invariants;
- substrate closure and digest checks; and
- secret/project hygiene checks.

Audit result is exactly one of:

- `GO_LIVE`;
- `NO_GO_CODE`; or
- `NO_GO_EXPERIMENT`.

### Phase 4: Build and dispatch

Only after `GO_LIVE`:

1. commit the mini-SWE changes in the isolated branch;
2. push with the `hbali-stack` GitHub account explicitly selected;
3. build the exact GT substrate from that commit;
4. verify the substrate digest and workflow ref;
5. audit the dispatch inputs one final time; and
6. dispatch one DeepSeek v4 Flash GT smoke on the same five tasks.

No GCP operation is part of this plan.

### Phase 5: Monitor and prove

Monitor the workflow until terminal. Then produce:

- task status and reward table;
- frozen-baseline comparison;
- complete 17-feature table;
- provider-final attribution table;
- lifecycle opportunity/disposition integrity;
- action-consistency analysis;
- token, iteration, and wall-time metrics;
- regression tripwires;
- exact code and substrate identities; and
- a claim classification: wiring proven, behavior observed, improvement
  promising/inconclusive, or regression.

## Stop/go criteria

Do not dispatch if:

- any lifecycle opportunity lacks a terminal disposition;
- one feature can be credited by another feature's candidate;
- host and task runtime proof identities differ;
- dynamic-root source actions remain unclassified;
- canonical timing relies on free-form event strings;
- provider-final payload or terminal-response proof is missing;
- CAP ownership is inferred rather than carried by lineage;
- mandatory metrics are incomplete or contradictory;
- workflow/model/task/substrate parity is ambiguous;
- the full local suite has an unexplained failure; or
- the commit under test is not the commit in the substrate.

Dispatch one candidate smoke when all blocking gates are green.

Claim improvement only from the observed task vector and cost metrics. If
reward is flat and cost rises materially, GT is worse. If one run improves,
report it as a positive candidate result, not universal superiority.

## Definition of done

1. All 17 identities have per-task opportunity and terminal accounting.
2. Correct quiet is distinct from missing instrumentation.
3. Every delivered FACT has exact canonical evidence lineage.
4. Every delivered CAP has authorized byte-owner lineage.
5. Every canonical delivery is bound to the provider-final request and terminal
   response.
6. Every timing claim joins through canonical observation identity.
7. Host and task runtime substrate identities match.
8. Mini-SWE's real source views and edits reach the canonical observer.
9. Evidence freshness follows repository and graph revisions.
10. Model-facing doses and repeated exposure are measured and bounded.
11. Recovery is attributable and does not create false steers.
12. The saved baseline remains frozen and comparable.
13. A real DeepSeek v4 Flash mini-SWE smoke passes strict audit.
14. The final report separates wiring proof, behavioral evidence, efficiency,
    and outcome improvement.
