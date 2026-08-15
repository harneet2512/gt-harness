# mini-SWE GT live proof and frozen GT-off comparison

Run: `30603432233`  
Model: `deepseek/deepseek-v4-flash`  
GT run commit: `dd1951438e0fee23164d9b709bb5a507f4d5aff5`  
Substrate digest: `sha256:0ad3cf8f7a8ccdf98ad8aa034fd47f9b233853a85615cbf233a15f5d4c475091`  
Frozen GT-off comparator: run `28627357720` (not rerun)  
Post-run fix commit: `665ba353fba04636dbfff07c2d8ab1ec433b9b07`

## Executive verdict

This smoke does **not** prove that GT improves mini-SWE.

- Official reward was identical: GT-off `1/5`, GT-on `1/5`.
- Every task had the same binary reward in both arms.
- GT-on consumed 10.0% more steps, 170.9% more input tokens, 184.5% more
  output tokens, and 167.4% more cost.
- GT-on eliminated measured loop steps (`6 -> 0`) and ran more tests
  (`19 -> 27`), but wasted views increased (`12 -> 23`).
- Six of the 17 direct features mechanically fired. Three of those six were
  one false pre-submit intervention expressed through three linked feature
  rows. Only `obligations` has complete truth + authority + delivery proof.
- The live trajectory exposed two additional defects. Both were fixed after
  the run and pushed in `665ba353`, but this run cannot prove those fixes.

Confidence in the descriptive measurements: **high**.  
Confidence in a causal GT effect: **low** because this is `n=5`, rewards were
identical, and the frozen GT-off run used a different thinking/template
configuration.

## Context-engineering ownership

mini-SWE owns the mechanical agent scaffold:

- system/task messages;
- shell action/observation loop;
- transcript transport and output truncation;
- model, step, and cost limits;
- submission formatting.

GT must own semantic context engineering:

- convert the task into durable obligations;
- deliver graph-derived caller/definition/localization facts;
- recognize lifecycle boundaries (task start, view, search, edit, test, submit);
- invalidate stale evidence after repository changes;
- arbitrate, deduplicate, and budget context;
- bind delivered bytes to the provider request and scored trajectory.

The smoke shows that mini-SWE does not solve this automatically. GT delivered
90 provider-bound capsules containing 30,349 characters. `obligations`
accounted for 86 evidence occurrences and was repeatedly resurfaced during
PATCH_CONSTRUCTION and SOURCE_UNDERSTANDING. This is semantic repetition,
not a transport problem. A useful GT context engine needs lifecycle-level
deduplication and resurfacing based on unmet/fresh obligations, not merely a
one-dose-per-observation rule.

## Official correctness

| Task | GT-off | GT-on | Delta |
|---|---:|---:|---:|
| `aiogram__aiogram-1594` | 0 | 0 | 0 |
| `amoffat__sh-744` | 1 | 1 | 0 |
| `arviz-devs__arviz-2413` | 0 | 0 | 0 |
| `aws-cloudformation__cfn-lint-3749` | 0 | 0 | 0 |
| `aws-cloudformation__cfn-lint-3764` | 0 | 0 | 0 |
| **Total** | **1/5** | **1/5** | **0** |

There are no discordant task outcomes, so this five-task sample contains no
observed correctness advantage or regression.

## Deep efficiency comparison

The GT-off model outputs were not rerun. Their frozen trajectories were
regraded offline with the same current extractor used for GT-on, eliminating
extractor-definition drift.

| Task | Steps off -> on | Input tokens off -> on | Output tokens off -> on | Cost off -> on |
|---|---:|---:|---:|---:|
| `aiogram__aiogram-1594` | 18 -> 17 (-5.6%) | 102,221 -> 108,148 (+5.8%) | 2,025 -> 3,811 (+88.2%) | $0.002005 -> $0.002492 (+24.3%) |
| `amoffat__sh-744` | 58 -> 34 (-41.4%) | 718,574 -> 437,544 (-39.1%) | 9,256 -> 7,652 (-17.3%) | $0.006726 -> $0.005656 (-15.9%) |
| `arviz-devs__arviz-2413` | 31 -> 44 (+41.9%) | 333,236 -> 1,512,234 (+353.8%) | 3,927 -> 34,051 (+767.1%) | $0.003866 -> $0.022239 (+475.2%) |
| `aws-cloudformation__cfn-lint-3749` | 52 -> 93 (+78.8%) | 1,027,952 -> 4,526,692 (+340.4%) | 12,406 -> 42,325 (+241.2%) | $0.009493 -> $0.035428 (+273.2%) |
| `aws-cloudformation__cfn-lint-3764` | 31 -> 21 (-32.3%) | 347,019 -> 266,974 (-23.1%) | 5,598 -> 6,659 (+19.0%) | $0.004227 -> $0.004557 (+7.8%) |
| **Total** | **190 -> 209 (+10.0%)** | **2,529,002 -> 6,851,592 (+170.9%)** | **33,212 -> 94,498 (+184.5%)** | **$0.026317 -> $0.070372 (+167.4%)** |

