# GroundTruth Phase II: Terminal Implementation Roadmap

Status: execution authority
Decision vocabulary: `BUILD`, `MODIFY`, `KEEP`, `REMOVE`
Planning confidence: high
Evidence basis: accepted GroundTruth deterministic-observation monograph

## 1. Outcome and hard boundary

This roadmap ends the GroundTruth project. It does not create another research phase or an unowned backlog. Every capability must reach a terminal implementation state, pass its release gate, or be removed from advertised and executable support.

GroundTruth's job is narrow: after Mini-SWE has selected an action, GroundTruth performs deterministic work that matches that action, compiles the result into typed evidence, and binds the exact delivered bytes to the provider exchange. GroundTruth does not choose goals, predict actions, infer hidden intent, or replace model reasoning.

The claim “GT never reduces baseline performance” cannot be established for every possible future task by testing. The enforceable engineering contract is stronger than a promise and narrower than an impossibility:

1. GT-off behavior is byte- and state-equivalent to the stock supported Mini-SWE path.
2. Unsupported, stale, ambiguous, or incomplete analysis falls back to raw behavior.
3. Each capability has a kill switch and an augmentation-only fallback.
4. Replacement and suppression require offline equivalence, leak, freshness, and determinism gates.
5. Without population-level paired evidence, stock Mini-SWE remains the default and GroundTruth remains explicit opt-in.
6. A capability that fails a gate is removed from replacement or suppression, not left in an unfinished state.

## 2. Final architecture

```text
Mini-SWE planner selects action
             |
             v
      ActionRequest binding
             |
      +------+-----------------------+
      | typed GroundTruth action?    |
      +------+-----------------------+
             | yes                         no / opaque / mixed
             v                                  |
 deterministic producer                        v
             |                            PASS_THROUGH
             v                                  |
      EvidenceArtifact                          |
             |                                  |
             +---------------+------------------+
                             v
                  InterceptionDecision
                             |
               raw fallback mechanically bound
                             |
                             v
                  exact final observation
                             |
                             v
                provider request / response
                             |
                             v
                     DeliveryReceipt
                             |
                             v
                 immediate next selected action
```

### 2.1 Mini-SWE interface

`KEEP` the stock `bash(command)` action without semantic reinterpretation. `BUILD` a harness-native `groundtruth(...)` function tool beside it. Do not patch the installed Mini-SWE package. The harness adapter owns registration, dispatch, result formatting, and receipts while preserving one normal model call per iteration.

The native tool initially exposes these typed kinds:

- `literal_search`
- `definition_lookup`
- `reference_lookup`
- `caller_lookup`
- `syntax_query`
- `patch_impact`
- `verification_status`

An invalid kind, invalid argument, unsupported language, stale snapshot, unresolved configuration, or incomplete producer returns an explicit incomplete artifact and selects pass-through or augmentation. It never silently changes the requested operation.

### 2.2 Public contracts

#### `ActionRequest`

| Field | Required behavior |
|---|---|
| `schema_version` | Versioned canonical contract; unknown major versions reject safely. |
| `action_id` | Unique within one run and stable across all downstream receipts. |
| `kind` | Closed typed enum; Bash remains a distinct literal kind. |
| `arguments` | Kind-specific validated value object, never an unparsed semantic guess. |
| `repository_snapshot` | Exact snapshot ID including dirty state and relevant filesystem inputs. |
| `configuration_id` | Toolchain, language, build, and analyzer configuration identity. |
| `requested_fidelity` | Exact, sound-overapproximate, execution-specific, or raw. |
| `original_shell_form` | Present only when the selected action originated as shell text. |
| `request_hash` | Hash of canonical serialization excluding no semantic field. |

#### `EvidenceArtifact`

| Field | Required behavior |
|---|---|
| `artifact_id` / `action_id` | Stable join to the action and delivery chain. |
| `answer` | Direct result matching the typed request. |
| `anchors` | Repository-relative file, byte/line span, symbol identity, and revision. |
| `witnesses` | Positive evidence needed to audit the result. |
| `producer` | Producer name, binary/library version, configuration, and build hash. |
| `freshness` | Source, graph, build, coverage, verification, and task revisions. |
| `semantics` | `exact`, `sound_overapprox`, `execution_specific`, or `incomplete`. |
| `coverage` | Scope searched and language/configuration dimensions covered. |
| `ambiguity` | Candidate count and unresolved binding causes. |
| `omissions` | Unsupported files, generated inputs, configurations, or dynamic behavior. |
| `raw_fallback` | Raw bytes or a content-addressed reference sufficient for restoration. |
| `artifact_hash` | Hash of canonical final artifact bytes. |

#### `InterceptionDecision`

The closed decision enum is `PASS_THROUGH`, `AUGMENT`, `REPLACE`, `REWRITE`, and `SUPPRESS`. Every decision records a reason code, required and observed semantics, freshness verdict, coverage verdict, ambiguity verdict, selected artifacts, raw-output requirement, transformation version, and final-observation hash.

`REPLACE` is legal only for a typed action with matching semantics, closed scope, a certified producer, a matching revision vector, canonical deterministic output, allowed ambiguity, declared omissions, a raw fallback, and passing adversarial equivalence tests. `SUPPRESS` is legal only for submit with fresh closed-scope blockers. Native tests and builds are augmented, not replaced.

#### `DeliveryReceipt`

The receipt binds action-request hash, pre-state revision, raw-result hash, transformation version and input hash, evidence hashes, final-observation hash, exact provider-payload hash, provider request identity, provider response identity/hash, immediate next-action hash, capability lineage, byte-owner lineage, and any fail-open or rollback event.

## 3. Priority roadmap

### P0: make intervention trustworthy

