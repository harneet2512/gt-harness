# ENGINE round audit — which of the 17 features worked + cross-run delta

Run: `python scripts/engine_round_audit.py` (regenerates the tables below).
Rounds: r2=30738637714(+30740338420), r3=30755837073, r4=30757560927,
r5=30759441102, r6=30762787550, r7=30766572498, r8=30772336112,
r9=30789635818, r10=30816206132, r11=30837898981.

## Round-11 summary (2026-08-03)

- Rewards: **9/10 solved** (write-compressor held 1.0; gpt2-codegolf 0.0 noise).
  `errored` 5→3.
- **Harness-probes went 49→101**: round-10 grep-anchor removal helped, but r11's
  model read `env` and found the `GT_*` producer flags the workflow exported.
  Fixed in `8205fbb` (GT_* no longer in the container env). r12 measures the drop.
- **Ladder (r11)**: obligations 105 delivered / 92 acted (87%); localization
  24/13 (54%). Payload: 151 facts, 0 empty-evidence.
- Full status: `ENGINE_ROUND11_STATUS.md`.

## Round-10 summary (2026-08-03)

- Rewards: **9/10 solved** (write-compressor recovered to 1.0 after 0.0 in r9;
  gpt2-codegolf 0.0, temp-1.0 noise). All task jobs + merge green.
- **Harness-probe actions dropped 109 (r9) → 38 (r10)**. The r9 fix (journal
  ID sanitization) helped but the model still greps internal event/blocker
  NAMES in readable files. Fixed in `6a193da` (renamed to neutral ids); the
  r11 smoke measures the effect.
- **Ladder census (r10)**: obligations 109 delivered / 95 acted (87%);
  localization 11/7 (64%); covering_red 32 delivered. Facts deliver usable
  payload the model follows.
- Payload integrity: 161 facts, 0 empty-evidence across r10 trajectories.

## A. Which of the 17 features worked (delivered >= 1 usable fact, per round)

| feature | r2 | r3 | r4 | r5 | r6 | r7 | r8 |
|---|---|---|---|---|---|---|---|
| obligations | - | - | - | YES | YES | YES | YES |
| localization | - | - | - | - | - | part* | YES |
| def_partition | YES | YES | - | YES | - | YES | YES |
| syntax_result | YES | - | - | - | - | YES | - |
| covering_red | YES | YES | - | YES | - | - | YES |
| recovery | - | - | part* | part* | part* | - | - |
| signature_delta | - | - | - | - | - | - | - |
| newfile_precedent | - | - | - | - | - | - | - |
| submit_refusal | - | - | - | - | - | - | - |
| GT_EDIT_CHECK | - | - | - | - | - | - | - |
| GT_PATCH_DELTA | - | - | - | - | - | - | - |
| GT_LOC_RESLOT | - | - | - | - | - | part* | YES |
| GT_SS_SUBMIT_RED | - | - | - | - | - | - | - |
| GT_HYPOTHESIS | - | - | - | - | - | - | - |
| GT_CHANGE_SURFACE | - | - | - | - | - | - | - |
| GT_CERT_DELIVERY | (fires every delivery receipt; not a `<fact>` byte) | | | | | | |
| caller_contract | REMOVE by disposition — never delivered | | | | | | |

`part*` = delivered but with an EMPTY `"evidence": ""` payload (the payload-drop
bug, fixed before r8).

**Read:** 6 of the 9 FACT features deliver usable facts by r8 (obligations,
localization, def_partition, covering_red, syntax_result, recovery). The
trigger-rare three (signature_delta, newfile_precedent, submit_refusal) never
hit a live trigger in any smoke; they are proven by the provider-free gate's
real-seam e2e (submit-while-RED → exactly 1 SUPPRESS) and the visibility matrix
(16/16).

## B. FACT delivery totals per round (usable / empty-payload / acted)

| round | tasks | facts | empty | acted | act% |
|---|---|---|---|---|---|
| r2 | 10 | 47 | 0 | 33 | 70% |
| r3 | 9 | 2 | 0 | 0 | 0% |
| r4 | 3 | 2 | 2 | 2 | 100% |
| r5 | 10 | 248 | 3 | 3 | 1% |
| r6 | 10 | 76 | 1 | 0 | 0% |
| r7 | 10 | 73 | 7 | 3 | 4% |
| r8 | 10 | 142 | 0 | 8 | 5% |

