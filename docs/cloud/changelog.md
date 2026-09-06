# Changelog

Every commit on `cloud/internal-harness` that is not on `origin/main`, newest
first, grouped by day. Generated from:

```
git log --format='%h %ad %s' --date=short cloud/internal-harness ^origin/main
```

31 commits, 2026-09-04 to 2026-09-05.

---

## 2026-09-05 — sandboxing, the audit, budgets, workers, prompt-first

| Commit | Change |
|---|---|
| `9c394863` | **Worker coding agents — spawn, report, apply.** A worker is a child session with its own workspace, sandbox and transcript; all-or-nothing spawning against the concurrency caps; reports mirrored into the parent's conversation; `git apply --3way` into an idle parent with conflict paths on 409. Schema v6. 306 passed / 4 skipped. |
| `54532f86` | **Prompt-first entry.** The landing configuration form becomes one composer; the repository is inferred from a GitHub URL in the message or the most recent session; model, GT mode and budgets move behind a gear and persist locally. Sending creates the session, navigates, and posts the message the moment it is idle. Slash commands and keyboard shortcuts. 169 Vitest tests. |
| `15f845a0` | **Close the audit gaps (server/ops).** P0: compose restart policy and healthchecks, the `gt_mode` `Literal`, `--init` plus recreate-and-retry for wedged sandboxes. P1: turn-scoped failures, `ensure_running` per turn, provider envelopes never stored as replies, 40-hex refs via fetch/checkout, the disk floor and workspace cap, interrupted turns closed on the wire, `turn_started.content`, per-request `ALLOWED_GITHUB_LOGINS`, a 24 h JWT TTL, the model preflight. P2: G-12 … G-23. Deferred: G-14, G-18. |
| `91f6f779` | **UI side of the audit gaps.** The prompt rendered from `turn_started.content` for any subscriber; interrupted turns closed on screen; the GT picker corrected to `off / advisory / assistive / enforced`; `lifecycle failed` surfaced; `Stopping…`; `after_id` floored; empty `tool_call` frames dropped; honest notes for exit codes 137/143/124/128. 147 Vitest tests. |
| `4ebf8dbe` | **HAR-86: exclude the harness state dir from GT's working-tree snapshot.** `_snapshot_authority` hashed `.gt_state/`, which GT writes during a turn, so every mid-turn typed action reported `repository_revision_mismatch`. The only `gt_engine/**` change on the branch, deliberately its own commit. |
| `24f9e0fb` | **Idle-session TTL reaper and per-turn wall-clock budget.** Sessions idle past `SESSION_IDLE_TTL_SECONDS` are closed exactly like `/close` with `closed_reason: "expired"` (schema v5); `TURN_WALL_SECONDS` with a watchdog that interrupts the command in flight, `finish_reason: "time_limit"`, and `wall_seconds` on the receipt. |
| `09b5e79a` | **Narrow-screen layout, expired/time-limit states, Vitest in CI.** Drawer and slide-over below 1100 px, stacked below 760 px; `closed_reason`, `time_limit`, `total_wall_seconds`, per-receipt `wall_seconds`, the cost column labelled *untracked*, and the persisted `gt_error` after a reload. 120 tests over the pure layers, wired into CI between typecheck and build. |
| `11369434` | Repair the producer build step's line continuation (a literal `\n` where a continuation belonged). |
| `b131fa03` | Resolve the producer variant stamp inside the build step, so a blanked `ARG` cannot stamp an empty variant. |
| `4e610350` | **The cloud producer patch becomes upstream PR #6 (`cloud.2`).** cloud.1 only skipped in the final insert loop, so an abstained candidate still wrote its `CANDIDATE_TARGET` edge, `DerivationFact` node and flow facts. The variant stamp moves to `cloud/producer/PRODUCER_VARIANT`. |
| `00b0d43f` | Document that Codespaces port visibility resets on every deploy; `deploy.sh` says so at the end. |
| `fa6a4a23` | **Tool frames survive GT's hook; stop interrupts commands; deploys stamp their commit.** Emission moves into `_EmittingEnvironment`, which every `execute_actions` shares (GT's replacement included) — 0/0 persisted frames became 23/23 on the codespace. `InterruptGuard` kills the command in flight, taking `/stop` from 18.5 s to 0.16 s. `cloud/deploy.sh` and `BUILD_SHA` in `/health` and the bundle. `python -c` added to the write regex. |
| `8ce95004` | **Round-2 QA.** Reload no longer re-relaxes the restored graph layout (0.00 px drift, measured); the `is_reply` assistant frame is counted but not rendered twice; sandbox lifecycle phases are named instead of falling through to *Preparing…*. |
| `80be612b` | `prepare_workspace` promised `a+rwX` but only added `rw`, so uid 1000 could not run the repository's own scripts. Caught by the POSIX-only test on the Linux CI runner. |
| `1d08976a` | **Per-step diff snapshots, persisted GT error, the reply frame; the sandbox doc.** `/diff?through_event=N` returns a real stored snapshot (512 KB cap, 2 s budget, `diff_snapshots_disabled`); `Session.gt_error` (schema v4); the `is_reply` assistant frame; `docs/cloud-sandbox.md` and `docs/upstream-groundtruth-issue.md`. |
| `a64fa592` | **Per-session sandbox containers with an allow-listed egress proxy.** `DockerSandboxEnvironment`, the container lifecycle, `--cap-drop ALL` / `no-new-privileges` / 2 GB / 2 CPUs / 512 pids / tmpfs `/tmp`, the internal network, the stdlib CONNECT/HTTP proxy, the docker socket into the server, bind-mount path equality, the sandbox image build profile, and `verify.sh`. Verified on the codespace in both GT modes. 42 sandbox tests. |

