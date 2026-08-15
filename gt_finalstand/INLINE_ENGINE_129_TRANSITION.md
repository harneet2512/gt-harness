# GroundTruth Inline Engine Transition — Complete 129-Row Design

Status: implementation design authority  
Inventory: 12 ACQ + 48 CAP + 11 FACT + 58 PERF = 129 unique identities  
Confidence: high for current timing and source reconstruction; moderate-high for the proposed interface; benchmark effect unknown until an engine-mode run

## 1. Decision

GroundTruth must become the sole action-to-observation interface whenever `MiniSweGtAgent` is selected. Mini-SWE remains the planner and reasoner. GT receives an already-selected action, binds it to the current repository/configuration snapshot, runs only deterministic producers justified by that action, executes the resulting interception decision, compiles one tool observation, and binds the exact delivered bytes to the provider exchange and immediate next action.

The exact seam is:

```text
Mini-SWE reasons and selects a concrete action
                     |
                     v
GT normalizes ActionRequest at the current snapshot
                     |
          deterministic preflight producers
                     |
          InterceptionDecision is executed
                     |
        exact original action executes if required
                     |
          deterministic postflight producers
                     |
       one compiled tool observation is appended
                     |
          next Mini-SWE reasoning call occurs
                     |
 provider response and immediate next action are receipted
```

GT cannot supply action-specific context before the model reveals the action without predicting intent. For a read, search, or test, post-execution evidence is correctly timed when it is included in the same tool observation before the next reasoning call. For a create/edit where evidence must affect the mutation before commitment, the interface must use a deterministic `PROPOSE -> PREFLIGHT -> COMMIT` transaction. That is a causal requirement, not a presentation choice.

The stock Mini-SWE loop is model query -> action execution -> observation formatting -> message append. The GT engine belongs around action execution and observation formatting, not in an unrelated future prompt and not in a task-text-based intent guess.

## 2. Current timing diagnosis

Current GT delivery is mixed:

- Predictive: task-start localization is derived from issue text before an action is selected.
- Reactive but detached: task/obligation deltas and recovery directives can be injected as global or separate user messages rather than the selected tool result.
- Same-observation and correctly timed for the next decision: post-search partitions, post-test RED, post-edit evidence, and syntax evidence can be spliced into the action's observation.
- Correct pre-execution seam but incomplete integration: submit is inspected after selection and before terminal commitment.
- Correct typed seam but incomplete decision execution: explicitly selected GroundTruth actions bypass Bash, but `PASS_THROUGH`, `AUGMENT`, and `REPLACE` are mostly labels rather than one mechanically executed state machine.

The target eliminates all model-visible global/prose injection except a minimal immutable session binding. Every dynamic fact must be joined to one selected `action_id` and one tool observation.

## 3. Public interface

### 3.1 SessionBinding

`SessionBinding` is not action-specific context. It binds immutable task bytes, repository root/scope, configuration, producer registry, and initial revision vector. It may expose the exact task-contract identity, but not predicted localization or inferred next actions.

### 3.2 ActionRequest

Required fields:

- `action_id`, `tool_call_id`, typed `kind`, exact literal arguments, and original shell form;
- pre-action repository/configuration/revision snapshot;
- requested fidelity and raw fallback;
- for mutations: exact target, expected preimage hash, proposed postimage/patch hash, and declared postconditions;
- for verification: exact command, environment fingerprint, selected scope, and timeout;
- for continuation: proposal token and the snapshot it authorizes.

Required action kinds include `SHELL`, `FILE_READ`, `SEARCH`, `SYMBOL_DEFINITIONS`, `SYMBOL_REFERENCES`, `SYMBOL_CALLERS`, `LOCALIZE`, `CREATE_PROPOSAL`, `EDIT_PROPOSAL`, `COMMIT_MUTATION`, `RUN_VERIFICATION`, `SYNTAX_QUERY`, and `SUBMIT`.

### 3.3 ActionResult

One result contains:

- execution state: `executed`, `held`, `rewritten`, or `suppressed`;
- complete raw result when required;
- zero or more typed `EvidenceArtifact` values;
- the executed `InterceptionDecision`;
- deterministic affordances such as `read(path,line)`, `inspect_callers(symbol)`, `view_prior_result`, or `rerun(command)` derived from artifact anchors;
- pre/post snapshots and the exact final observation hash.