**Read:** r8 = 142 usable facts, ZERO empty payload (the bug-1 fix holds).
r5's 248 facts were mostly obligations spam before selectivity; r8's 102
obligations are relevance-gated (96% acted in the ladder).

## C. Solved (reward 1.0) per task per round

| task | r2 | r3 | r5 | r6 | r7 | r8 |
|---|---|---|---|---|---|---|
| fix-code-vulnerability | Y | Y | Y | Y | Y | Y |
| portfolio-optimization | Y | Y | Y | Y | Y | Y |
| modernize-scientific-stack | Y | Y | Y | Y | Y | Y |
| headless-terminal | Y | Y | Y | Y | Y | Y |
| llm-inference-batching-scheduler | Y | Y | Y | Y | Y | Y |
| break-filter-js-from-html | Y | Y | Y | Y | Y | Y |
| write-compressor | - | Y | N | Y | Y | Y |
| gpt2-codegolf | N | - | Y | N | N | N |
| schemelike-metacircular-eval | Y | Y | Y | Y | Y | Y |
| cobol-modernization | Y | Y | Y | Y | Y | Y |

**Read:** solve rate is stable 9/10 from r5 onward (gpt2-codegolf is the only
failure; pure temp-1.0 noise — it solved r5, failed r6/r7/r8). r8 = 9/10.

## D. Total_tokens round-over-round delta (identical measurement)

| task | r2→r3 | r3→r5 | r5→r6 | r6→r7 | r7→r8 |
|---|---|---|---|---|---|
| fix-code-vulnerability | -30% | -33% | +8% | -7% | +342% |
| portfolio-optimization | -42% | - | +54% | +45% | -63% |
| modernize-scientific-stack | +28% | -15% | +192% | -55% | +91% |
| headless-terminal | -57% | - | -36% | +67% | +387% |
| llm-inference-batching-scheduler | -22% | - | +24% | -54% | +3324% |
| break-filter-js-from-html | -50% | - | -54% | +11% | +3842% |
| write-compressor | +135% | - | +19% | +8% | +365% |
| gpt2-codegolf | - | - | +143% | +170% | -84% |
| schemelike-metacircular-eval | -12% | - | +14% | -4% | -12% |
| cobol-modernization | -13% | - | -38% | -74% | +899% |

## E. Root cause of the r8 token explosion (NEW, 2026-08-02)

4 tasks hit the 100-action cap in r8 (llm 102, break-filter 113, headless 102,
cobol 105) yet ALL still solved. Action counts and harness-probing:

| task | r7 actions | r8 actions | r8 harness-probe actions |
|---|---|---|---|
| llm-inference-batching-scheduler | 27 | 102 | 27 |
| break-filter-js-from-html | 20 | 113 | 35 |
| headless-terminal | 42 | 102 | 10 |
| cobol-modernization | 37 | 105 | 0 (repeated its own verify block 16x) |

**The model deliberately audited the GT harness.** Assistant text in r8
trajectories, verbatim: "Let me look at the GT engine's verification contract
and controller modules", "Let me understand how failed-action blockers get
resolved/cleared", "how the gate's bounce/override works", "how the gate
refuses", "how repository snapshots detect changes", "how the store append
works, since triggering an exception there could fail-open the gate". This is
the model reverse-engineering the submit gate + RED/predicate machinery to find
a bypass — NOT a payload leak (r8 = 0 empty-evidence facts) and NOT a broken
feature.

**Implication:** the r8 RED/obligation facts + submit gate make the harness
sufficiently visible and gameable that a temp-1.0 model spends 40-50% of its
budget auditing instead of solving. Solve rate held (9/10) but efficiency
collapsed. This is the honest open item: reduce the model's incentive to probe
the harness (e.g. neutral submit-refusal wording that names obligations by
task meaning, not `pred-<sha>` predicate IDs; avoid rendering internal
predicate identities in model-visible bytes).