Cost per resolved task therefore changed from `$0.026317` to `$0.070372`
(+167.4%), because both arms resolved exactly one task.

The aggregate regression is concentrated in `arviz-2413` and `cfn-lint-3749`.
`amoffat-744` is a genuine descriptive efficiency win, but it was already
resolved in GT-off and therefore adds no correctness gain.

## Trajectory/process deltas

| Task | Steps to gold view off -> on | Steps to gold edit off -> on | Tests off -> on | Verify gap off -> on | Loop steps off -> on | Wasted views off -> on |
|---|---:|---:|---:|---:|---:|---:|
| `aiogram__aiogram-1594` | 3 -> 2 | 7 -> 6 | 3 -> 3 | 2 -> 1 | 0 -> 0 | 3 -> 2 |
| `amoffat__sh-744` | 1 -> 2 | 11 -> 17 | 8 -> 4 | 2 -> 1 | 0 -> 0 | 2 -> 3 |
| `arviz-devs__arviz-2413` | 2 -> 9 | 15 -> 8 | 3 -> 4 | 10 -> 3 | 1 -> 0 | 4 -> 7 |
| `aws-cloudformation__cfn-lint-3749` | 1 -> 2 | 26 -> 22 | 3 -> 12 | 9 -> 1 | 5 -> 0 | 2 -> 9 |
| `aws-cloudformation__cfn-lint-3764` | 1 -> 1 | 12 -> 5 | 2 -> 4 | 4 -> 3 | 0 -> 0 | 1 -> 2 |
| **Total** | — | — | **19 -> 27** | — | **6 -> 0** | **12 -> 23** |

Interpretation:

- GT improved first-edit timing on four tasks and verification gap on all five.
- GT removed all detected loop steps.
- GT made initial gold-file arrival worse on three tasks and doubled aggregate
  wasted views.
- Better process metrics did not convert into more resolved tasks.

## Provider-bound GT delivery by task

These counts come from canonical provider-delivery rows, not from grepping
visible `GT` markers.

| Task | Provider-bound attempts | Delivered chars | Direct features mechanically fired |
|---|---:|---:|---|
| `aiogram__aiogram-1594` | 5 | 2,271 | `obligations`, `caller_contract`, `submit_refusal`, `GT_CERT_DELIVERY`, `GT_SS_SUBMIT_RED` |
| `amoffat__sh-744` | 9 | 3,874 | `obligations` |
| `arviz-devs__arviz-2413` | 29 | 9,323 | `obligations` |
| `aws-cloudformation__cfn-lint-3749` | 36 | 12,192 | `obligations`, `caller_contract`, `def_partition` |
| `aws-cloudformation__cfn-lint-3764` | 11 | 2,689 | `obligations` |
| **Total** | **90** | **30,349** | **6 distinct direct-feature rows** |

The fact-occurrence counts were:

- `obligations`: 86 occurrences, delivered on all five tasks, on-time 5/5;
- `caller_contract`: 6 occurrences, delivered on two tasks, on-time 6/6;
- `def_partition`: 1 occurrence, delivered on one task, timing not evaluable;
- `submit_refusal`: 1 occurrence plus two CAP owner rows, delivered on
  `aiogram`, but false/harmful.

## The 17-feature audit