| ID | Decision | Implementation | Benchmark impact | Efficiency / tokens / exploration | Regression risk | Complexity | Dependencies |
|---|---|---|---|---|---|---|---|
| P0-01 | BUILD | Observation compiler contracts, canonical serialization, and decision engine | Enabling; no direct solve claim | Small metadata cost; enables later removal of redundant exploration | High if contracts omit semantic state | High | None |
| P0-02 | BUILD | Repository snapshot and multi-domain revision vector authority | Enabling; prevents false evidence | Hashing cost offset by safe reuse | High if dirty or generated state is missed | High | P0-01 |
| P0-03 | MODIFY | One generated language registry for harness and indexer | Neutral directly; prevents coverage lies | Removes duplicate maintenance and unsupported queries | Moderate | Medium | P0-02 |
| P0-04 | BUILD | Native `groundtruth` action tool beside unmodified Bash | High enabling value | Avoids shell-discovery sequences; one model call remains | High if tool schema or dispatch changes Bash | High | P0-01, P0-02 |
| P0-05 | MODIFY | Ordered provider delivery pipeline at the actual final request boundary | Enabling; makes delivery measurable | Small hashing/storage cost | High if binding occurs before final serialization | High | P0-01, P0-04 |
| P0-06 | BUILD | Exact delivery and next-action receipts | Neutral directly; makes impact attributable | Metadata cost; enables removal of dead injections | Moderate privacy/storage risk | High | P0-05 |
| P0-07 | MODIFY | Composite graph revision certification, twin retention, and query freshness guard | Moderate by preventing wrong evidence | Avoids unnecessary full rebuilds after certification | High if current source and vendored binary diverge | High | P0-02 |
| P0-08 | BUILD | Global and per-capability modes, kill switches, fail-open and rollback | Protects baseline | Negligible steady-state cost | Low after tests | Medium | P0-01 |
| P0-09 | REMOVE | Embeddings, whole-graph dumps, all-pairs closure, unrestricted co-change delivery, universal source replacement | Prevents negative interventions | Reduces compute, storage, and irrelevant tokens | Low; check for hidden consumers | Medium | P0-08 |

### P1: compile exact action-bound evidence

| ID | Decision | Implementation | Benchmark impact | Efficiency / tokens / exploration | Regression risk | Complexity | Dependencies |
|---|---|---|---|---|---|---|---|
| P1-01 | BUILD | Typed exact lexical search with certified zero results | Expected high on search-heavy tasks | Removes repeated grep/view sequences; usually fewer raw bytes | Moderate from ignored files or encoding mismatch | High | P0-02, P0-04 |
| P1-02 | MODIFY | Ordered atomic multi-file edit transaction | Expected moderate through correct impact evidence | Eliminates repeated reconstruction of changed surface | High for renames/deletes/symlinks | High | P0-02 |
| P1-03 | MODIFY | Immediate per-file syntax evidence | Expected moderate through earlier correction | Can avoid manual syntax checks; modest token reduction | Moderate due toolchain mismatch | Medium | P1-02 |
| P1-04 | MODIFY | Exact patch plus signature and affected-caller evidence | Expected moderate/high on API edits | Removes caller-search repetition; evidence may be richer, not always smaller | High if impact is presented as exact | High | P1-02, P0-07 |
| P1-05 | MODIFY | Structured build/test augmentation with all raw diagnostics retained | Expected moderate through better verification | Faster diagnosis; added structure can add tokens | Low because raw is retained | Medium | P0-01, P0-05 |
| P1-06 | BUILD | Incremental invalidation with atomic full-reindex fallback | Enabling for every post-edit query | Large latency saving compared with unconditional full rebuild | High for signature and delete invalidation | High | P0-02, P0-07, P1-02 |
| P1-07 | BUILD | Leak, replay, action-identification, equivalence, freshness, and cost suites | Protects solve rate; no direct gain | Test cost only | Low | High | P0/P1 producers |
| P1-08 | REMOVE | Legacy prepend-only delivery after compiler parity | Prevents duplicate/conflicting evidence | Reduces duplicate tokens and maintenance | High until parity proven | Medium | P0-06, P1-07 |

### P2: finish repository reasoning coverage

| ID | Decision | Implementation | Benchmark impact | Efficiency / tokens / exploration | Regression risk | Complexity | Dependencies |
|---|---|---|---|---|---|---|---|
| P2-01 | MODIFY | Configuration-bound definitions, references, and callers | Expected high on cross-file tasks | Replaces several acquisition steps when certified | High for overloads/dynamic imports | Very high | P0-03, P0-07, P1-06 |
| P2-02 | BUILD | Full shipped-language adversarial certification corpus | Enabling and protective | CI cost; removes unsupported maintenance claims | Low | Very high | P2-01 |
| P2-03 | MODIFY | Anchored localization with coverage and omission reporting | Expected moderate | Narrows first views; may add concise anchors | Moderate from ranking bias | Medium | P1-01, P0-07 |
| P2-04 | MODIFY | Stable delta-only task obligations | Expected low/moderate through verification discipline | Removes repeated checklist prose | Moderate if parser overstates requirements | Medium | P0-02, P0-05 |
| P2-05 | MODIFY | Exact repeated-failure recovery ledger | Expected low/moderate on loops | Avoids identical failed attempts | Moderate if normalization conflates failures | Medium | P0-02, P0-06 |
| P2-06 | BUILD | Configuration and build graph adapters | Expected moderate on compiled/configured repositories | Reduces dependency exploration; construction cost varies | High from configuration incompleteness | Very high | P0-03, P0-07 |
| P2-07 | BUILD | Per-language typed symbol replacement for certified pairs | Expected high where available | Large exploration and byte reduction | Very high; strict gate required | High | P2-01, P2-02, P2-06 |
| P2-08 | REMOVE | Advertised typed pairs that cannot meet a result contract | Protects baseline | Reduces false calls and maintenance | Low with Bash fallback | Medium | P2-02 |
| P2-09 | REMOVE | Contradictory language claims and unsupported extension aliases | Neutral directly | One registry reduces confusion | Low | Low | P0-03, P2-02 |