Affordances are options, not recommendations. They let Mini-SWE continue its normal reasoning without a diversion into a second advisory channel.

### 3.4 Decision law

| Decision | Runtime meaning |
|---|---|
| `PASS_THROUGH` | Execute the exact original action now and preserve its raw observation. |
| `AUGMENT` | Execute the original action, preserve all raw bytes, and attach action-bound evidence. |
| `REPLACE` | Skip raw execution only for a certified, complete, exact typed equivalent. |
| `REWRITE` | Execute only an explicitly authorized, mechanically equivalent transformation. |
| `SUPPRESS` | Execute nothing; legal only before side effects under a narrow certified blocker/precondition. |

An incomplete typed request without an executable bound fallback is not `PASS_THROUGH`; it is an honest incomplete result. GT must never invent shell intent.

## 4. Complete ACQ transition (12/12)

ACQ rows are producer substrate and gates. They do not independently inject context.

| # | Identity | Decision | Exact inline role |
|---:|---|---|---|
| 1 | `graph_validity` | MODIFY | Run at snapshot/index publication and immediately before graph-backed queries. Emit only coverage/omission metadata into the requesting artifact; revoke replacement on any failure. |
| 2 | `structural_depth` | MODIFY | On-demand bounded traversal for an explicit caller/dependency/localization request. Bind accepted edge types, depth, configuration, visited-set hash, and truncation; never run as an unsolicited neighborhood dump. |
| 3 | `resolution_honesty` | KEEP | Mandatory artifact qualifier recording resolution method, trust tier, candidates, ambiguity, and unresolved bindings before the decision evaluator can allow replacement. |
| 4 | `type_intelligence` | MODIFY | Configuration-bound compiler/LSP adapter invoked only by explicit symbol/signature actions or exact proposed edits. Stateful failures and missing dependencies become omissions and raw fallback. |
| 5 | `lexical_FTS5` | MODIFY | Typed exact token/literal producer over a declared closed scope. It may `REPLACE` only when tokenizer, byte inventory, ignored paths, case rules, and revision are certified complete. |
| 6 | `body_retrieval` | MODIFY | Attach requested symbol/body ranges to an explicit read or symbol action. Preserve literal source when parser boundaries, preprocessing, or omitted bytes prevent complete equivalence. |
| 7 | `semantic_embedder` | REMOVE | Remove from the compliant engine and public schema. Keep only as an isolated comparison control because approximate model retrieval is not deterministic repository truth. |
| 8 | `LSP` | MODIFY | Typed adapter for definitions/references/callers with server version, initialization, workspace folders, overlays, document versions, build configuration, and capability receipt. Failure executes bound fallback. |
| 9 | `freshness_basis` | KEEP | Mandatory use-time revision-vector comparison for every producer, artifact, decision, transformation, and submit blocker. No stale artifact can replace or suppress. |
| 10 | `repo_scope` | KEEP | Session-bound closed inventory of roots, ignores, submodules, symlinks, generated files, dependencies, and exclusions. Every completeness claim names this scope. |
| 11 | `cochange_history` | MODIFY | On-demand advisory input only for explicit precedent/impact actions. Bind history revision and biases; never convert correlation into dependency or automatic co-change. |
| 12 | `determinism` | KEEP | Passive canonicalization/replay gate over recorded inputs. It certifies byte stability, not semantic completeness, and contributes hashes/versions rather than model advice. |

## 5. Complete CAP transition (48/48)

CAP rows are control and lineage. Seven may own bytes; fourteen are eligibility gates; twenty-seven are mediators. None may manufacture a new FACT.