| Feature | Mechanical verdict | Correctness/timing audit |
|---|---|---|
| `GT_CERT_DELIVERY` | FIRED | **HARMFUL FALSE FIRE**; inherited false submit refusal |
| `GT_CHANGE_SURFACE` | TRIGGER-ABSENT | Correct quiet; no net-new-file requirement |
| `GT_EDIT_CHECK` | ARBITRATED | Evaluated, withheld; no attributable syntax error |
| `GT_HYPOTHESIS` | ARBITRATED | Evaluated, withheld |
| `GT_LOC_RESLOT` | TRIGGER-ABSENT | Correct quiet |
| `GT_PATCH_DELTA` | ARBITRATED | Evaluated, withheld; no proven caller-breaking signature change |
| `GT_SS_SUBMIT_RED` | FIRED | **HARMFUL FALSE FIRE**; inherited false submit refusal |
| `caller_contract` | FIRED | Provider delivery proven; on-time 6/6; content truth/authority still UNMEASURED |
| `covering_red` | ARBITRATED | Evaluated 52 times and withheld |
| `def_partition` | FIRED | Provider delivery proven once; content truth/authority and timing still UNMEASURED |
| `localization` | TRIGGER-ABSENT | Correct quiet after 43 evaluated windows |
| `newfile_precedent` | TRIGGER-ABSENT | Correct quiet after 38 evaluated windows |
| `obligations` | FIRED | **Fully proven**: truth, authority, delivery, and on-time 5/5 |
| `recovery` | ARBITRATED | Evaluated 19 times and withheld; no false recovery delivery |
| `signature_delta` | ARBITRATED | Evaluated 82 times and withheld |
| `submit_refusal` | FIRED | **HARMFUL FALSE FIRE**; unattributed failing check was incorrectly promoted |
| `syntax_result` | ARBITRATED | Evaluated 82 times and withheld |

Mechanical inventory summary:

- FIRED: 6
- TRIGGER-ABSENT/correct quiet: 4
- ARBITRATED/withheld: 7
- NO-INSTRUMENTATION: 0
- DELIVERY-FAILURE: 0

Honest proof summary:

- Fully truth + authority + delivery proven: 1/17 (`obligations`)
- Delivery/timing proven but content truth incomplete: 2/17
  (`caller_contract`, `def_partition`)
- Fired incorrectly/harmfully: 3/17 (the linked submit-refusal family)
- Correctly quiet: 4/17
- Evaluated and arbitrated away: 7/17

Therefore “6/17 fired” is not equivalent to “6/17 worked.”

## Live defects found and fixed

### 1. Staged-copy edit attribution

Live command shape:

```text
cat > /tmp/edit_context.py ... &&
cp /tmp/edit_context.py aiogram/fsm/context.py
```

GT attributed the edit to `/tmp/edit_context.py`, not the repository
destination. Consequences:

- post-edit context targeted the wrong subject;
- edit-quality metrics became applicable but empty;
- the exact-129 diagnosis became non-publishable;
- submit verification selected/associated evidence with the temporary file.

Fix: `cp` destination attribution now takes precedence over the earlier
temporary redirect in runtime predicates and both metric extractors.

### 2. Unattributed covering failure bypassed correct-or-quiet

The pre-submit summary recorded:

- syntax: `ok`, attributed;
- unit: `fail`, **not attributed**;
- integration: `pass`, attributed;
- `positive_failure: false`.

The submit seam nevertheless passed the raw unit `verdict=fail` into
`gate_verdict`, which ignored attribution and emitted
`covering_test_failed`. GT held a valid submission after the agent had seen
`159 passed, 25 skipped`, causing several unnecessary reasoning/search steps.

Fix:

- the presubmit summary now carries `attribution_satisfied`;
- `gate_verdict` blocks only when `verdict=fail` **and** attribution is not
  false;
- the attribution bit is preserved in the gate record and attestation replay.

## Verification of post-run fixes

- Exact live-shaped RED tests reproduced both failures before the fixes.
- Targeted submit/runtime tests: `107 passed`.
- Metric/edit-predicate suites: `104 passed, 1 skipped`.
- Complete runtime suite: `3038 passed, 169 skipped, 2 xfailed`.
- Python compilation: passed.
- `git diff --check`: passed.
- Offline five-task regrade after the edit-attribution fix:
  - exact inventory: 129/129;
  - publishable: true;
  - incomplete inputs: 0;
  - missing task/feature rows: 0;
  - leakage violations: 0;
  - dose violations: 0;
  - PERF aggregate failures: 0.

## What must happen next

Do not promote GT as a performance improvement from this smoke.

The next proof run must:

1. Build an immutable substrate from `665ba353`.
2. Use the exact same five tasks and DeepSeek v4 Flash.
3. Byte-match GT-off and GT-on system prompt, task prompt, thinking mode,
   templates, step limit, and cost limit. The existing frozen baseline is
   useful descriptively but is not a clean causal comparator because thinking
   and template configuration differ.
4. Fail the promotion gate if any unattributed/false submit refusal is
   delivered.
5. Add a lifecycle-level obligation resurfacing budget. Repeating obligations
   86 times is context pollution unless a state transition makes the reminder
   newly necessary.
6. Require per-feature proof at four levels: trigger opportunity, producer
   result, provider-bound delivery timing, and externally scored consequence.

Until that run exists, the correct status is:

**GT instrumentation is comprehensive, some process behavior improves, but
measured performance advantage is unproven and this smoke exposed a harmful
pre-submit intervention.**