### P3: enforce, validate, release, close

| ID | Decision | Implementation | Benchmark impact | Efficiency / tokens / exploration | Regression risk | Complexity | Dependencies |
|---|---|---|---|---|---|---|---|
| P3-01 | MODIFY | Final provenance-rich advisory `newfile_precedent` | Expected low | Can avoid precedent searches; small evidence cost | Moderate from misleading precedent | Medium | P0-06, P2-03 |
| P3-02 | BUILD | Fresh closed-scope submit blocker and narrow suppression | Expected moderate through fewer invalid submissions | Avoids doomed submit iterations | Very high | High | P1-03, P1-05, P2-04 |
| P3-03 | REMOVE | Predictive dynamic test dependency as a product claim | Protects against false omission | Reduces compute and misleading tokens | Low | Low | P1-05 |
| P3-04 | BUILD | Execute owner-approved single matched Mini-SWE witness | Reward tied on one task; descriptive only | Calls fell, while actions/exploration/raw bytes rose | High risk of overgeneralizing one sample | Medium | All implementation and offline gates |
| P3-05 | KEEP | Keep stock Mini-SWE default and GT explicit opt-in | Protects baseline under insufficient efficacy evidence | Preserves optional deterministic tooling without forced overhead | Low | Low | P3-04 |
| P3-06 | KEEP | Retain implemented controlled paths; remove nothing solely from one witness | Avoids unsupported migration churn | Existing switches retain rollback and isolation | Low | Low | P3-05 |
| P3-07 | BUILD | Final runbook, schemas, compatibility manifest, benchmark report, rollback and release checklist | Operational protection | Reduces support and incident time | Low | Medium | P3-04, P3-06 |
| P3-08 | KEEP | Permanent stock Mini-SWE path | Baseline control and emergency recovery | No GT benefit; essential comparison path | Low | Low | P0-08 |
| P3-09 | BUILD | Terminal completion/removal receipt for every roadmap item | Project-closeout integrity | Small documentation cost | Low | Medium | All items |

## 4. Complete 17-capability implementation matrix

The CSV authority is [direct_capabilities.csv](direct_capabilities.csv). This table expands the four axes.

| Capability | Overall | Deterministic knowledge | Representation | Evidence delivery | Interception |
|---|---|---|---|---|---|
| `caller_contract` | MODIFY | MODIFY into configuration-bound, sound-overapproximate callers | MODIFY into qualified bindings and caller edges with revision/config keys | MODIFY to return all anchors, ambiguity, coverage, and omissions | BUILD typed caller action; replace only under complete certified scope |
| `covering_red` | MODIFY | KEEP execution-specific truth | MODIFY into revision-bound verification artifacts | MODIFY to pair structured verdicts with complete raw diagnostics | MODIFY at immediate post-test/build observation |
| `def_partition` | MODIFY | MODIFY into a complete candidate partition for the declared configuration | MODIFY symbol storage around qualified identities and binding witnesses | MODIFY to deliver every candidate and the resolution basis | BUILD typed definition action; replace only when its partition is complete |
| `localization` | MODIFY | KEEP approximate ranking | MODIFY into hybrid lexical/syntax/graph anchors | MODIFY to expose scores, coverage, omissions, and source anchors | MODIFY to task-start or explicitly action-bound augmentation; never absence proof |
| `newfile_precedent` | MODIFY | KEEP incomplete advisory knowledge | MODIFY into a stable precedent record with repository history identity | MODIFY to include witnesses, provenance, relevance basis, and omissions | KEEP advisory augmentation only |
| `obligations` | MODIFY | KEEP facts exact to task bytes and parser version | MODIFY into stable obligation IDs with state transitions | MODIFY to emit deltas and source binding instead of repeated prose | MODIFY at task start, verification state changes, and submit window |
| `recovery` | MODIFY | MODIFY so exactness requires identical normalized action, failure, pre-state, and environment | MODIFY into an attempted-remedy and outcome ledger | MODIFY to state match conditions, prior outcome, and expiration | MODIFY to augment immediately on a repeated identical failure |
| `signature_delta` | MODIFY | MODIFY into exact patch facts plus sound affected-symbol overapproximation | MODIFY into ordered atomic multi-file patch transactions | MODIFY to include raw diff, signature witnesses, affected callers, ambiguity | MODIFY immediately after the edit transaction |
| `submit_refusal` | MODIFY | KEEP only fresh registered blockers within declared closed scope | MODIFY into revision-bound blocker registry entries | MODIFY to include blocker witnesses and verification receipts | BUILD narrow submit suppression with one-step raw rollback |
| `syntax_result` | MODIFY | KEEP parser/toolchain-specific results | MODIFY into per-file revision-bound receipts | MODIFY to include structured verdict and native diagnostics | MODIFY immediately post-edit and on explicit typed syntax query |
| `GT_CHANGE_SURFACE` | MODIFY | KEEP as non-producer | MODIFY into explicit owner lineage | MODIFY into a byte-owner receipt | MODIFY to fire only with `newfile_precedent` delivery |
| `GT_PATCH_DELTA` | MODIFY | KEEP as non-producer | MODIFY into explicit owner lineage | MODIFY into a byte-owner receipt | MODIFY within the atomic post-edit transaction |
| `GT_LOC_RESLOT` | MODIFY | KEEP as non-producer | MODIFY into explicit owner lineage | MODIFY into a byte-owner receipt | MODIFY at task-start or explicit localization action |
| `GT_SS_SUBMIT_RED` | MODIFY | KEEP as non-producer | MODIFY into explicit owner lineage | MODIFY into a byte-owner receipt | BUILD attachment to certified submit decisions |
| `GT_EDIT_CHECK` | MODIFY | KEEP as non-producer | MODIFY into explicit owner lineage | MODIFY into a byte-owner receipt | MODIFY attachment to immediate syntax evidence |
| `GT_HYPOTHESIS` | MODIFY | KEEP as non-producer | MODIFY into explicit owner lineage | MODIFY into a byte-owner receipt | MODIFY attachment only to exact recovery evidence |
| `GT_CERT_DELIVERY` | MODIFY | KEEP as non-producer | MODIFY into final-payload lineage and receipt join | MODIFY into the exact delivered-byte receipt | MODIFY at the actual provider payload boundary |

