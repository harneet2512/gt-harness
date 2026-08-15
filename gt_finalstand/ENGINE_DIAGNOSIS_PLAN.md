# GT ENGINE — Deep Diagnosis + Fix Plan (round-6 readiness)

Goal: GT must deliver each of the 17 features as **usable, deduplicated,
relevance-gated, fresh** facts at the correct time — and we must *prove* it
with a per-feature diagnosis (mocks/stubs) before another smoke.

## Phase 0 — Diagnosis: why the 17 aren't delivering usable content

**0a. Per-feature firing matrix (mocks + stubs).** New `scripts/engine_17_diagnosis.py`
exercises every FACT producer with controlled stubs (fake adapter / TaskContract /
GatewayState) and reports per feature: **fired? content usable? anchors?
dedup key? why-not?** This is the "breakdown of all 17" the round-5 data
couldn't show (we only saw 4 features fire, mostly with unusable content).

**0b. Round-5 content autopsy** (done, `D:\tmp\opencode\spam_analysis.py`):
| feature | total | content usable? | diagnosis |
|---|---|---|---|
| obligations | 242 | NO — opaque hash IDs, empty text | `render_obligation_delta` returns "" for long rows; matcher too aggressive (1-2 token overlap) |
| covering_red | 1 | partial — command echo, no source frame parsed | fires only on RED with a source traceback frame |
| def_partition | 2 | usable (exact search + byte offsets) | fires only when the model calls the typed tool |
| recovery | 3 | echo (targets the file the model already edits) | adapter failure machinery; confounded |
| localization / signature_delta / newfile_precedent | 0 | — | **graph-gated producers abstained** |

**0c. Graph freshness diagnosis.** `localization/signature_delta/newfile_precedent`
need the graph DB. Root cause candidates: (1) `graph_db` not populated or
`graph_fresh` False in the container; (2) the gateway's graph-backed producers
need `graph_revision`/`episode_overlay`; (3) `refresh_graph` may be a full
rebuild that never runs. Verify with a stub.

## Phase 1 — Dedup: fire-once per episode (the top fix)

The gateway has the mechanism (`seal_delivery` / `dedup_chain` /
`state.delivered_keys`) but the engine only stamps the single gateway winner.
**`_obligations_fact` and the engine-direct producers never dedup** — so the
same obligation re-fires every matching action (242×).

Fix: one unified per-episode dedup registry (`adapter._dedup_chain`, a set)
checked by every engine fact producer with a stable key `owner + target/anchor +
content-hash`. A fact already delivered this episode is **suppressed** (fire-once)
or held with a small cooldown. Gate: run the same action twice → fact delivered
once.

## Phase 2 — Firing selectivity (relevance, not token overlap)

`matching_obligation_ids` fires on 1-2 significant-token overlap → matches
nearly every action. Fix: require the action to reference the obligation's
**subject/file** (or ≥3 distinctive tokens), so obligations only fire when the
model is genuinely working on that requirement. Cap facts per observation
(single-dose already) and per episode (Phase 1). The gateway `arbitrate` +
`recently_delivered` already rotates; ensure the engine routes everything
through it.

## Phase 3 — Payload fix for all 17 (usable content, verified per feature)

- **obligations**: real requirement text + subject anchors (landed `b215a9f`,
  gate-verified; needs live confirmation).
- **covering_red**: source target + failing assertion lines + test-file identity.
- **recovery**: exact normalized failure identity + the failed command/output
  (not just the target file).
- **def_partition / localization / signature_delta / newfile_precedent**:
  usable only when the graph is fresh (Phase 4) — the gateway already renders
  them; verify each with the diagnosis harness.
- The diagnosis (Phase 0) drives which content is unusable → fix each.

## Phase 4 — Graph incremental freshness (no full rebuild, latest info)

- Diagnose why graph-backed = 0 (Phase 0c).
- `refresh_graph()` — check full vs incremental; if full, implement
  **incremental**: on an edit, re-index only the changed files' nodes/edges,
  update `graph_revision`, mark `graph_fresh`, and keep `episode_overlay`
  current. Graph-backed facts then fire with the LATEST info without a repo-wide
  rebuild.

## Phase 5 — Frontier-lab research + gates

Research (Anthropic context engineering, OpenAI agent design) on dedup /
selectivity / minimal context / freshness — recorded in the ledger. Gates that
would have caught every round-5 failure:
- dedup fire-once (repeated action → one fact),
- selectivity (unrelated action → no obligation fact),
- payload usability (no opaque IDs; real text + anchors),
- graph freshness (post-edit graph-backed producer fires with the new revision),
- 17-feature diagnosis harness (each producer exercised with stubs).

## Phase 6 — Round-6 smoke (the proof)

After 0-5 + provider-free green: dispatch round-6, measure per feature:
**distinct** facts (not spam), L2/L3, rewards, delta. Decision rule unchanged:
high-gain facts reaching L3 = causal; else iterate.
