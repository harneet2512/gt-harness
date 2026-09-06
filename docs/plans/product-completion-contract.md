# Product completion contract and drift correction

Recorded 2026-09-05 for `codex/product-completion`, based on `62b1254c`.
Status: implementation in progress; not ready for paid smoke.

Continuing Linear record (update after material changes and verification):
https://linear.app/harneet2512/document/gt-product-contract-and-drift-correction-continuing-implementation-49e1f2b67cc1

## What GT is

Groundtruth is a deterministic evidence layer between the environment and a coding
agent's policy. It supplies current repository facts, contracts, obligations,
change consequences and verification evidence in the native observation channel,
at the decision where the agent can act on them. Mini-SWE retains reasoning and
action authority. The official verifier determines task success.

The architectural chain is canonical events, repository revisions, existing state
and reasoning projections, typed evidence producers, eligibility and composition,
bounded native rendering, exact observation join, and provider delivery proof.
A source module, stored artifact, or executed producer is not by itself proof of
delivery or consumption.

Primary references:

- Current user plan: finish runtime repairs and declared capabilities, then prove
  Harbor → installed Mini-SWE → GT → real execution → patch → official verifier.
- `GT_HARNESS_SESSION_HANDOFF.md`: current harness ownership and release path.
- `D:/Groundtruth/gt_gt.md`, Part I §§1–2 and §7: evidence operating layer,
  canonical event flow, evidence selection versus delivery compilation, and
  semantic conservation. Part II and older handoffs contain historical states.
- `D:/Groundtruth/ALREADY_BUILT.md`: locate existing implementations before adding
  a supposed missing feature. Confirm reachability against the installed release.
- `docs/plans/gt_engine_repair.md`: one EngineState, typed observations, immutable
  graph/overlay, bounded context and canonical verification consumers.

Older single-winner policies, feature denominators, model routes, and baseline
instructions do not override the current user plan. No old benchmark claim is
revalidated by reading these documents.

## Mistake identified and corrected

While repairing output capture, an intermediate patch fed only an 8 KiB preview
into GT analysis and changed zero-exit large observations to unknown. This was
conservative about false GREEN but still weakened existing supported behavior:
a legitimate test summary beyond the preview could no longer certify a pass,
and a later failure could disappear from the analyzer's input.

The correction preserves complete input to the existing canonical GT analyzers.
The transport preview remains bounded and originals remain recoverable. This
restores semantics; it does **not** complete bounded-memory GT analysis. Complete
streaming analysis remains required and must be implemented through the existing
canonical producer interfaces, rather than a second harness classifier.

Executable regressions in `tests/test_output_evidence.py` cover both directions:

- A passing test summary beyond the preview reaches GT and remains PASS.
- A failure beyond the preview retains precedence over an earlier pass marker.

These tests are required alongside byte reconstruction and corruption tests.
They prevent a transport optimization from silently weakening verification.

## Rules for subsequent implementation

Before changing a boundary, identify the current installed producer, state owner,
consumer, and exact semantic behavior that must survive. Prove the required
behavior through that consumer after the change. Do not use module presence or
an unrelated fixture as a substitute.

Preserve ambiguity, dependency freshness, complete evidence and native action
semantics. A resource limit can produce a truthful incomplete record, but that
record cannot be used to declare a required supported capability complete.

`gt-evidence read` only recovers original command bytes through existing Bash.
It must not become GT's primary delivery mechanism. Current failures, obligations,
contracts and edit consequences still require automatic, timely native delivery.

## Requirements still open

Complete bounded-memory analysis and efficient integrity-checked paging; unified
state-root isolation; all 19 identities through the production admission owner;
the 15 comparison capabilities, dense execution and revision-bound LSP; the
integrated synthetic-transport Harbor rehearsal and interruption/verifier audit;
fixed-workload performance measurements; immutable release and canonical CI.
Only after these gates pass may the approved one-task smoke run. Conditional
expansion still requires a successful official result and valid release-bound
receipts. Preserve the retained Muse baseline.

## Installed integration discoveries, 2026-09-05

The continuing Linear record retains attempts 01 through 07. Attempt 06 reached
real repair and submission: the independent fixture verifier returned 1.0 and
consumed patch `409e66271a01c98790aeea096edda06eb4e61cc946e9ae3a69acfae07208a1e1`.
The overall rehearsal remained FAILED. It is a fixture inheriting the pinned
task image, not the canonical arktype task's official verifier.

Installed boundaries exposed defects that component fixtures had missed:

- Use Pier's actual Trial, exact installed adapter and filtered Docker environment.
- Resolve the actual memory-controller mount on cgroup v1/v2 hybrid hosts;
  unavailable counters remain unknown.
- Docker runner and daemon must see the same durable log paths. Different
  `/proof` mounts hid surviving artifacts from collection in attempt 05.
- The synthetic HTTP endpoint must use the production proxy's permitted port.
  Port 18383 was denied; port 80 reached the transport without proxy changes.
- Installed Mini-SWE 2.4.6 writes terminal status in `info.exit_status`. The
  auditor's top-level-only fixture concealed this incompatibility.