The seven CAP owners never claim independent knowledge. Their acceptance test is delivery lineage, including a valid zero-delivery receipt when their owned fact did not reach the model.

## 5. Engineering TODOs

Every TODO below is terminal. “Remove operation” is a successful terminal result when a producer cannot satisfy its advertised result contract.

### FS-001: Observation compiler contracts

- **Decision / objective:** BUILD canonical `ActionRequest`, `EvidenceArtifact`, `InterceptionDecision`, and `DeliveryReceipt` types and a pure decision function.
- **Likely files:** new modules under `gt_engine/`; `gt_engine/gt_session.py`; GroundTruth `contracts/`, `evidence/`, and `delivery/` packages.
- **Components and data:** versioned enums/value objects, canonical UTF-8 JSON, content hashes, reason-code registry, producer registry.
- **Algorithm:** validate request; resolve producer; compare snapshot/config; evaluate semantics, coverage, ambiguity, omissions, and freshness; select the least destructive legal decision; serialize once.
- **Acceptance:** canonical byte equality across process, path-order, and locale variants; unknown versions fail open; no decision lacks a reason code or raw policy.
- **Tests:** constructor/property tests, golden serialization, malformed schema, enum exhaustiveness, decision truth table, replay determinism.
- **Expected outcome:** one auditable model-visible evidence path. Direct benchmark effect is enabling. Regression risk high until golden parity exists.

### FS-002: Snapshot and revision authority

- **Decision / objective:** BUILD one snapshot identity covering committed revision, dirty patch, untracked relevant files, symlinks, file identities, configuration, graph, build, coverage, verification, and task state.
- **Likely files:** `gt_engine/indexer.py`, integration/session state, GroundTruth foundation/state/index packages.
- **Data and algorithm:** normalized relative paths; content hashes; ordered Merkle-style manifest; explicit external-input fields; no timestamp as truth unless recorded as an input.
- **Acceptance:** any relevant edit/add/delete/rename/symlink/config change changes the proper revision component; irrelevant cache changes do not.
- **Tests:** dirty worktree matrix, case/path separators, generated files, ignored-file policy, identical snapshots in separate roots.
- **Expected outcome:** stale evidence becomes mechanically ineligible. Enabling benchmark impact; moderate hashing overhead.

### FS-003: Generated language registry

- **Decision / objective:** MODIFY the indexer registry into the sole language source and generate harness/analyzer manifests.
- **Likely files:** `gt_engine/indexer.py`, GroundTruth parser registry, build scripts, [language_support.csv](language_support.csv).
- **Data and algorithm:** language ID, extensions, parser identity, syntax capability, symbol capability, build adapters, terminal status; deterministic generation and diff check.
- **Acceptance:** exactly 30 named languages; no duplicate extension ownership without explicit precedence; CI fails on drift.
- **Tests:** registry generation golden, extension collisions, case normalization, unknown extension.
- **Expected outcome:** no false support claims and one maintenance point.

### FS-004: Native GroundTruth action tool

- **Decision / objective:** BUILD a harness-native typed tool while KEEPING stock Bash unchanged.
- **Likely files:** `scripts/miniswe_gt_run.py`, `gt_engine/miniswe_runtime.py`, `gt_engine/gt_session.py`, tests around runner/runtime.
- **Data and algorithm:** strict tagged union for seven initial kinds; validate before dispatch; bind current snapshot; invoke one deterministic producer; compile observation; never add a model call.
- **Acceptance:** stock actions and prompts remain identical in GT-off; invalid requests yield typed errors; Bash `rg`, `sed`, views, tests, and mixed commands keep literal execution.
- **Tests:** tool schema golden, one-call-per-iteration, invalid arguments, mixed action batches, provider serialization, GT failure fallback.
- **Expected outcome:** deterministic evidence can arrive in the only useful window without intent guessing. High expected exploration impact.

### FS-005: Final provider boundary and delivery receipt

- **Decision / objective:** MODIFY current logical payload hashing into exact final-payload binding and BUILD the full receipt join.
- **Likely files:** `gt_engine/miniswe_runtime.py`, `gt_engine/miniswe_integration.py`, `gt_engine/miniswe_receipt.py`, `gt_engine/event_journal.py`.
- **Data and algorithm:** append-only hash chain plus content-addressed blobs; explicit callback order; provider-native ID when available and local ID otherwise; immediate next action joined after selection.
- **Acceptance:** unique sentinels prove which bytes do and do not enter the next request; response joins exactly one request; zero-delivery is recorded rather than inferred.
- **Tests:** retries, streaming/non-streaming response, exception before send, provider mutation, multiple actions, process restart, hash-chain tamper.
- **Expected outcome:** delivery claims become facts instead of post-hoc string matching. Enabling impact; moderate storage cost.

### FS-006: Feature control and rollback

