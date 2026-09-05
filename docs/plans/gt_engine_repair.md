# GT engine repair

Status: implementation in progress; not benchmark-ready.
Reviewed starting harness: `9010199412dd1cb4fb5cd60e9ebd63000cc2132f`.

## Product contract

GT is the deterministic context engine inside Mini-SWE's reasoning/action loop.
It must supply supported, current, relevant, compact evidence. Deterministic
metadata is not a substitute for meaningful facts. Mini-SWE owns reasoning,
commands, edits and normal completion; the official verifier owns reward.

Empty or failed optional GT must preserve native action order/count/arguments,
observations, tool-call IDs, accounting, completion and final workspace. Required
product identity errors fail before provider use. Optional runtime failures must
not turn into startup refusal or erase successful native work.

Computation, additional command execution and submission enforcement are three
independent policies. Automatic computation is on; autonomous checks and submission
veto are off unless explicitly configured. Unknown is never PASS/current/proven.

Work in the canonical harness and the manifest-bound producer source. Never edit
an extracted wheel as source. Preserve unrelated work and historical receipts.
No cloud/credential changes, provider calls, paid dispatch or GT-off rerun. Clean
Linux installation and explicitly approved comparison follow engine repair.

## State and interfaces

One EngineState owned by GTSession is the authority for source revision, workspace
epoch, immutable graph handle, overlay, claim dependencies, verification, delivery
and capability health. Adapters delegate; persistent bindings are snapshots, not
competing authorities. Keep source/graph/overlay revisions, graph artifact SHA and
policy identity distinct. Git HEAD is not a working-tree revision.

Interfaces to consolidate:

- observe_action -> typed views, changes, execution evidence and incompleteness.
- query_context -> current-complete/current-partial/historical/pending/unavailable.
- produce_candidates -> claims without delivery side effects or hidden execution.
- prepare_context -> validate, rank, deduplicate, structurally compact and budget.
- finalize_request -> actual provider request including tools/output reservation.
- record_transport_attempt -> exact bytes, attempt and later response binding.
- schedule_refresh/poll_refresh -> nonblocking source-bound build coordination.

Claims carry substantive typed payload, feature/status, source ranges and digests,
complete/incomplete dependencies, source/graph/overlay identity, provenance refs,
decision boundary, supersession key and renderer/policy identity. Confidence is
supplementary, never proof. Facts, hypotheses and unknowns remain distinct.

## Ordered work packages

### P0: regression truth set

Pin harness/producer/wheel/Mini-SWE/assets/policy and assert actual import locations.
Add RED witnesses for dirty-path loss, stale build publication, lost claim payload,
pre-admission latches, oversized localization, new-file early return, stale covering,
pipeline/no-tests false pass, same-epoch recovery, lexical-restricted dense search,
wrong encoder recipe and missing embedding refresh. Replace obsolete fail-fast
source matching with actual Call AST matching plus startup refusal/no-provider proof.
Preserve working sed/head/nl/multifile and compound semantic-event tests.

### P1: Mini-SWE preservation

Compare stock pinned Mini-SWE, harness GT-off, and empty/failed GT with identical
scripted responses and real temporary workspaces. Assert actions, outputs, IDs,
accounting, completion, workspace and patch. Wrap native execution where possible;
do not replay actions after GT errors or erase earlier batch observations. Invalid
GT tools never become shell. Disabled GT tools disappear from later schemas; an
already-issued call receives typed unavailable. Restore/bypass hooks coherently
without removing neutral credential/accounting guards. Do not compact native
history to create room for GT. Preserve actual submission-marker behavior, not
substring guesses. Export patches on normal/catchable termination without changing
the real Git index (use an isolated index); preserve pre-existing staged changes.

### P2: truthful observations

Preserve all semantic events per action. Record exact viewed ranges only when
proven; unsupported shell scope is unknown. Bind create/delete/rename/script edits
to actual before/after bytes. Missing/oversized bytes and parser failures are
incomplete. Feed transaction artifacts into context, not only storage.

Reuse the producer's five-language parser through a pure JSONL inspection mode:
request ID/language/relative path/content SHA/bytes -> parser identity/completeness/
declarations/signatures/ranges/diagnostics. No graph mutation, repository execution,
or fallback to different disk bytes. Type malformed/cancelled requests. Compare
before/after declarations across Python, Go, TS, JS, Rust, including body-only edits.