| # | Identity | Decision | Target engine stage and visibility |
|---:|---|---|---|
| 1 | `GT_BRIEF_MINIMAL` | REMOVE | Remove the standalone brief gate. Requested fidelity and the canonical observation schema determine rendering; no model-visible independent payload. |
| 2 | `GT_BRIEF_NATIVE` | REMOVE | Replace native brief prose mediation with the canonical observation renderer. Preserve transformation lineage only. |
| 3 | `GT_CERT_DELIVERY` | MODIFY | Sole final renderer/receipt owner for the canonical submit certificate; model-visible only inside the selected submit result. |
| 4 | `GT_CHANGE_SURFACE` | MODIFY | Byte owner for `newfile_precedent` at explicit create/edit preflight and same-action postcondition; never trigger from task-text prediction. |
| 5 | `GT_COMPLETION_CERT` | MODIFY | Compile registered blocker heads into the `SUBMIT` decision artifact; no separate prompt or independent truth claim. |
| 6 | `GT_CONTENT_LEG` | REMOVE | Fold content fields and anchors into `EvidenceArtifact`; eliminate the separate envelope leg and duplicate byte path. |
| 7 | `GT_CONTRACT_BILATERAL` | MODIFY | Join exact task obligations with repository-scoped action targets inside the requesting action artifact. No global bilateral prose. |
| 8 | `GT_CONTRACT_MODE` | REMOVE | Replace profile/mode selection with typed semantics and capability policy frozen in `SessionBinding`. |
| 9 | `GT_CONTRACT_NATIVE` | REMOVE | Remove native contract renderer; use canonical action-scoped obligation fields. |
| 10 | `GT_D7_RELATEDNESS` | MODIFY | Artifact eligibility reason for advisory relatedness only. It can exclude an option but cannot authorize replacement or become a global feature flag. |
| 11 | `GT_EDIT_CHECK` | MODIFY | Byte owner for `syntax_result` as a declared mutation postcondition in the same edit/create observation. |
| 12 | `GT_EDIT_OVERLAY` | MODIFY | Preflight snapshot overlay gate for proposed edits and dirty files; contributes authority/omission state, not prose. |
| 13 | `GT_EVIDENCE_NATIVE` | REMOVE | Eliminate the parallel native evidence form; `EvidenceArtifact` is the only public evidence carrier. |
| 14 | `GT_GATEWAY` | REMOVE | Remove the legacy action-after-the-fact gateway from model-visible delivery. Producers may be reused behind typed dispatch. |
| 15 | `GT_GATEWAY_EDIT_BRIDGES` | REMOVE | Replace bridge inference with canonical `EditTransaction` pre/post events bound to the selected mutation action. |
| 16 | `GT_GATEWAY_NATIVE` | REMOVE | Eliminate the duplicate native gateway delivery path. |
| 17 | `GT_GLOBAL_ARBITER` | MODIFY | Deterministically select/coexist artifacts for one action. Do not choose one unrelated global “dose” that hides another fact answering the same action. |
| 18 | `GT_HYPOTHESIS` | MODIFY | Byte owner for neutral exact failure-recurrence evidence. Remove imperative hypothesis steering; model-visible only in the failing/repeated action result. |
| 19 | `GT_INSEAM_METRICS` | MODIFY | Passive event observer at normalize/decision/execute/compile/deliver boundaries. Never model-visible. |
| 20 | `GT_L6_FRESH` | MODIFY | Collapse into the central freshness evaluator at preflight and postflight; no independent rendered context. |
| 21 | `GT_LANE_ENVELOPE` | REMOVE | Replace lane-specific envelope with canonical artifacts and one final observation. |
| 22 | `GT_LOC_RESLOT` | MODIFY | Byte owner for action-keyed `localization` attached to the exact search/read/localize result; remove task-start reslot prediction. |
| 23 | `GT_NUDGE_NATIVE` | REMOVE | Remove imperative nudge rendering. Deterministic affordances may be returned inside recovery/localization artifacts without selecting the next action. |
| 24 | `GT_OBLIGATION_FRESHNESS` | MODIFY | Eligibility gate comparing task, patch, verification, and predicate revisions immediately before obligation delivery or submit use. |
| 25 | `GT_PATCH_DELTA` | MODIFY | Byte owner for prospective and actual `signature_delta` artifacts bound to one edit proposal/commit transaction. |
| 26 | `GT_POST_SEARCH` | REMOVE | Remove master post-search guess gate. Exact selected search arguments directly dispatch relevant producers. |
| 27 | `GT_POST_SEARCH_NATIVE` | REMOVE | Eliminate native post-search prose mediation; search observation carries structured results and related fields. |
| 28 | `GT_REGISTRY_ENFORCE` | KEEP | Mandatory producer/FACT/owner authorization gate before artifact admission; record decision and registry revision. |
| 29 | `GT_SCOPE_NATIVE` | REMOVE | Fold scope, exclusions, and coverage into request/snapshot/artifact contracts. |
| 30 | `GT_SEM_BODY` | MODIFY | On-demand body-retrieval mediation for an explicit read/symbol request; no unsolicited semantic body injection. |
| 31 | `GT_SS_ACK_FORM` | REMOVE | Do not require acknowledgment prose or a diversionary action. The next action receipt provides behavioral lineage. |
| 32 | `GT_SS_ACK_METRICS` | REMOVE | Move any useful acknowledgment measure to passive PERF derivation; remove it from runtime context control. |
| 33 | `GT_SS_ARBITER_V2` | REMOVE | Replace single-dose legacy arbitration with the per-action deterministic artifact compositor. |
| 34 | `GT_SS_COHERENCE_V2` | MODIFY | Retain only passive episode/loop classification and exact eligibility inputs for `recovery`; no independent context. |
| 35 | `GT_SS_DEDUP2` | MODIFY | Deduplicate identical artifact identity at the same action/revision while preserving explicit no-delivery receipts. |
| 36 | `GT_SS_ELIGIBILITY` | MODIFY | Central typed eligibility lattice: registry, freshness, scope, semantics, ambiguity, omissions, requested fidelity, and action-kind compatibility. |
| 37 | `GT_SS_EXEC_TRUTH` | MODIFY | Gate execution-specific facts on exact raw command/result/environment receipts. Never derive execution truth from prose. |
| 38 | `GT_SS_LATE_DROP` | MODIFY | Drop artifacts whose action, snapshot, or transaction no longer matches before observation compilation; record the drop reason. |
| 39 | `GT_SS_NOVELTY` | MODIFY | Suppress redundant unchanged evidence within the same state, but never suppress raw output or a newly requested exact result. |
| 40 | `GT_SS_PROVENANCE` | MODIFY | Canonical lineage projection joining producer, owner, transformation, final bytes, provider response, and immediate next action. |
| 41 | `GT_SS_RECOVERY_V2` | MODIFY | Admit recovery only on exact normalized action/failure/environment/revision identity and certified prior outcome. |
| 42 | `GT_SS_SHADOW` | KEEP | Testing/rollback posture that computes and receipts decisions without altering model-visible bytes. It is not the GT-on product posture. |
| 43 | `GT_SS_SUBMIT_RED` | MODIFY | Contribute fresh closed RED heads to the selected `SUBMIT` decision; `GT_CERT_DELIVERY` renders the single refusal. |
| 44 | `GT_STEER_NATIVE` | REMOVE | Remove standalone steering. Evidence exposes neutral options; Mini-SWE chooses. |
| 45 | `GT_VERIFICATION_PLAN` | MODIFY | Bind explicitly selected verification actions and task obligations to a structured plan artifact; never silently schedule tests. |
| 46 | `GT_VERIFY_EXECUTE` | MODIFY | Execute only agent-selected or mutation-contract-declared verification. Record exact command/environment/raw diagnostics; no hidden probe. |
| 47 | `GT_XSESSION_MEMORY` | REMOVE | Remove from the deterministic engine. Cross-session state is an external historical input and must not silently affect an action. |
| 48 | `GT_XSESSION_RANKUP` | REMOVE | Remove cross-session rank steering from model-visible delivery; isolated research control only if explicitly manifested. |