- **Decision / objective:** BUILD global off/shadow/augment/replace/enforce modes and independent capability kill switches.
- **Likely files:** session config, runner flags, decision engine, run manifest.
- **Data and algorithm:** explicit precedence `global off > capability off > safety fallback > configured mode`; flags frozen into run identity.
- **Acceptance:** one setting restores stock behavior; an individual producer can be disabled without disabling receipts; no suppression outside enforce mode.
- **Tests:** configuration matrix, environment/config precedence, mid-run prohibition, fail-open injection, kill-switch receipts.
- **Expected outcome:** bounded regression blast radius and immediate rollback.

### FS-007: Graph digest and vendored binary certification

- **Decision / objective:** MODIFY graph release so the current composite revision and retained structural-twin witnesses are proven before the Linux indexer is revended.
- **Likely files:** GroundTruth `gt-index/cmd/gt-index/main.go`, `internal/store/revision.go`, Go tests, harness vendor manifest and `gt_engine/indexer.py`.
- **Data and algorithm:** canonical sorting and hashing of all query-visible tables after enrichment; binary source/build hash in snapshot; atomic DB swap.
- **Acceptance:** ten cold builds are byte-identical in semantic artifacts; path-order changes do not change digest; any query-visible mutation does; source, Windows test binary, and vendored Linux binary pass the same corpus.
- **Tests:** structural twins, assertions, closure, co-change tables, content FTS, receiver metadata, incremental/full equivalence.
- **Expected outcome:** graph evidence can be freshness-gated. No binary replacement before proof.

### FS-008: Exact lexical producer

- **Decision / objective:** BUILD typed literal search matching a declared scope and byte contract.
- **Likely files:** new evidence producer, compiler registry, lexical tests.
- **Data and algorithm:** explicit roots/globs/ignore policy, byte pattern or declared encoding, stable path/span sorting, closed-scope searched-file manifest, certified zero-result proof.
- **Acceptance:** result set equals the reference scanner on adversarial fixtures; literal Bash grep remains untouched; binary/encoding omissions are explicit.
- **Tests:** regex metacharacters as literals, Unicode, CRLF, binary, symlink, ignored/untracked, deletion during query, empty and huge files.
- **Expected outcome:** high expected reduction of repeated searches and raw repository bytes.

### FS-009: Atomic edit transaction

- **Decision / objective:** MODIFY single-target preimage capture into ordered multi-file before/after state.
- **Likely files:** `gt_engine/miniswe_runtime.py`, session/integration state, patch evidence modules.
- **Data and algorithm:** workspace fingerprint before action, changed-path reconciliation after action, rename pairing as evidence not assumption, exact before/after blobs, atomic transaction ID.
- **Acceptance:** captures edits, additions, deletions, renames, mode changes, symlinks, and partial command failure; no multi-file change is silently reduced to one file.
- **Tests:** compound commands, formatters, generated outputs, rename-plus-edit, delete/recreate, failed patch, concurrent filesystem noise fixture.
- **Expected outcome:** correct surface for syntax, signature, freshness, and verification evidence.

### FS-010: Syntax receipts

- **Decision / objective:** MODIFY `syntax_result` and `GT_EDIT_CHECK` into immediate per-file revision-bound evidence.
- **Likely files:** runtime post-action pipeline, GroundTruth syntax producer, verification modules.
- **Data and algorithm:** select registry-certified parser/toolchain; run against exact postimage; retain native diagnostics; bind configuration and producer version.
- **Acceptance:** every changed applicable file has a receipt or explicit unsupported record; success never means program correctness; raw tool output survives augmentation.
- **Tests:** malformed/valid corpus, macros, generated files, multi-language changes, missing toolchain, timeout, parser crash.
- **Expected outcome:** moderate expected reduction in manual syntax checks and faster correction.

### FS-011: Patch, signature, and callers

- **Decision / objective:** MODIFY `signature_delta`, `GT_PATCH_DELTA`, and `caller_contract` around the atomic transaction.
- **Likely files:** GroundTruth patch/symbol/call modules, harness evidence compiler.
- **Data and algorithm:** exact canonical diff; old/new symbol signatures; qualified binding; sound affected-caller union; ambiguity and unsupported configurations preserved.
- **Acceptance:** exact patch always reconstructs postimage; caller result never claims exactness without certified closed world; deleted and moved symbols remain anchored.
- **Tests:** overloads, shadowing, re-export, interface implementation, method receivers, macros, dynamic imports, signature-only and body-only edits.
- **Expected outcome:** moderate/high expected improvement on cross-file API changes and fewer wrong-surface edits.

### FS-012: Structured build and test evidence

- **Decision / objective:** MODIFY `covering_red` into execution-specific structured augmentation.
- **Likely files:** runtime action classification, verification modules, evidence formatter.
- **Data and algorithm:** command identity, cwd/environment digest, exit code, duration, selected targets, parsed failures, raw stdout/stderr hash and bytes, pre/post revision.
- **Acceptance:** structured parsing can fail without losing a raw byte; a passed test is scoped to its exact execution; repeated runs remain distinct receipts.
- **Tests:** pass/fail/timeout/signal, interleaved output, color, flaky sequence, compound test command, build that edits files.
- **Expected outcome:** moderate expected verification improvement; structure may add tokens but reduces diagnosis actions.

### FS-013: Incremental freshness

- **Decision / objective:** BUILD post-edit invalidation and incremental index update with atomic full rebuild fallback.
- **Likely files:** `gt_engine/indexer.py`, GroundTruth index/graph packages, Go incremental indexer.
- **Data and algorithm:** invalidation closure by changed file, symbol/signature, import/build config, delete/rename; build candidate DB; validate revision; atomic swap; otherwise full build.
- **Acceptance:** incremental and clean full results agree on the adversarial corpus; the old DB remains readable after failure; stale artifacts cannot be selected.
- **Tests:** source/config changes, signature fanout, generated files, deletion, rename, crash during write, locked DB, restart recovery.
- **Expected outcome:** safe post-edit reasoning with lower latency than full indexing.