Measure inventory/hash/parse/write costs separately. Slow reconciliation stays off
the critical path; budget expiry means incomplete, not invented observations. Later
current-state reconciliation cannot fabricate historical transactions. Metadata is
only a change hint. Reuse captured bytes by content digest.

### P3: immutable graph and overlay

Published graph is read-only. Overlay records operations, before/after identities,
parsed declarations and invalidations; mask deleted/superseded entities. Reuse only
claims with complete currently valid dependencies. Inventory/scope/import/export
changes invalidate affected resolution and absence claims.

Use one in-flight build, one latest pending request, unresolved dirty-path union,
frozen source input and owner-thread publication. Default incremental capability
off until full dependency closure is certified. Build only when demanded facts
cannot be answered soundly by base plus overlay, not every read/edit.

Publication requires healthy artifact, matching producer/schema/input identity,
complete input and requested source still current. Old results cannot clear newer
changes. Every graph consumer (covering, localization, contracts, tools, LSP) uses
common query validation. Test overlapping edits/builds, failures and blocked worker
without blocking native actions.

### P4: context meaning and admission

Write context packet v2 preserving/validating payload and source references. Bind
identity to actual contents/provenance/render policy. Reject invalid refs, missing
payload and conflicting freshness. Budget the entire serialization including
digest/wrappers. Send exactly router-admitted representation. Historical v1 remains
read-only; do not upgrade its claims retroactively.

Collect candidates before selecting (remove producer early returns). Validate,
deduplicate/supersede, rank, structurally compact, admit, then record transport.
Priority is current relevant executed failure; proven contract/signature/definition;
current localization/check selection; relevant explicit issue requirements;
supported precedent; historical hypothesis. Unrelated RED cannot win by severity
alone. Tie-break relevance/specificity/novelty/size/stable identity deterministically.

Initial policy: <=4 new claims/boundary, <=9600 UTF-8 GT-owned bytes/request subject
to provider budget, normal fact <=1400 bytes, initial contract <=2000 bytes,
co-change <=480 bytes/request and <=2 distinct hints/task. No shared lifetime cap
that lets weak early evidence starve later facts. Use existing multidose facility
where suitable. Structural shortening preserves fact/status/location/evidence/
limitation; drop optional examples or lower-ranked claims, never arbitrary slices.

Track produced/prepared/attempted/sent/response-bound separately. Rejection consumes
no delivery quota or shipped latch. Retry localization/contract when still useful.
Transport errors do not imply response; retries reuse immutable packet identity.

### P5: provider budget and history

Budget actual final messages/tools/GT/output reservation using existing route
configuration and validated counter or conservative bound. Unknown GT capacity
means no GT addition. Native overflow follows the same neutral policy in on/off;
do not destroy native history. Store message/output/provenance bodies once in CAS;
requests are ordered refs plus parameter/schema/packet identity. Atomically publish
manifests and verify reconstruction. Preserve old audit readers. Report unique vs
referenced bytes, new vs replayed GT bytes and real input/cache/output separately.

### P6: semantic retrieval/cache

Pinned Snowflake encoder uses CLS pooling, query-only retrieval prefix, max length
512 and L2 normalization. Recipe participates in vector identity; invalidate old
mean-pooled vectors. Cache verified assets/tokenizer/session per process.

Embed primary source/contracts, not candidate/provenance rows. Search complete
eligible dense corpus independently of lexical/property matches and first-256 IDs.
Use chunked exact cosine search initially and deterministic RRF (existing constant).
Bind vectors to model/recipe/content/schema/dimension/unambiguous stable ID. Publish
vectors+bindings transactionally. Reuse unchanged content across graphs but resolve
locations against current snapshot. Call actual ContractEmbeddingStore.refresh;
surface failure. Test no-overlap semantic recall, beyond-256 recall, warm zero-doc
embedding, single-content invalidation, ambiguous IDs and explicit lexical-only mode.

### P7: verification and recovery

