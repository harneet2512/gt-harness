# ENGINE Deep Research — why 0/low facts, why inert, and the fix to activate all 17 DIRECT features

Evidence: round-2 witness `30738637714` (10 tasks, 9 trajectories) + the
GroundTruth gateway source (`D:\Groundtruth\src\groundtruth\runtime\gateway.py`)
+ `gt_engine/miniswe_evidence.py` + `gt_engine/engine/runner.py`.

## Q1. Why are delivered facts 0 or low when GT is the engine?

The ENGINE owns the boundary (normalize → decide → execute → compile →
receipt, on time, per action) — proven. But **the ENGINE's producers are a
2-producer subset with brittle triggers**, so most actions map to no fact:

| wired producer | trigger | result in round 2 |
|---|---|---|
| `syntax_result` | changed `.py` detected by `git status --porcelain` + `ast.parse` | fired in fix-code (39); **missed schemelike** (heredoc edits to `eval.scm`/`interp.py`) |
| `covering_red` | command matches `pytest`/`make test`/`go test`/… (exact words) | near zero — the model's verification is usually `python3 -m …` or custom scripts |
| `def_partition` | **the model must call the typed `groundtruth(...)` tool** | headless 7, portfolio 2, all others 0 |

Root causes:
1. **The groundtruth gateway was never ported.** `gateway._produce_raw_candidates`
   dispatches a producer for *every* semantic event — `file_view` →
   caller_contract_view, `edit_result` → patch_delta/signature_delta +
   caller_contract + change_surface (newfile_precedent), `test_result` →
   covering, `search_result`/`failed_search` → ranked_localization + outcome
   lattice (def_partition/body/trace/name_fold/wrong_surface), `submit` →
   submit_refusal. `gateway.produce_raw(event, state)` is documented as "the
   sole Gateway API for the canonical reasoning runtime" and is
   provider-free/deterministic. `runner.py::_postflight_facts` bypassed it.
2. **Dominant action types map to no wired producer.** Per-task census: the
   majority of actions are generic commands (`python3 …`, env checks, `ls`,
   installs), reads, searches, and heredoc edits — none of which the two wired
   producers handle. The gateway's obligations/localization/covering would fire
   on exactly those.
3. **Brittle edit detection.** `_git_changed_py` needs a git checkout and the
   porcelain `M`/`??` marker. schemelike's `python3 - <<'PYEOF' … write` edits
   produced 0 syntax facts — the workspace's git tracking did not surface the
   `.py` edits to the producer.

## Q2. Why are the facts inert even when delivered?

Causal trace (delivered → referenced → acted), round 2:

| task | delivered | referenced | acted | verdict |
|---|---|---|---|---|
| fix-code-vulnerability | 39 | 1 | 18 | confounded (model edits bottle.py anyway) |
| headless-terminal | 6 | 0 | 0 | inert (def_partition answer ignored) |
| portfolio-optimization | 1 | 0 | 0 | inert |
| break-filter-js-from-html | 1 | 0 | 0 | inert |
| others | 0 | 0 | 0 | — |

1. **Render order defeats attention.** `render()` emits raw output first, then
   a trailing `<result>/<fact>` block. This is exactly the "lost in the
   middle" position (Liu et al., 2023): mid/late context is under-weighted, so
   facts appended after long raw output are the least-attended bytes.
2. **Zero information gain.** Every syntax fact reported `ok:true` ("the file
   parses") — the model already knows this by reading the file.
   `def_partition` returned what `grep` would return. A fact that provides no
   information the model lacks cannot change behavior.
3. **No affordances.** The plan (§3.3) specified deterministic affordances
   (`read(path,line)`, `inspect_callers(symbol)`) so facts become next-step
   pointers. The engine never rendered them — facts are descriptive, not
   actionable.
4. **The model's next action is not bound to the fact.** There is no mechanism
   making the model consume the fact; the up/down token delta is temp-1.0
   variance, not causation.

## Q3. Latest research grounding

- **Lost in the Middle (Liu et al., 2023)** — models under-use middle context;
  trailing fact blocks are in the worst position. Fix: lead with the
  actionable fact, keep raw as the secondary payload.
- **Context engineering (Anthropic, 2025)** — minimal context, at the point of
  use, actionable, no duplication. Facts must be few, bound to the action, and
  tell the model what to do next.
- **Information gain / counterfactual** — an observation changes behavior only
  if it (a) supplies information the model does not already have and (b) is
  actionable. "File parses OK" fails (a); unanchored search output fails (b).
- **Agent-loop evidence (SWE-bench agent studies)** — extra context that does
  not reduce the search/verify burden does not improve solve rate; models act
  on their priors unless the fact redirects a decision.
- Consequence: the correct endpoint is the **receipt ladder (L2 referenced /
  L3 acted)**, not token deltas or solve rate. A fact the next action never
  targets is definitionally useless.

## The 17 DIRECT features — current working status and the gap

| # | feature (FACT) | wired in ENGINE? | status round 2 | action |
|---|---|---|---|---|
| 1 | caller_contract | no (REMOVE disposition) | — | absent by design |
| 2 | covering_red | partial (`_covering_red_artifact`) | ~0 | port gateway `covering` (any test outcome) |
| 3 | def_partition | typed tool only | 4 (headless) | keep; gateway `def_ref_partition` for search outcomes |
| 4 | localization | no | 0 | port gateway `ranked_localization` on search |
| 5 | newfile_precedent | no | 0 | port gateway `change_surface` on create |
| 6 | obligations | no | 0 | wire contract/obligation deltas into action observations |
| 7 | recovery | no | 0 | wire repeated-failure recovery fact |
| 8 | signature_delta | no | 0 | port gateway `patch_delta` on edit |
| 9 | submit_refusal | gate wired, needs a fresh blocker | 0 | keep; verify gate fires |
| 10 | syntax_result | yes (`_syntax_artifact`) | 39 | keep; harden edit detection |
| 11–17 | CAP_OWNER lineage | partial (receipts only) | — | attach byte-owner lineage to the delivered facts |

Only **1 of the 10 FACT features (syntax_result) demonstrably fired** in round
2; the plan requires all FACT features to fire on their triggers and the 7
CAP_OWNERs to bind lineage. The fix (next) ports the gateway so all producers
activate.

## The fix (architecture + research grounded)

1. **Port the gateway into the ENGINE compile step.** In `runner.py`, replace
   `_postflight_facts` with `gateway.classify_event` → `gateway.produce_raw`,
   compiled into canonical observations. Every action (read/edit/test/search/
   submit) then fires its producers: obligations, localization, covering_red,
   signature_delta, newfile_precedent, def_partition, recovery, submit_refusal.
2. **Lead with the actionable fact.** Render the decision+fact FIRST, raw
   second (attention), or bind facts inline at the exact raw anchor
   (`path:line`) instead of a trailing block.
3. **Value-gate + affordances.** Emit only facts with information gain
   (skip "parses OK"; prefer obligation/location/RED), and render an
   affordance (`read(path,line)`) per fact.
4. **Endpoint = receipt ladder.** Gate usefulness on L2 referenced / L3 acted.
5. **Harden edit detection** — use the gateway's edit classification
   (heredoc/tee/redirect aware) instead of `git status` alone.
6. **17-feature census gate** — a provider-free test asserting every FACT
   feature's producer fires when its trigger event occurs.