### FS-014: Definition/reference/caller analyzers

- **Decision / objective:** MODIFY `def_partition`, `caller_contract`, and reference queries into configuration-bound analyzer adapters.
- **Likely files:** GroundTruth LSP/index/graph/adapters packages, compiler producer registry.
- **Data and algorithm:** qualified symbol keys; adapter result normalized to candidates, witnesses, semantics, scope, ambiguity, omissions; graph/name binding/build configuration union where required.
- **Acceptance:** every answer exposes configuration and scope; zero results require closed coverage; incomplete languages cannot be called through the typed public schema.
- **Tests:** overload, shadowing, dynamic import, macro, generated code, multiple configs, partial workspace, symlink, language-server failure.
- **Expected outcome:** high expected exploration reduction on cross-file tasks.

### FS-015: All-language certification

- **Decision / objective:** BUILD terminal certification for all 30 registry languages and REMOVE unsupported typed pairs.
- **Likely files:** cross-language fixtures/tests, generated compatibility manifest, provider adapters.
- **Data and algorithm:** matrix of language by operation by configuration; terminal values exact, sound overapproximation, execution specific, not applicable, or removed.
- **Acceptance:** no “experimental” pair ships; all applicable adversarial cases pass the result contract; the advertised tool schema is generated from certified pairs.
- **Tests:** each language fixture covers definitions, references, callers, syntax, generated/configured cases where meaningful; manifest completeness check.
- **Expected outcome:** broad coverage without lying about universality. CI and maintenance cost are high but finite.

### FS-016: Localization

- **Decision / objective:** MODIFY `localization` and `GT_LOC_RESLOT` into bounded advisory evidence.
- **Likely files:** `gt_engine/miniswe_evidence.py`, GroundTruth orientation/localization packages.
- **Data and algorithm:** hybrid exact lexical, syntax anchors, and graph proximity; deterministic scoring and tie order; scope/coverage/omissions always visible.
- **Acceptance:** never emits negative proof; every item has a stable source anchor and score explanation; missing index degrades to lexical anchors.
- **Tests:** deterministic ties, stale graph, irrelevant high-degree symbols, no-match task, non-code task, dirty files.
- **Expected outcome:** moderate expected time-to-first-correct-file improvement.

### FS-017: Obligations

- **Decision / objective:** MODIFY `obligations` into task-parser-bound state with delta delivery.
- **Likely files:** task contract, session/integration, evidence compiler.
- **Data and algorithm:** stable obligation ID from task span and parser version; states open/evidenced/satisfied/invalidated; never equate checklist completion with correctness.
- **Acceptance:** unchanged obligations are not repeated; edits or new verification can invalidate prior state; each item cites task bytes.
- **Tests:** ambiguous tasks, task update, repeated model turn, verification change, invalidation, submit request.
- **Expected outcome:** low/moderate verification benefit and lower repeated prompt bytes.

### FS-018: Exact failure recovery

- **Decision / objective:** MODIFY `recovery`, `GT_HYPOTHESIS`, and the failure ledger so reuse requires identical normalized conditions.
- **Likely files:** session failure tracking, evidence modules, delivery lineage.
- **Data and algorithm:** identity over action, cwd, relevant environment, pre-state revision, exit/signal, normalized diagnostic fingerprint, attempted remedy and outcome; expire on any identity change.
- **Acceptance:** identical failures retrieve prior outcomes; similar-only failures are labeled incomplete and do not prescribe action; no future action is predicted.
- **Tests:** same command/different revision, reordered nondeterministic diagnostics, environment difference, timeout, prior successful remedy, multiple failures.
- **Expected outcome:** low/moderate reduction in repeated failed actions.

### FS-019: Configuration and build graphs

- **Decision / objective:** BUILD on-demand build/configuration adapters used to qualify dependency and symbol evidence.
- **Likely files:** GroundTruth adapters/repo_intel/config packages; build fixtures.
- **Data and algorithm:** declared targets, source membership, generated inputs, configuration/toolchain identity, dependency edges with provenance; query only the requested slice.
- **Acceptance:** no all-config union presented as exact; unsupported systems are explicit; config changes invalidate affected evidence.
- **Tests:** monorepo, multiple targets, optional feature, generated source, missing tool, cyclic config, dirty build file.
- **Expected outcome:** moderate expected reduction in dependency exploration; construction cost paid on demand.

### FS-020: New-file precedent

- **Decision / objective:** MODIFY `newfile_precedent` and `GT_CHANGE_SURFACE` into final advisory-only evidence.
- **Likely files:** GroundTruth repository-history/precedent producer and delivery lineage.
- **Data and algorithm:** deterministic similarity over path, language, role, and nearby build membership; cite actual precedent files and revision; expose omissions.
- **Acceptance:** cannot trigger replacement or suppression; no precedent returns an empty advisory artifact; every recommendation is inspectable in raw source.
- **Tests:** no history, renamed precedent, generated files, multiple equally ranked examples, dirty new file.
- **Expected outcome:** low expected reduction in pattern-discovery searches with bounded regression risk.

### FS-021: Submit blocker and suppression