## 6. Complete FACT transition (11/11)

| # | Identity | Current timing | Target synchronization and decision |
|---:|---|---|---|
| 1 | `caller_contract` | Currently available mainly after a file view or post-edit gateway event; typed caller action is removed. | BUILD typed caller/read/edit-preflight producer. Reads preserve raw and `AUGMENT`; a complete typed caller query may `REPLACE`; edit constraints use proposal/commit and may hold precommit only on a certified positive constraint. |
| 2 | `cochange_prior` | The registry describes an advisory historical prior, while the current gateway doctrine already treats co-change as internal ranking rather than deliverable evidence. | REMOVE as a model-facing FACT; retain fresh repo-scoped `cochange_history` only as an internal tie-breaker among candidates admitted by independent evidence. It cannot add candidates or strengthen a decision. |
| 3 | `covering_red` | Computed only after an actual selected test or optional hidden post-edit probe. | MODIFY into the same selected verification or declared mutation-postcondition result. Preserve complete diagnostics and `AUGMENT`; no hidden test selection and no replacement. |
| 4 | `def_partition` | Currently inferred after grep/search and omitted from the public typed schema. | BUILD typed definition/partition query. Ordinary search executes literally then may `AUGMENT`; certified closed typed partitions may `REPLACE`; ambiguity/staleness executes fallback. |
| 5 | `localization` | Task-start issue-text ranking predicts relevance; reactive search ranking may still be issue-keyed rather than action-keyed. | MODIFY: remove unsolicited model-visible task-start localization. Key results to explicit `SEARCH`, `READ`, or `LOCALIZE` arguments. Approximate ranking stays advisory `AUGMENT`; enumerative exact search is a separate replaceable fact. |
| 6 | `newfile_precedent` | Failed-search path guesses creation; post-create path arrives after destination/content are committed. | MODIFY into `CREATE_PROPOSAL` preflight using exact target/content, then commit token. Advisory precedent normally `AUGMENT`s; certified preconditions may hold before creation; never autonomously rewrite destination. |
| 7 | `obligations` | Full contract/deltas are injected globally before model calls; later states derive from real edits/tests but are detached from their action. | MODIFY: keep only immutable task-contract identity at boot. Attach relevant exact task spans and status deltas to matching read/edit/test results and selected submit. Normally `AUGMENT`; fresh RED may feed submit suppression. |
| 8 | `recovery` | Derived after repeated actual failures but delivered as a separate user directive with imperative hypothesis language. | MODIFY into neutral recurrence/no-information-gain data in the same failed/repeated action result. Exact identity only, raw preserved, `AUGMENT`; expose options without choosing them. |
| 9 | `signature_delta` | Exact transaction data is stored; legacy gateway may append a selected subset after the edit already committed. | MODIFY into prospective edit-preflight plus actual post-commit artifact. Same edit result `AUGMENT`s exact old/new signatures and bounded callers; incomplete graph cannot claim completeness. |
| 10 | `submit_refusal` | Submit is already intercepted at the correct selected-action seam, but advisory mode accepts and refusal text is detached as a user directive. | BUILD coherent `SUBMIT` action handling. Fresh closed blockers produce `SUPPRESS` and a structured same-tool result; stale/unknown/incomplete state `PASS_THROUGH`s native submit. |
| 11 | `syntax_result` | Exact postimages are parsed/stored, while a hidden assistive subprocess may generate visible failures; typed syntax is separate. | MODIFY into a declared mutation postcondition attached to the same result, with exact postimage/parser/revision/diagnostics. Explicit certified typed syntax may `REPLACE`; unsupported languages preserve raw and record omission. |

