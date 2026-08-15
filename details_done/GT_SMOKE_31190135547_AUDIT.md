# GT-on ten-task smoke `31190135547` — final audit

Date: 2026-08-07  
Commit: `e92089720a731b9821a97bab5612deac5ca14bc9`  
Workflow: `tb2_miniswe_central.yml`  
Arm: treatment / `all17` / `integration_mode=active` / `preflight_mode=shadow`  
Model: `deepseek-v4-flash`, temperature 1.0, timeout multiplier 1.0

## Outcome gate

The workflow completed all ten tasks, all ten were graded, and none was
censored or errored. Verifier outcome was **9/10**, exactly matching the
frozen GT-off outcome. `gpt2-codegolf` was the sole miss in both arms.

This passes outcome preservation but does **not** pass the strict efficiency
gate: seven mutually solved tasks have at least one positive primary-resource
delta. The treatment is therefore not approved for the 89-task run.

## Deep matched deltas

These are deep trajectory metrics (prompt input plus completion output), not
the baseline cache-hit column mislabeled as completion tokens in the frozen
summary. Delta is GT-on minus GT-off.

| Task | Outcome | Tokens B→GT | Δ tokens | Calls B→GT | Δ calls | Steps B→GT | Δ steps | Actions B→GT | Δ actions | Pareto |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| break-filter-js-from-html | 1→1 | 185,193→205,282 | +20,089 | 12→17 | +5 | 12→17 | +5 | 16→18 | +2 | fail |
| cobol-modernization | 1→1 | 1,482,783→1,967,531 | +484,748 | 39→57 | +18 | 39→57 | +18 | 59→58 | −1 | fail |
| fix-code-vulnerability | 1→1 | 462,305→362,386 | −99,919 | 33→26 | −7 | 33→26 | −7 | 33→26 | −7 | pass |
| gpt2-codegolf | 0→0 | 8,878,853→2,153,898 | −6,724,955 | 59→35 | −24 | 59→34 | −25 | 59→34 | −25 | n/a |
| headless-terminal | 1→1 | 4,984,143→2,903,440 | −2,080,703 | 86→67 | −19 | 86→67 | −19 | 86→67 | −19 | fail |
| llm-inference-batching-scheduler | 1→1 | 3,098,098→2,069,098 | −1,029,000 | 41→41 | 0 | 41→41 | 0 | 42→41 | −1 | fail |
| modernize-scientific-stack | 1→1 | 44,373→67,181 | +22,808 | 8→10 | +2 | 8→10 | +2 | 16→23 | +7 | fail |
| portfolio-optimization | 1→1 | 459,690→1,184,519 | +724,829 | 26→42 | +16 | 26→42 | +16 | 30→46 | +16 | fail |
| schemelike-metacircular-eval | 1→1 | 8,587,359→7,010,458 | −1,576,901 | 100→84 | −16 | 100→84 | −16 | 125→84 | −41 | fail |
| write-compressor | 1→1 | 1,040,219→177,098 | −863,121 | 16→9 | −7 | 16→9 | −7 | 17→10 | −7 | pass |

Aggregate deep delta:

* tokens: `29,223,016 → 18,100,891` (**−11,122,125; −38.06%**);
* API calls: `420 → 388` (**−32**);
* assistant steps: `420 → 387` (**−33**);
* model actions: `483 → 407` (**−76**);
* effective actions: `483 → 461` (**−22**);
* normalized cost: `$0.2804 → $0.4102` (**+$0.1298; +46.30%**).

The cost increase despite lower total tokens is caused by the treatment’s
different cache-miss/output mix. Aggregate token reduction is not sufficient
to claim efficiency when per-task Pareto and normalized cost fail.

## Delivery and timing audit

Across the ten central receipts:

* 310 effects were produced and 310 applied;
* 7 coalesced model guidance deliveries were recorded;
* 9 effects carried `provider_payload` disposition (multiple effects can share
  one provider delivery);
* 7/7 payloads arrived in the first eligible provider request;
* late payloads: 0; predictive payloads: 0;
* provider request hashes: 388/388, coverage 1.0;
* provider budget failures: 0;
* payloads containing derived artifact anchors: 0;
* feature missed triggers: 0; false fires: 0;
* batch interrupts: 0; submit holds: 0.

The seven deliveries were `covering_red` (break-filter),
`newfile_precedent` (gpt2, headless, write), `GT_EDIT_CHECK` (portfolio), and
`covering_red` plus `GT_EDIT_CHECK` (schemelike). Three deliveries had an
immediate anchor relation; this is a behavioral proxy, not proof that the
model acknowledged or causally used GT.

The repaired artifact boundary was exercised: the only natural
`signature_delta` receipt (cobol) named `program.py`; no cache or `.pyc` path
entered its payload or claim anchors. One signature receipt was explicitly
suppressed as `represented_in_action_history`.

## Feature lifecycle

The smoke naturally fired 16/17 feature IDs across the ten tasks. `recovery`
did not receive its exact repeated-failure trigger. This does not invalidate
the all-17 provider-free proof; it means the stochastic smoke did not naturally
exercise every exact event. Every produced effect was applied, but private
effects are not model-visible by design.

Effect accountability totals:

* engine internal state: 221;
* existing engine actuation: 20;
* provider payload: 9;
* prepared decision frame: 1;
* unread private state: 52;
* expired unconsumed claim: 7.

The last two categories are explicitly not claimed as causal model help.

Context/compiler totals:

* 6,411 candidate facts accounted;
* 506 selected state facts, 2,076 represented facts, and 2,449 controller-only
  facts;
* 230 bounded compaction calls;
* 0 duplicate turns represented and 0 unique assistant-reasoning characters
  removed;
* 72,314 total GT context characters added, of which 71,139 were state-frame
  characters and 1,175 were runtime advisories.

## Replay and interpretation

`python -m scripts.central_replay D:\gt_runs\31190135547` returned
`REPLAY_OK` for all ten trajectories. Replaying the archived trajectories
through the repaired delivery policy suppresses facts already represented in
the model history and retains only decision-new guidance.

The separate `central_regression_preservation_replay.py` witness is not
applicable to this run’s short `write-compressor` trajectory: its fixed
compressor witness requires either bounded observations or old-tool-result
compaction, while this trajectory never crossed that context threshold. This
is a witness precondition failure, not a task outcome failure.

## Decision

The integration repairs are mechanically validated: grounded delivery,
artifact exclusion, timing, hashing, accounting, and outcome preservation all
hold. The efficiency objective is **not yet proven**. The positive per-task
deltas—especially portfolio (+724,829 tokens, +16 calls, +16 steps, +16
actions) and cobol (+484,748 tokens, +18 calls, +18 steps)—must be diagnosed
before another paid smoke or the 89-task run.
