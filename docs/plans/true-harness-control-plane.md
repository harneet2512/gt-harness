# GT Context Engine on the Mini-SWE Harness — Redesign

Status: active implementation program. Supersedes the incremental
delivery-render fixes (which remain landed and valid — the redesign changes
*when* payloads render, not *what* they render).

Origin: smoke-20 (run 34715686102) artifacts + the standing defect list.

## Layering — the symbiote model (the frame this plan is written in)

GT is Venom; Mini-SWE is Eddie Brock.

- **Unbonded**: Mini-SWE does everything normally — full loop, tools,
  history, submit. GT-off is bit-identical Mini-SWE; the bond is reversible
  and non-destructive (`session.disabled` + `restore()` is the detach
  contract — provably transparent, zero residue).
- **Bonded**: Mini-SWE keeps the body; GT's senses become the operative
  perception. Context, evidence, verification state, plans, and steering
  flow through the host's actions — GT's output is the *main thing* at
  every decision boundary, not an advisory channel.

Rules of the bond — every known defect is one of these broken:

1. **Never impede the host.** Advisory work must not stall the agent loop
   (boa: 96 min before the first provider call).
2. **Never poison the host.** Only re-derivable, verifiable bytes enter
   model-visible context (fabricated plan prose; internal digests the
   agent fed to git).
3. **Enhance, don't abandon.** Correction channels exhaust before
   termination (churn_abort with steers_issued=0).
4. **Don't charge the host for symbiote metabolism.** GT's own writes and
   subprocesses can never bill to the agent (phantom-edit precedent).
5. **Never stale senses.** The graph cannot be stale because staleness
   cannot be constructed (942 frozen-source refresh failures).

Non-consumption of GT output is a context defect until proven otherwise —
the bar is that GT perception is strictly more actionable than the host's
own senses.

## The incident data

| Signal | Count | Where |
|---|---|---|
| `graph_refresh_failed` | **942** | abs-stepped-slices 532, abs-module-cache-flags 410 — all `frozen_source_incomplete:missing=1` on `docs/src/.vuepress/dist/assets/js` |
| `graph_rebuild_embedding` ValueError | 109 | embedding rebuild path |
| `semantic_localization_unavailable` | 3 | `ambiguous_stable_identity` (katex, testem-bail, testem-per-launcher) |
| `provider_failure` FormatError | 5 | model output parse |
| `gt_degraded_fail_open` | 2 | secret-detector — fixed `e3cd9475` |
| Check observations | 1,060 | 812 UNVERIFIED / 217 CHECK_FAILED / **31 CHECK_PASSED** |
| Churn abort | 1 | claude-code: `stall_turns=50, steers_issued=0` — aborted with zero steering on a baseline-4/4 task |
| Plan fabrication | 1 confirmed | plan claimed a tool loop in `chat.ts` that was `workerAgents.map(a => a.id)`; agent chased it, tried GT digests as git objects |
| Old-turn evictions | **0 / 3,642** context-assembly events | full-history replay, 95.4% cache-read |
| `predicate_compiled_count` | 0 / 20 | Mini-SWE path never journaled (fixed) |

## The invariant

> The graph cannot be stale because staleness cannot be constructed.

Every mutation flows through `execute_actions` → before/after snapshot →
transaction. The seam is already total — nothing writes to the worktree
except through the intercepted tool call. That makes synchronous indexing
*possible*, not aspirational:

- **Write path**: `record_edit_transaction` → after `apply_transaction`,
  synchronously reparse each changed file from the transaction's own
  after-bytes (no filesystem read — bytes are in the overlay), patch
  nodes/edges, set `graph_revision = post_revision`. Typical edits (1–3
  files): tree-sitter reparse + edge patch — milliseconds. The observation
  that returns to the agent already carries post-edit evidence.
- **Delivery path**: the pending queue stores the *query* (what to deliver),
  not the rendered payload. Payload renders at the admission choke point
  from the current graph — it cannot be stale because it does not exist
  until it is delivered. `localization_source_revision_mismatch` becomes
  unrepresentable.
- **Enrichment**: per-edit, scoped to the delta's blast radius; whole-graph
  products (initial dense index, full LSP promotion) run during provider
  wait — `attach_provider_boundary` already marks when the agent is blocked
  on the network. Apply at next loop boundary, scoped to paths still clean.

## Phases

### Phase 0 — live bugs (investigate regardless of redesign)

1. `frozen_source_incomplete:missing=1` ×942 — one unreadable path
   (`docs/src/.vuepress/dist/assets/js`) poisons every abs-repo refresh
   forever. Root-cause whether the frozen list holds a directory/broken
   symlink/deleted file and why the same entry recurs deterministically.
2. `ambiguous_stable_identity` — embedding-input projection raises on
   duplicate `gtsym1:` ids (3 tasks) + 109 `graph_rebuild_embedding`
   ValueErrors. Correctness bug *and* Phase-1 constraint: delta indexing
   keys on the same identity, so collision handling must be deterministic
   (collapse/dedupe), never raise.
3. Governor: `steers_issued=0` then `churn_abort` at 50 turns. Pure reads
   don't reset the stall counter (correct — a read loop IS a stall) but
   abort must require ≥1 issued steer. Policy ordering bug.
4. Plan fabrication: the planning-model call generates architecture prose
   validated only for "anchor exists." Contract violation — graph facts
   laundered into unverified claims. Also: internal 64-char digests render
   model-facing and the agent fed them to `git`.

### Phase 1 — index on the write path