## 7. Complete PERF transition (58/58)

PERF rows never become context. They are passive runtime or offline evaluation derived from immutable action, transaction, delivery, provider, verifier, and gold-surface receipts. “Impact” means temporal mediation unless a paired experiment supports causality.

| # | Identity | Decision | Exact observation source |
|---:|---|---|---|
| 1 | `gold_in_L1_top_k` | KEEP offline | Frozen localization artifact joined to independently curated gold files. |
| 2 | `gold_rank` | KEEP offline | Ordered localization candidates and gold surface. |
| 3 | `files_to_gold_view` | MODIFY measurement | Ordered `FILE_READ`/parsed shell-read ActionRequests before first gold view. |
| 4 | `steps_to_gold_view` | MODIFY measurement | Global action IDs before first gold view. |
| 5 | `files_to_gold_edit` | MODIFY measurement | Distinct viewed/targeted files before first qualifying gold edit. |
| 6 | `steps_to_gold_edit` | MODIFY measurement | Global action IDs before first qualifying gold edit. |
| 7 | `localization_precision` | KEEP offline | Localization candidate set versus frozen gold denominator. |
| 8 | `localization_recall` | KEEP offline | Gold files covered by localization candidates. |
| 9 | `false_file_rate` | KEEP offline | Action-bound file targets versus frozen gold surface. |
| 10 | `exploration_ratio` | MODIFY measurement | Typed action kinds plus shell-AST classification; opaque Bash remains unclassified. |
| 11 | `gold_view_precision` | KEEP offline | Read targets versus gold surface. |
| 12 | `wasted_views` | KEEP offline | Repeated/noncontributing views under a preregistered equivalence and outcome rule. |
| 13 | `navigation_directness` | KEEP offline | Ordered action path to gold view/edit, controlled for task/repo size. |
| 14 | `self_localization_needed` | KEEP offline | Acquisition actions before a relevant action-bound localization artifact is consumed. |
| 15 | `edit_attempts_per_gold` | KEEP offline | Ordered mutation transactions joined to gold surface. |
| 16 | `rewrite_count` | KEEP offline | Repeated postimage changes for the same file/symbol across transactions. |
| 17 | `compile_failures_after_edit` | KEEP | Revision-bound edit and subsequent raw verification receipts. |
| 18 | `edit_revert_rate` | KEEP offline | Transaction postimages later restored to prior hashes. |
| 19 | `first_edit_correctness` | KEEP offline | First transaction per file joined to final accepted patch/verifier outcome. |
| 20 | `patch_size` | KEEP | Exact final transaction/diff bytes under a frozen line/byte definition. |
| 21 | `patch_files` | KEEP | Exact final changed-path set. |
| 22 | `contract_compliance_rate` | KEEP offline | Fresh obligation/caller artifacts joined to independent verification. |
| 23 | `signature_changes_warned` | MODIFY measurement | Signature-changing transactions with same-action `signature_delta` delivery receipt. |
| 24 | `p2p_regression_rate` | KEEP offline | Producer/interface compatibility corpus and verified transaction outcomes. |
| 25 | `caller_breakage_count` | KEEP offline | Verified caller failures attributable to a revision-bound patch. |
| 26 | `scope_coverage` | KEEP offline | Required/gold surface covered by discovered/edited scope. |
| 27 | `scope_excess` | KEEP offline | Action/patch surface outside frozen required scope. |
| 28 | `multi_file_discovery` | KEEP offline | Required multi-file surface and ordered action targets. |
| 29 | `scope_gap_files` | KEEP offline | Required files absent from discovered/edited scope. |
| 30 | `degenerate_loop_count` | MODIFY measurement | Canonical action/result/snapshot identity recurrence, not regex intent inference. |
| 31 | `steps_in_loops` | MODIFY measurement | Global action IDs inside certified recurrence intervals. |
| 32 | `nudge_recovery_steps` | RENAME/MODIFY | Measure steps from neutral recovery artifact to preregistered recovery; remove “nudge” product semantics. |
| 33 | `coherence_collapse_count` | MODIFY measurement | Passive episode-state rule over canonical actions/results; never itself context. |
| 34 | `stuck_duration` | MODIFY measurement | Action IDs within a frozen stuck-state definition. |
| 35 | `test_before_submit` | KEEP | Selected verification receipt at the current patch revision before submit. |
| 36 | `test_runs_total` | MODIFY measurement | Typed verification actions plus shell-AST-confirmed tests; opaque commands excluded. |
| 37 | `test_edit_ratio` | KEEP | Revision-bound verification action count divided by edit transactions. |
| 38 | `obligation_test_rate` | KEEP offline | Task obligations mapped to qualifying fresh verification receipts. |
| 39 | `verify_gap` | KEEP | Action/revision distance between last qualifying verification and submit. |
| 40 | `impact_rate` | KEEP mediation-only | Provider-bound delivered observation and immediate next action under a frozen change rule. |
| 41 | `per_tag_impact` | KEEP mediation-only | `impact_rate` stratified by canonical FACT/artifact kind. |
| 42 | `gt_tokens_injected` | MODIFY measurement | Tokenization of exact GT-owned fields in final provider-consumed observations. |
| 43 | `gt_tokens_per_pivot` | MODIFY measurement | GT token count divided by preregistered action-state changes; no causal claim. |
| 44 | `nudge_compliance_rate` | RENAME/MODIFY | Option-follow rate for neutral recovery affordances; remove imperative nudge semantics. |
| 45 | `L1_followed_rate` | MODIFY measurement | Delivered localization artifact anchors joined to immediate subsequent file action. |
| 46 | `contract_consulted_rate` | MODIFY measurement | Delivered obligation IDs joined to subsequent matching verification/edit actions. |
| 47 | `obligation_completion_rate` | KEEP offline | Final fresh obligation states with explicit UNKNOWN retained. |
| 48 | `nudge_action_rate` | RENAME/MODIFY | Neutral affordance selection rate; never evidence of correctness. |
| 49 | `scope_chain_followed` | MODIFY measurement | Delivered scope-edge option joined to immediate next action. |
| 50 | `total_steps` | KEEP | Monotonic action IDs, separating proposals, commits, and provider iterations. |
| 51 | `total_tokens_in` | KEEP | Provider-accounted input usage bound to committed request identity. |
| 52 | `total_tokens_out` | KEEP | Provider-accounted output usage bound to response identity. |
| 53 | `total_cost_usd` | KEEP | Provider-accounted cost; local estimates separately labeled. |
| 54 | `cache_hit_rate` | KEEP | Provider cache usage receipts. |
| 55 | `tokens_per_gold_edit` | KEEP offline | Provider tokens divided by qualifying gold edits; zero-denominator rule frozen. |
| 56 | `cost_per_resolved` | KEEP offline | Total matched cost divided by independently verified resolved tasks. |
| 57 | `gt_token_overhead` | MODIFY measurement | Exact GT-attributable provider-consumed tokens versus matched GT-off input. |
| 58 | `wasted_token_rate` | KEEP offline | Tokens assigned to preregistered wasted-action classes; opaque actions excluded. |