- **Decision / objective:** BUILD `submit_refusal`, `GT_SS_SUBMIT_RED`, and `GT_CERT_DELIVERY` around a closed blocker registry.
- **Likely files:** submit gate, verification state, runtime request-submit path, receipt pipeline.
- **Data and algorithm:** blockers have ID, producer, witness, scope, creating revision, invalidation rule, freshness check, and remediation status; suppression only in enforce mode.
- **Acceptance:** stale/open-scope/advisory blockers cannot suppress; one kill switch restores ordinary submit; suppressed actions and their absence from provider payload are receipted.
- **Tests:** fresh/stale blocker, blocker resolved by edit/test, multiple blockers, producer crash, false blocker fixture, enforcement off, rollback.
- **Expected outcome:** moderate expected reduction in doomed submissions; highest regression risk, so promotion occurs last.

### FS-022: Remove noncompliant capabilities

- **Decision / objective:** REMOVE semantic embedding/reranking, whole-graph dumps, all-pairs closure, unrestricted co-change injection, predictive dynamic test dependency, and universal raw replacement from default and advertised interfaces.
- **Likely files:** feature registry, configuration, evidence router, docs/tests, dead producer modules after dependency audit.
- **Algorithm:** find registrations and consumers; disable behind migration release; prove zero execution/visibility; remove code and flags after parity.
- **Acceptance:** no public schema, default config, prompt, or runtime branch exposes them; comparison experiment code remains isolated from compliant product code if required.
- **Tests:** registry snapshot, forbidden producer invocation spies, configuration rejection, prompt sentinel absence.
- **Expected outcome:** lower compute/token cost and fewer false interventions.

### FS-023: Offline validation suite

- **Decision / objective:** BUILD the complete pre-release validation battery.
- **Likely files:** harness tests, GroundTruth tests, Go tests, deterministic fixtures and local benchmark scripts.
- **Algorithms/tests:** shell AST identification against human gold; analyzer adversarial corpus; evidence sufficiency; freshness/invalidation; ten cold deterministic builds; sentinel observation leak; cold/incremental/query cost; GT-off parity.
- **Acceptance:** zero material information-loss failures; byte-identical semantic artifacts for identical recorded inputs; no leaked suppressed sentinel; matched revision outputs; complete action accounting.
- **Expected outcome:** prevents unsound promotion. Test cost is intentional.

### FS-024: Final matched Mini-SWE witness — COMPLETE

- **Decision / objective:** BUILD the owner-approved one-run witness comparing GT advisory with the frozen local GT-off baseline on `fix-code-vulnerability`.
- **Superseded design:** the six-arm, 10-task, 60-trial manifest and dry-run were never executed. They remain historical artifacts with `executed=false` and `provider_calls=0`; the owner explicitly removed them from the completion requirement.
- **Controlled identity:** Mini-SWE `2.2.8`, `deepseek-v4-flash`, identical provider fingerprint, task checksum, task prompt, temperature `1.0`, step/cost/timeout budgets, one attempt, and concurrency one.
- **Recorded outcome:** reward `1.0 -> 1.0`; provider calls `33 -> 25`; total actions `33 -> 37`; pre-edit exploration `19 -> 25`; raw pre-edit bytes `34,696 -> 43,009`.
- **Acceptance:** the matching identities, complete trajectories, GT receipts, independent reward, and descriptive analysis are frozen.
- **Claim boundary:** complete for the owner-defined engineering witness only. It supplies no population solve-rate, confidence-interval, non-inferiority, general efficacy, or exploration-reduction result.

### FS-025: Conservative release decision — COMPLETE

- **Decision / objective:** KEEP the stock Mini-SWE path as the default and KEEP GroundTruth explicit opt-in.
- **Reason:** the candidate tied reward and reduced calls on one task, but increased actions, pre-edit exploration, and raw bytes. One observation cannot establish a Pareto-dominant default.
- **Acceptance:** no baseline-default mutation; GT remains behind existing activation, fail-open, kill-switch, and rollback controls.
- **Expected outcome:** baseline behavior remains protected while the completed deterministic interface remains available for explicit use.

### FS-026: Release documentation and bounded closeout — COMPLETE

- **Decision / objective:** BUILD final schemas, compatibility manifest, operational runbook, bounded benchmark report, rollback guide, frozen evidence references, and completion ledger.
- **Likely files:** `gt_finalstand/` and final versioned release artifacts.
- **Acceptance:** all 26 TODO IDs are terminal; all 17 DIRECT rows and 129 role-audit rows are accounted for; all language-operation pairs are terminal; FS-023's provider-free artifact and FS-024's descriptive matched witness are recorded; the conservative FS-025 default decision is explicit.
- **Tests:** link checker, CSV/schema validation, hash verification, clean-machine runbook rehearsal, rollback rehearsal.
- **Expected outcome:** a closed project with an explicit one-task claim boundary rather than an inflated benchmark-wide conclusion.

## 6. Regression register

| Failure | How Mini-SWE becomes worse | Prevention | Rollback |
|---|---|---|---|
| Stale evidence | Model edits against obsolete code | Revision-vector equality and query freshness gate | Disable producer; raw pass-through |
| False completeness | Valid candidates or diagnostics disappear | Closed-scope corpus and raw fallback | Change replacement to augmentation or remove typed pair |
| Bash reinterpretation | Selected command no longer means what planner chose | Literal Bash path cannot enter semantic rewrite | Global GT-off or Bash-only mode |
| Duplicate evidence | Context grows and conflicting facts distract reasoning | One compiler path and byte-owner receipts | Disable compiler capability; restore raw-only |
| Analyzer configuration mismatch | Wrong definition/caller surface | Configuration identity and omission reporting | Remove affected language-operation pair |
| Incremental index corruption | Queries return mixed revisions | Candidate DB, validation, atomic swap, full rebuild | Pin last certified DB/binary and disable incremental mode |
| Provider receipt bound too early | Delivery claim does not match actual request | Bind after final serialization; sentinel leak tests | Mark delivery unknown and disable replacement |
| Structured test parser drops detail | Model misses native failure cause | Always retain raw stdout/stderr | Disable structured augmentation |
| Recovery over-normalizes | Prior failed remedy blocks a valid new attempt | Strict identity including revision/environment | Disable recovery producer and clear run-local ledger |
| Localization bias | Model views a plausible but wrong surface | Advisory label, anchors, coverage, raw search remains | Disable task-start localization |
| Submit false blocker | Correct solution cannot be submitted | Fresh closed registry and enforce-only mode | Turn off suppression and replay ordinary submit |
| Language registry drift | Tool advertises unsupported operations | Generated schema plus CI diff check | Remove drifted pair from schema |
| Hash/storage overhead | Tool latency consumes task budget | Cost gates, content addressing, bounded retention | Disable nonessential receipts while keeping minimal audit chain |
| GT internal exception | Agent loop crashes | Boundary exception containment and fail-open receipt | Global GT-off |