- `record_edit_transaction` → synchronous reparse of changed files from
  transaction after-bytes → patch nodes/edges → `graph_revision = post`.
- **Edge blast radius**: reparse yields the edited file's own nodes and
  outgoing edges; *incoming* edges (unchanged callers of a renamed symbol)
  need symbol-table re-resolution over the dirty set. Node-only patching
  produces a current-looking, wrong graph — this is where correctness lives.
- **Stable identity**: per-file patch must dedupe `gtsym1:` collisions
  deterministically (the finding-4 bug lives exactly here).
- **Mass mutations** (formatter over 500 files, `git checkout`): block the
  tool call on a bounded resync of the dirty set.
- **Undiffable/incomplete transactions**: decided (a) — block-and-rebuild
  inline with file-count AND wall-clock bounds; escalate to typed
  `graph_resync_incomplete` failure only when the dirty set is unknowable.
- **Post-return drift**: daemons/`&`-writers mutate after the post-image;
  the next action's pre-image diverging from the last post-image IS the
  untracked-mutation signal — feed the same resync path.
- **Open design fork — where the parser lives**: nodes/edges come from the
  Go `gt-index` 4-pass whole-repo build. Per-file patching needs either
  (a) a `gt-index --files <list>` incremental mode (preferred — preserves
  ONE parser/pipeline), or (b) a Python reparse duplicating specs (violates
  the one-pipeline rule — reject). Phase 1 starts by proving (a) is feasible.
- Initial build stays a task-start gate — synchronous, fail-closed. The one
  place "wait for the harness" is legitimate.

### Phase 2 — compute-at-delivery

- Pending queue stores queries (`{kind, params, budget}`), not payloads.
- Render at the admission choke point from current graph; dedup on content
  head (same approach as the landed localization fix).
- Deletes: staleness refusals, revision-mismatch refusal reasons,
  compute-revision pinning on deliveries.
- Enables drift re-localization: agent search actions feed the query store;
  a re-rank at admission costs nothing extra since nothing is pinned.

### Phase 3 — delta-scoped enrichment

- Per-edit: LSP promotion re-derives the delta's blast radius; embeddings
  update for touched files only.
- Whole-graph products run during provider wait; applied at next loop
  boundary scoped to paths still clean (bounded by one tool call, masked
  by the overlay).
- `MAX_RUNTIME_EMBED_DOCUMENTS` (landed) remains the query-path bound; the
  goal is for it never to fire.

### Phase 4 — delete the sidecar

- Dead code: `GraphBuildCoordinator` (schedule/coalesce/poll/adopt),
  `publish_graph` CAS, `FrozenBuildInput` re-freezing, reclaim protected-set,
  staleness refusals, `graph_current` as serving precondition.
- **Stays**: revision *identity* — receipts/attestation bind evidence to
  `graph_revision`. The deletion is concurrency control, not provenance.

### Phase 5 — proof

- Invariant test: hammer edits faster than any async build could run;
  assert at every observation `graph_revision == source_revision`, zero
  staleness refusals, zero discards. Impossible on the current design —
  that is the point.
- Replay arktype trajectory: total index time 82.4min of rebuilds →
  seconds of deltas.
- Provider-free acceptance → smoke-10 → paid smoke-20 (separate approval).

## Orthogonal fixes (independent of the redesign)

- **Plan governance (finding 1)**: verifiable-only persistent plan —
  requirement rows, verified anchors, signatures, extracted checks.
  Generative prose returns only behind a per-claim verifier. Strip internal
  digests from model-facing plan output (audit blob keeps them).
- **Checks v2 (finding 5)**: `cargo test -p <pkg>` needs package→manifest-dir
  resolution (package name ≠ directory); unbound specs must stay *pending*
  (retry-able), not discarded — "bound before test file exists" resolves
  when the file lands.
- **Context growth (finding 6)**: full-history replay is cheap in dollars
  (95.4% cache-read) but persists detours in model attention. Prefix
  truncation would destroy the cache and cost MORE. Sidecar-feasible fix:
  collapse superseded `[GT_EVIDENCE:*]` blocks to one-line pointers at
  prepare time — tag-addressed, deterministic, agent turns untouched.
- **Metrics (finding 7)**: dedupe task dirs in metrics.json (22 rows/20
  tasks); fix GT-byte double-count (prep+delivery); teach evaluator
  `request_manifest`; consumption matching requires distinctive payload
  terms (qualified names, exact anchors), not common words — my earlier
  consumption stats were overstated on exactly this.

## Workstream map

| WS | Contents | Files | Parallel-safe? |
|---|---|---|---|
| A | Phase-0 investigations ×4 | read-only | yes — explore agents |
| B | Phases 1–4 (architecture surgery) | miniswe_integration, indexer, graph_coordinator, gt_session | **no — sequential** |
| C1 | checks v2 | persistent_plan/checks.py + tests | yes |
| C2 | metrics/audit fixes | scripts/gt_audit.py, evaluators | yes |
| C3 | GT-block supersession at prepare | miniswe_runtime.py | yes (after B lands) |
| C4 | plan render: verifiable-only + digest strip | persistent_plan renderers | yes |
| D | proof suite | tests/, replay scripts | after B |

Already landed this session (survives the redesign): localization/catalog/
execution-evidence payload renders, check-channel binding+rebind+GREEN
receipts, content-keyed re-localization (cap 3), obligation-delta
transitions, predicate-compiled journaling, evidence persistence into
agent history, dense-embed query bound, audit eligibility+predicate counts.