## 8. Natural in-flow context shapes

The model should not receive a second advisory lecture. Its chosen tool result should expose structured, local fields:

```json
{
  "status": "executed",
  "raw": "...exact native result...",
  "evidence": {
    "primary": [...],
    "related": [...],
    "obligations": [...],
    "postconditions": [...],
    "ambiguity": [...],
    "omissions": [...]
  },
  "options": [
    {"kind": "read", "path": "src/a.py", "line": 40},
    {"kind": "callers", "symbol": "pkg.mod.fn"}
  ]
}
```

The action result answers the action first. Related context is subordinate, provenance-rich, and bounded. Empty related evidence produces no filler. Options use exact artifact anchors and are not automatically executed.

## 9. Required implementation sequence

1. Add an `ENGINE` product posture and make `MiniSweGtAgent` select it; retain `MiniSweAgent`/GT-off as the baseline and rollback.
2. Add canonical normalization for every selected action and a sequential barrier for stateful action batches.
3. Implement the authoritative five-decision executor and truthful raw fallback.
4. Move all dynamic evidence into same-action observations; remove task-start localization, separate recovery/refusal user directives, old gateway splicing, and legacy profile authority.
5. Add proposal/commit transactions for creates and edits where precommit context is required.
6. Wire exact postflight artifacts for edit, syntax, signature, test, obligation, and recurrence evidence.
7. Restore certified scoped definition/reference/caller producers and keep unsupported language/configuration pairs absent.
8. Emit one canonical delivery receipt only after provider response commitment and bind the immediate next action.
9. Derive all 58 PERF rows passively/offline from canonical receipts; no PERF row may affect model context.
10. Rebuild the finalstand receipts and documentation, preserving the historical advisory witness strictly as historical evidence.

## 10. Acceptance gates

- Exactly 129 unique inventory identities remain accounted for: ACQ 12, CAP 48, FACT 11, PERF 58.
- No ACQ, CAP eligibility/mediator, or PERF row is mislabeled as independent knowledge.
- No dynamic model-visible evidence lacks the selected action ID and exact snapshot.
- Literal and opaque Bash retain exact semantics.
- `PASS_THROUGH` executes the exact original bound action in the same turn.
- `AUGMENT` preserves every raw byte.
- `REPLACE` passes completeness, freshness, configuration, ambiguity, omission, determinism, and sentinel-leak gates.
- Mutations receive precommit guidance only through explicit proposal/commit transactions.
- No hidden test, build, live probe, or cross-session memory executes without an explicit selected action or declared transaction postcondition.
- One tool action produces one compiled observation and one byte-owner lineage chain.
- A delivery receipt is committed only after the provider consumes the exact observation and returns a response.
- GT-off remains byte/state equivalent to the supported stock Mini-SWE path.