## 2026-09-04 — the service, the chat rebuild, GroundTruth, the graph, the UI

| Commit | Change |
|---|---|
| `87892150` | Actually escape the control characters in `graph.ts` (the previous commit claimed it; its script failed before writing). |
| `ceed00b9` | **Stable graph layout across turns, honest GT status, diff replay, empty-state CTA.** A field signature so an unchanged graph never restarts the simulation (0.0 px drift measured); positions and camera persisted per session; one definition of *step* = one model call; `gt_unavailable` surfaced as a dismissible notice; the scrubber's approximate replay; `docs/cloud-vm-substrate.md` gains "What we actually did". |
| `807f6510` | **Browser-QA fixes.** A stale refetch could resolve last and wedge the header on *Working* — responses are now applied in issue order; `close()` had no control; graph labels saturated; the fixed-width inspector crushed the layout; `/auth/me` accepts the same credentials as every `/api` route; `forwardPorts` declared in the devcontainer. |
| `72b63291` | **HAR-85: make glob scopes concrete before GT's literal search.** `cloud/server/typed_scopes.py`, applied in a `GroundTruthLitellmModel` subclass at `_parse_actions` so `gt_engine` and the benchmark path stay untouched. Verified on the cloud: the same query returned exact/complete with 2 matches. 14 tests. |
| `f6c393d0` | CI on Python 3.12 only — the package requires >=3.12, so the 3.11 leg failed at install on every push and `fail-fast` cancelled 3.12 before any test ran. |
| `f4329e44` | **Build the GT producer from source so arbitrary repos index.** The certified producer aborts the whole graph on one derivation-invalid candidate; `pallets/click` has 27. The image fetches the pinned commit, applies the patch and builds with CI's recipe, stamping `+cloud.1`. `click` → ready, 62 839 nodes / 79 216 edges. GT's `exact_literal_search` then abstained — filed as HAR-85. |
| `0426bd7a` | Publish the UI on port 80 so the forwarded URL matches the OAuth callback. |
| `4bde9d98` | **"Synapse" UI.** Every file a particle, every relation a filament, force-laid-out; the agent's activity as signal travelling along edges; click-to-inspect with the file's live diff, its relations and the steps that touched it. Canvas 2D plus d3-force/d3-zoom, 1500-particle cap. Replaces the Survey treemap; `trail.ts` is kept. |
| `c1acce22` | Ship GroundTruth in the server image: the vendored `groundtruth-mcp` wheel plus the prebuilt Linux `gt-index` binary. |
| `34f11f69` | **`GET /api/sessions/{id}/graph`.** Static import edges per language plus GT symbol edges collapsed to file level; the mapping derived from the producer's own DDL, `CONTAINS` excluded; fail-open to `gt: false`; cached per tree signature; capped at 5000 nodes. `graph_db` persisted (schema v3). |
| `dd41057f` | **"Survey" UI.** Replaces the generic dark dashboard with a light two-column design: a squarified treemap lighting as the agent reads and edits, a scrubber, Trail/Changes/Bearings/Receipts instruments, and a radio-log conversation. Path inference only accepts unambiguous hits. |
| `aa177013` | **GT readiness was never reachable.** `IndexBuildReceipt` has no `available` attribute, so `gt_ready` could never be reported and every GT turn raised an `AttributeError` the degradation handler swallowed. Also: the opening message carries its turn id; the brief states that every command runs under POSIX bash regardless of host platform. |
| `32d89eae` | Make the tree size assertion byte-exact on Windows (`write_text` emits CRLF). |
| `75f0c9a5` | **`GET /api/sessions/{id}/tree`** — every tracked or untracked non-ignored file with its byte size, excluding `.gt_state/` and `.git/`. |
| `a26d02a6` | **Rebuild the server as a chat product over the mini-SWE + GT harness.** `ConversationalAgent` (one transcript across turns, a text-only response ends the turn, steering and stop at step boundaries, per-turn step budget, context collapsing); `SessionManager` (workspace create, turns under a per-session lock, receipts, cumulative diff, transcript persistence, restart recovery); the `/api/sessions` surface with cross-turn SSE, `Last-Event-ID`, `/diff`, `/receipts`, `/stop`, `/close`; `agent_error` renamed off `error`; CORS from env; schema v2. 60 tests. |
| `7bc6e760` | **Harden the runner, prove steering live, add route tests, finish the UI build.** `CloudLocalEnvironment` (credential-scrubbed shell env, `bash -c`, process-group kill); untracked files in the patch; every event normalised to one envelope; SSE replay stops at a stored terminal event; `MSWEA_COST_TRACKING` set before the mini-SWE import; deterministic list ordering. 40 tests, plus the first live five-case run. |
| `767f00f2` | **The service skeleton.** FastAPI with a steerable agent, a SQLite session store with state-machine enforcement, a per-session event bus, a runner that clones and optionally installs GT, GitHub OAuth with a server-issued JWT, and a React workspace UI. Plus the CI workflow, `.env.example`, docker-compose and the VM substrate evaluation. |