- Rehearsal success must require executed initial failure and final success,
  GREEN-delivered audit, Submitted, and valid runtime receipts. Exempt only the
  intentional synthetic-not-paid classification; never waive capability failures.
- Synthetic flags must survive setup and supervisor failures. Verify the bound
  report independently so removing a receipt's flag cannot conceal synthetic use.

Attempt 06's runtime validator additionally found dense readiness and graph-backed
delivery absent. These remain open requirements. The actual independent dense
ranking receipt now reaches the adapter journal; a successful producer with a
discarded receipt does not prove installed capability delivery. Attempt 07 stages
the exact hash-verified ONNX assets and includes a real source view to exercise
the eligible view consumer. Its result is pending; no gate is waived.

The pinned GT source matching the vendor wheel is
`D:/gt-har83-unified-source` at `1ecd03674f7eb6a79f401c95bf147423379d5143`.
Locate canonical implementations there before changing producers. Older
`D:/Groundtruth` source and `vendor/gt-index-src` do not represent that installed
implementation. The master architecture remains a product-intent reference.

## Latest bounded verification (superseded below)

The rebuilt harness wheel with SHA-256
`f7981c162295b33c6baecd1d0b356f359a8b516d01117abe3a2f12567be196ab`
passed 145 installed Linux tests, with zero failures, errors or skips, in
24.235 seconds on 2026-09-05. Networking was disabled; source packages were not
mounted. The test set covers output recovery and related runtime regressions,
including resolving a relative command working directory once before worker
launch. This supersedes the earlier 144-test candidate proof, retained in Linear.

Local wheel and JUnit evidence reside in `D:/gt-product-proof`. Implementation
is uncommitted work in `D:/gt-product-completion`, branch
`codex/product-completion`, based on `62b1254c`. These are continuation pointers,
not an immutable release identity. No paid calls were made. The full installed
Harbor/verifier rehearsal and all other open requirements above remain required.

## Installed continuation through attempt 09

Attempt 09 passed the stricter synthetic fixture rehearsal with harness wheel
`92cb64600e8281acf7ed24be9fd24135ef9d0287bfe384fbfa27fc833c836a9c`.
It used actual graph construction, ONNX inference, commands, repair, collection,
and the independent fixture verifier through Pier's production installer and
adapter. The verifier consumed the exact exported patch. The runtime validator
returned only `synthetic_transport_not_paid_evidence`, as required. Attempt 08
passed an earlier, weaker graph-certificate check and is retained as superseded.

Installed regression-03: 448 passed, zero failures/errors/skips, 119.022 seconds.
Tests ran against installed wheels with networking disabled and no source-package
mounts. The retained arktype graph was supplied explicitly; its digest is
`204aa77800224e03f7810344afe292b164c318a569a2c8516a4fd86376cfec16`.
This is scoped regression evidence, not a baseline/candidate performance study.

The fixture exposed another gap after its repair proof passed: the harness
discarded `python3 -m unittest` and canonical GT missed `python3 -B -m unittest`.
Consequently a passing transcript did not prove structured test evidence reached
GT. Canonical source repair `64e8585957523db32ee2deb76a4e385fb4db4b7b` lives in
`D:/gt-product-source`, a separate worktree descended from the exact pinned source.
The harness now consumes canonical test classification. Interpreter switches are
case-sensitive, and nonexecuting version/help/code-string forms are excluded.
Keep this rule: assert the actual consumer chain, not just the command result.

The rebuilt GT wheel matches all 317 packaged files in the commit archive; its
digest is `400eb8322d70b5cf939265c9273f92ebafa2869b90ccbfb0cc0042867cde6d54`.
This changes the required installed artifacts. Attempt 10 must verify initial
FAIL and final PASS evidence blobs, raw bytes, immediate request consumption, and
different source revisions. Rebuilding and repinning the producer is in progress.

Still open: original arktype verifier rehearsal, all 19 identities and all 15
comparison capabilities at their declared fidelity, complete bounded context and
analysis, remaining state/publication gaps, interruption and corruption proofs,
offline performance, immutable release, and green canonical CI. No paid run has
occurred. Keep the continuing Linear HAR-83 document updated after each material
finding, repair, proof, or limitation.

## Continuing implementation pointer

The attempt numbers above are historical. The current queue is
[`product-completion-todos.md`](product-completion-todos.md), and the linked
continuing HAR-83 Linear record contains subsequent installed attempts and
review corrections. Candidate 18 passed its affected installed checks and the
real ONNX cache test. Candidate 19 repaired the fixture but failed the collected
localization audit because absolute runtime evidence roots were not relocatable.
That failure is retained; its root repair and subsequent streaming/LSP/dependency
changes still require rebuilt installed evidence. No whole-product readiness
or paid-smoke success is claimed.

When reviewing delegated work, check its actual canonical API, primary consumer,
and collected artifact path. Do not invent a receipt schema, certify an unknown
derivation, discard alternative candidate evidence, or infer complete dependencies
from lexical scopes. A passing component fixture cannot replace the installed
collection boundary. Keep findings and invalidated proofs in Linear as they occur.