Every regression incident records the request, snapshot, raw bytes, decision, final bytes, provider identity, next action, capability flag state, and rollback exercised. No incident is closed merely because the failing feature was turned off; its advertised support must be repaired or removed.

## 7. All-language terminal policy

[language_support.csv](language_support.csv) contains the 30-row registry inventory. Each language-operation cell ends in one of these semantic release states:

- `exact`: complete for the declared scope and configuration.
- `sound_overapprox`: may include extras but cannot omit a valid result inside declared scope.
- `execution_specific`: true only of the exact recorded run.
- `not_applicable`: operation has no meaningful contract for that language.
- `removed`: operation is absent from the public schema for that language.

There is no experimental state at closure. A dynamic language may ship a deliberately broad, proven sound overapproximation. If soundness cannot be established, the operation is removed for that language and stock Bash/raw-source exploration remains available.

## 8. Release gates

### 8.1 Baseline gate

- GT-off produces identical model requests, selected actions, tool observations, workspace state, termination state, and budget accounting to the supported stock path.
- GT exceptions fail open without changing the selected action.
- Stock Bash remains available in every release configuration.

### 8.2 Evidence gate

- No material information-loss failure in adversarial equivalence tests.
- Typed zero results prove the searched scope.
- Every artifact declares semantics, coverage, ambiguity, omissions, and freshness.
- Build/test augmentation retains complete raw diagnostics.

### 8.3 Determinism and freshness gate

- Ten cold builds from identical recorded inputs produce byte-identical semantic artifacts.
- Incremental and full rebuilds answer identically over the certification corpus.
- Edits, additions, deletions, renames, symlinks, generated files, dirty files, configurations, graph, build, coverage, verification, and task changes invalidate the correct component.

### 8.4 Delivery gate

- Unique raw-output sentinels prove whether replacement prevents raw bytes entering the next provider request.
- Final observation and provider payload hashes bind the actual sent bytes, not a prior logical representation.
- Immediate next action is joined without post-hoc substring inference.

### 8.5 Performance gate

- Terminal owner-approved witness: independently verified reward on the single matched task.
- Default decision: retain the baseline default because one task cannot establish a confidence bound or Pareto-dominant promotion.
- Secondary: exploration actions, correct-file/symbol/edit latency, wrong-surface edits, repeated acquisition, raw bytes, verification quality, tool latency, compute cost, false interventions, stale/incomplete incidents.
- Token or exploration reductions never override a failed solve-rate or correctness gate. In the recorded witness, exploration and raw bytes increased, so no reduction claim is made.

## 9. Execution order

```text
FS-001 -> FS-002 -> FS-003 -> FS-004 -> FS-005 -> FS-006
                 \-> FS-007 -> FS-013 -> FS-014 -> FS-015
FS-002 -> FS-008
FS-002 -> FS-009 -> FS-010 -> FS-011
FS-005 -> FS-012
FS-008 + FS-007 -> FS-016
FS-002 + FS-005 -> FS-017 -> FS-021
FS-002 + FS-005 -> FS-018
FS-003 + FS-007 -> FS-019 -> FS-014
FS-005 + FS-016 -> FS-020
all producers -> FS-023 -> FS-024 -> FS-025 -> FS-026
FS-006 -> FS-022 -> FS-025
```

Work may proceed in parallel where the graph permits, but no team may change a contract independently after FS-001 is frozen. Contract changes require a schema-version decision and replay of every dependent golden test.

## 10. Project closeout criteria

GroundTruth Phase II is complete only when all conditions are true:

1. All P0-P3 items have a signed terminal completion or removal receipt.
2. All 26 FS TODOs satisfy their acceptance criteria.
3. All 17 DIRECT identities have implemented four-axis decisions.
4. The code-derived 129-row inventory has no missing, duplicate, or role-misclassified row; the stale “128” wording remains recorded as a documentation defect.
5. All 30 registry languages have terminal operation classifications.
6. The observation compiler is the sole GroundTruth model-visible delivery path.
7. Duplicate bridge, prepend, and receipt paths are removed.
8. GT-off passes byte/state parity with stock Mini-SWE.
9. Every default replacement and submit suppression passes all release gates.
10. The owner-approved single matched witness is complete and reproducible within its frozen one-task inputs; the superseded six-arm/60-run plan remains unexecuted and is not a closure requirement.
11. The release configuration and global/per-capability rollback configurations are rehearsed.
12. Repository HEADs, dirty-diff hashes, task/model/environment manifests, graph schema, runner, wheel, and native indexer binaries are frozen and recorded.
13. No capability is marked experimental, planned, pending, or unowned.
14. No provider credentials, project identifiers, account identities, or secrets enter the repository or release artifacts.
15. The final benchmark report states negative results plainly and removes failing capabilities from shipped support.

The completed execution record uses [execution_ledger.md](execution_ledger.md). A checkbox without primary evidence is not a completion receipt.
