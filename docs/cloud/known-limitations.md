# Known limitations

Everything not done, deferred, or working in a way that will disappoint someone,
with the reason and where it lives. As of `9c394863`.

- [In progress right now](#in-progress-right-now)
- [Deferred from the audit](#deferred-from-the-audit)
- [Product limits](#product-limits)
- [Isolation and security](#isolation-and-security)
- [Operational limits](#operational-limits)
- [GroundTruth limits](#groundtruth-limits)
- [Testing gaps](#testing-gaps)

---

## In progress right now

| Item | State | Where |
|---|---|---|
| **Worker agents in the browser.** The server side is complete and the API works, but the committed UI still answers `/spawn` with *"spawning worker agents is coming — the server side is being built"*. `cloud/ui/src/api.ts` has no `agent_spawned` / `agent_report` / `agent_applied` / `agent_closed` event types, no `agent_id` on `MessageMeta`, no `"spawned"` in `MessageDelivery`, and no spawn/apply client functions. | In progress | `cloud/ui/src/**` (uncommitted: `workers.ts`, `WorkerCard.tsx`, `TermWorker.tsx`, `__tests__/workers.test.ts`) |
| **The Claude-Code terminal re-skin.** `>` prompt, `⏺`/`⎿` lines, spinner status with *esc to interrupt*, box-drawn input, `/resume`, `/theme`, dark by default with a light terminal theme. `slash.ts` still lists six commands with no `/resume` or `/theme`. | In progress | `cloud/ui/src/**` (uncommitted: `TermLine`, `TermStatus`, `TermOutput`, `TermActivity`, `TermSettings`, `Box`, `ResumePicker`, `theme.ts`, `palette.ts`, `gt.ts`, `styles/term.css`) |
| **GroundTruth typed-action events.** A typed GT action produces no `tool_call`/`tool_result` pair, so the trail shows a model call with nothing under it. The intended `gt_action` frame, and the `gt_actions` / `gt_exact_matches` receipt counters, are not committed. | In progress | `cloud/server/gt_events.py`, `tests/test_cloud_gt_events.py` (both untracked) |

Once these land, re-check [api.md](api.md), [user-guide.md](user-guide.md) and
[testing-and-ci.md](testing-and-ci.md).

---

## Deferred from the audit

| Gap | Status | Why |
|---|---|---|
| **G-14 — `/stop` while a model call is in flight** | Partially fixed | 0.48 s when a command is running (it is killed); **46.8 s** measured when a model call is. The LiteLLM call is synchronous inside the turn worker, so a stop cannot reach it. Fully fixing it means changing how turns are executed. `MODEL_REQUEST_TIMEOUT` (default 300 s) now bounds the worst case explicitly instead of leaving it to the provider. |
| **G-18 — GT evidence artifacts absent from the transcript** | Deferred | `.gt_state/transcript.json` holds the `exact_literal_search` calls and zero `gt.evidence_artifact.v1`, zero typed actions, zero `abstain` records. Persisting them means changing what `gt_engine.miniswe_runtime` / `MiniSweAdapter` write — i.e. editing `gt_engine/**`, which is out of scope for the cloud change sets. GT's contribution is visible in `/graph` but not receipted in the trajectory. The in-progress `gt_action` package addresses the *stream*, not the trajectory. |

Both are recorded in [`docs/cloud-audit-fixes.md`](../cloud-audit-fixes.md) with
their measurements.

---

## Product limits

| Limit | Detail |
|---|---|
| **An interrupted turn is not resumed.** | After a restart, a `running` session becomes `idle`, the receipt closes with `finish_reason: "interrupted"` and a `system_note` says so. The work already done is in the workspace and the transcript; the turn itself is not restarted. |
| **Applying a worker's patch is not transactional against the parent's agent.** | It changes files, not the transcript. The parent's agent does not know it happened unless told. |
| **A worker's patch dies with its workspace.** | Apply **before** close: closing a worker deletes its clone like any other session's. |
| **One worker at a time, one level deep.** | `apply` merges a single worker; applying two workers that touched the same lines gives the second a 409. A worker cannot spawn workers. |
| **Public GitHub HTTPS only.** | `repo` must match `^https://github\.com/owner/name(\.git)?$`. No private repositories (the server holds no git credentials), no other hosts, no SSH remotes, no local paths. |
| **Shallow clones.** | `git clone --depth 1`, or `init` + `fetch --depth 1 <sha>` + `checkout FETCH_HEAD` for a SHA. History is not available to the agent. |
| **No branch, commit or push.** | The session produces a diff. Nothing pushes it anywhere. |
| **No per-object authorisation.** | Any authenticated, allow-listed user can list, read, message, stop and close **every** session in the deployment. Sessions carry no owner. |
| **Cost is untracked.** | `MSWEA_COST_TRACKING=ignore_errors` is required, because LiteLLM aborts a run it cannot price and the free models have no price entry. `cost` is always `0.0`; `wall_seconds` is the budget signal that means something. |
| **`/spawn` is all-or-nothing and shallow-parsed.** | Every non-blank line of the message must be a `/spawn` line, or it is a 400. |
| **Slash commands are client-side.** | Only `/spawn` has a server behaviour. The others do nothing over the API. |

---

## Isolation and security

Full treatment in [security.md](security.md) and
[`docs/cloud-sandbox.md`](../cloud-sandbox.md) §7.

| Limit | Detail |
|---|---|
| **`SANDBOX_MODE=local` has no isolation.** | It is the default outside compose. Agent commands run in the server process's own machine account. |
| **No seccomp/AppArmor/SELinux profile beyond Docker's default**, and no user-namespace remapping. | Docker's default profile plus `--cap-drop ALL` plus `no-new-privileges` is the whole kernel-attack-surface story. |
| **The disk caps are watermarks, not kernel quotas.** | A single `dd` larger than `SANDBOX_WORKSPACE_MAX_MB` still lands on disk in full and is caught immediately afterwards. A hard bound needs `WORKSPACES_HOST_DIR` on a dedicated volume; `--storage-opt size=` works only on overlay2 over xfs mounted `pquota`, and not at all on a codespace's ext4 overlay. |
| **The proxy is HTTP-level.** | `CONNECT host:443` and absolute-form plain HTTP, ports 80 and 443 only. SSH, raw TCP, UDP, ICMP and `ssh://` remotes are not proxied and have nowhere to go — so "blocked by policy" and "unreachable" look the same to a tool. |
| **No DNS inside the sandbox network.** | A tool that resolves a name itself before connecting reports a name-resolution error, not a policy denial. |
| **TLS is not inspected.** | `CONNECT` is allowed or refused on the authority the client asked for; SNI is not checked against the request and the tunnel contents are not examined. |
| **One shared proxy.** | Its logs interleave and its allow-list is global. There is no per-session policy. |
| **The allow-list is host-level.** | Anything a sandbox can reach on `github.com` it can reach in full. |
| **Cross-session interference is not prevented.** | Sessions share a host, a CPU quota and one proxy. This is not a multi-tenant boundary. |
| **A server compromise is total.** | The server holds the Docker socket and the provider keys. |
| **No JWT revocation.** | `JWT_TTL_SECONDS` (default 24 h) is the only bound; the allow-list re-check is the mitigation. |
| **The OAuth pending-state map is process-local.** | A restart or a second worker loses in-flight logins. |
| **Docker Desktop weakens the uid-1000 story.** | Bind-mount ownership and permissions are synthesised, so `prepare_workspace()` is effectively a no-op there. |

---

## Operational limits

| Limit | Detail |
|---|---|
| **Single server, SQLite, no horizontal scaling.** | `SessionManager` holds in-memory state per session and the event bus is process-local. A second replica would not see either. |
| **The database is dropped on a schema bump.** | `init()` rebuilds every table when `PRAGMA user_version` differs. Six schema versions landed in two days. No migrations, no backup mechanism, and workspace directories are left orphaned on disk. See [operations.md](operations.md#the-database-is-dropped-on-a-schema-bump). |
| **Codespaces port visibility resets on every deploy.** | Recreating the containers re-registers ports 80/8000 as private and the public URL 302s to GitHub sign-in. `gh codespace ports visibility 80:public -c <name>` after every deploy, from a machine with a `codespace`-scoped `gh` login. |
| **A codespace rebuilt under a new name needs the OAuth App updated.** | The callback host must match the forwarded origin exactly. |
| **Free Codespaces hours are the real ceiling.** | A 4-core machine burns 4 core-hours per wall-clock hour: roughly 30 h/month on Free, 45 h on Pro of *running* time. Storage keeps accruing until the codespace is deleted. |
| **`restart: unless-stopped` does not undo a manual stop.** | A `docker kill` or `docker stop` stays stopped. Bounce with `docker compose restart <svc>`. |
| **`verify.sh` hard-codes Codespaces paths.** | `cd /workspaces/gt-harness` and `/srv/gt-workspaces/...`. Adjust both on another host. |
| **Diff snapshots can be switched off mid-turn.** | One `compute_diff` over 2 s disables snapshots for the rest of the turn; the scrubber then has no data for later steps. A `diff_snapshots_disabled` frame says so. |
| **A stored snapshot patch is capped at 512 KB.** | Over it the per-file bodies are dropped and `truncated: true` is set — it becomes a summary, not a patch anyone can apply. The `patch_sha256` is still over the full patch. |
| **SSE replay is capped at 5000 events.** | A very long session's history is not fully replayable from event 0. |
| **The graph is capped at 5000 nodes.** | Over it only the busiest files survive, with `truncated: true`. |
| **`after_id` is not range-checked** in the events route, unlike `Last-Event-ID`. | A negative value is passed through to the store. |

---

## GroundTruth limits

| Limit | Detail |
|---|---|
| **GT features need the `gt-index` binary and the `groundtruth-mcp` wheel.** | Without them a session degrades to `gt_status: unavailable` and runs plain. |
| **The cloud producer is not the certified benchmark producer.** | It is the pinned commit plus upstream PR #6, stamped `+cloud.2`, and must never be used as a benchmark producer. When PR #6 merges into the pinned commit, `cloud/producer/` can be deleted. |
| **`GT_PRODUCER_ARTIFACT` must stay unset.** | Setting it makes `_binary_certification()` fail closed against a deliberately-uncertified binary, and every session degrades to `gt_unavailable`. So must `GT_TASK_ID` / `GT_PRODUCT_SOURCE_SHA`. |
| **`shadow` mode is not offered.** | It runs the engine without letting it affect the agent — a benchmark mode, not a product one. |
| **Typed-scope normalisation only widens.** | A scope whose literal prefix does not exist is left alone and the producer abstains, by design: a typo must not silently widen to a parent. |
| **A secondary HAR-85 observation was not fixed.** | Recorded in [`docs/har85-literal-search.md`](../har85-literal-search.md) §2. |
| **GT contribution is visible in `/graph` but not receipted.** | G-18, above. |

---

## Testing gaps

| Gap | Detail |
|---|---|
| **No component tests.** | Vitest runs `environment: "node"` over the pure layers only; component tests would need jsdom. Browser behaviour is covered by manual Playwright passes recorded in commit messages, not by an automated suite in CI. |
| **The docker-dependent sandbox tests skip in CI.** | `SANDBOX_MODE` is unset on the runner and there is no daemon, so the integration half of `tests/test_cloud_sandbox.py` never runs there. It runs locally and on the deployment host. |
| **The model provider is faked in every server suite.** | Deliberately — but it means no automated test exercises a real provider's error shapes. Those were found live and are pinned by unit tests over the *shapes*, not the provider. |
| **No load or soak testing.** | The concurrency caps are asserted; behaviour at the caps under sustained load is not measured. |
| **No test asserts the UI and server event catalogues agree.** | The `_WRITES` regex has a twin test; the event type list does not. `cloud/ui/src/api.ts:EVENT_TYPES` is currently missing the four worker event types the server emits. |