One outcome authority: PASS/FAIL/NO_TESTS/ENVIRONMENT_ERROR/TIMEOUT/INTERRUPTED/UNKNOWN.
Shell zero alone does not prove a test; pipeline tails and copied logs are not
success. Prefer existing structured reports; ambiguous compounds remain unknown.
Do not change user commands to simplify classification. Parser != compiler != test.

Select checks from current failure identity, proven coverage/dependency, explicit
reproduction, repository conventions, then labeled broad fallback. Return command,
cwd, reason, covered scope, cost/prerequisites. Path mention alone is not causality.
Mini-SWE executes normal selected commands; optional bounded probes never install
dependencies or use network/paid services. Explicit requirements retain issue spans;
inferences are suggestions. Checks clear only established obligations. Relevant
edits invalidate results. Recovery-after-edit requires newer relevant epoch; same
epoch repetition is separately labeled no-information, never proven falsification.

### P8: producer size without truth loss

Measure table/index/repeated-field sizes. First intern candidate sets/provenance/
receiver/import chains losslessly; separate source nodes from bookkeeping. Update
actual readers/versioned views and rebuild immutable artifacts, no in-place migration.
Then narrow only with complete scope/import/type/visibility/signature evidence.
Preserve ambiguity and incomplete parsing/dynamic dispatch; never top-N cap stored
truth. Presentation may be bounded independently. Compare query semantics before/
after. Target >=50% storage reduction on identical large input; missing input or
missed target remains an open gate, not a tiny-fixture substitute.

### P9: LSP/derived layers

Graph/source-bound job identity replaces process-global scheduling. Own worker
lifecycle from synchronous entrypoints. Cancel/discard superseded work. LSP uses
correct snapshot/config and emits proposals/typed deltas, never mutates immutable
base edges. Empty/failed response cannot prove absence. Record server/query/source/
response/interpretation. One canonical community index; don't compute incompatible
parallel copies. Missing language services are unavailable, not passed.

### P10: native wiring and feature proof

All producers use common state/candidate/admission paths. Offer versioned catalog
IDs at task start, validate native tool selections and bind to action focus without
requiring selection. Invalid ID does not alter ordinary Mini-SWE actions.

19 identities: caller_contract, cochange_prior, covering_red, def_partition,
localization, newfile_precedent, obligations, recovery, signature_delta,
submit_refusal, syntax_result, GT_CERT_DELIVERY, GT_CHANGE_SURFACE, GT_EDIT_CHECK,
GT_HYPOTHESIS, GT_LOC_RESLOT, GT_PATCH_DELTA, GT_SS_SUBMIT_RED, select_catalog.

Distinguish implemented/reachable/exercised/admitted/sent/behaviorally relevant.
Capability execution != fact delivery. Native witness includes artifact/policy,
scenario, producer/result, admission and exact provider binding where applicable.
Each identity needs positive and negative witnesses in suitable policy; not every
task triggers every feature. Zero required witnesses must fail verification.

## Acceptance and final review

Add `python -m scripts.gt_engine_acceptance --suite` with baseline/context/state/
retrieval/features/performance/all. Use pinned native Mini-SWE, scripted external
model, real files/parser and exact requests. Missing required runtime assets is
INCOMPLETE, not a passing skip. Six families per five languages: signature callers,
def/ref ambiguity, no-overlap localization, new-file convention, current vs stale
failure, compound action. Use synthetic independent fixtures, no hidden gold data.

Zero unsupported facts/false pass/false freshness/false causality in adversarial
corpus; every fact's evidence resolves; semantic gold top5; hypotheses never
displace stronger relevant facts. Stable packets under reordered input; numeric
inference tolerance explicit, stable ID tie-breaking. Structural compression keeps
meaning. No universal solve-rate claim from fixtures.

Measure declared cold/warm workload: target warm boundary p95 <=250ms; optional slow
work cannot hold native actions; <=1 build +1 pending; zero warm document inference,
per-query model initialization/model hashing/graph hashing; unique CAS bodies;
no unchanged claim redelivery without reason. Report cold build/RSS/storage too.

Execute P0-P10 in order, rerun graph/retrieval/provenance after P8 packaging. Apply
`review_me_gt.md` independently. Engine acceptance is not benchmark readiness;
clean installed Linux E2E and separately authorized smoke/comparison remain gates.
