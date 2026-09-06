# API reference

Every route below is as committed at `9c394863`, from `cloud/server/routes.py`,
`cloud/server/models.py`, `cloud/server/auth.py` and `cloud/server/events.py`.

- [Authentication](#authentication)
- [Auth endpoints](#auth-endpoints)
- [Health](#health)
- [Sessions](#sessions)
- [Messages and turns](#messages-and-turns)
- [Worker agents](#worker-agents)
- [Workspace views](#workspace-views)
- [Receipts](#receipts)
- [Server-sent events](#server-sent-events)
- [Schemas](#schemas)
- [Status codes at a glance](#status-codes-at-a-glance)

---

## Authentication

`router = APIRouter(dependencies=[Depends(require_user)])` — the dependency is
attached **once, at the router**, so no `/api` endpoint can forget it. There is
no "auth disabled" mode: a request with neither credential is `401`.

| Credential | Form |
|---|---|
| Cookie | `session=<jwt>` — set by `/auth/callback`, `HttpOnly`, `SameSite=Lax`, `max_age = JWT_TTL_SECONDS`. |
| Header | `Authorization: Bearer <jwt>` — the header wins when both are present. |

The JWT is HS256 over `JWT_SECRET` and carries `sub`, `login`, `name`,
`avatar_url`, `iat`, `exp`.

**The allow-list is re-checked on every request**, not only at
`/auth/callback`. If `ALLOWED_GITHUB_LOGINS` is non-empty and the token's
`login` is not in it, the answer is `403` — a token signed for a login that was
never allowed, or has since been removed, used to read and write every session
in the deployment (HAR-84 G-10). An empty `ALLOWED_GITHUB_LOGINS` means anybody
holding a token signed by `JWT_SECRET`.

There is no revocation list, so `JWT_TTL_SECONDS` (default **86400**, one day —
it was seven days) is also how long a removed user keeps access if the
allow-list is not used.

| Failure | Status |
|---|---|
| No cookie and no bearer header | 401 `not authenticated` |
| Expired token | 401 `session expired` |
| Bad signature or malformed | 401 `invalid session` |
| Valid token, login not allow-listed | 403 `user is not allowed to use this deployment` |

---

## Auth endpoints

`auth_router` is mounted at `/auth` and is **not** behind `require_user`
(except `/auth/me`).

### `GET /auth/login`

Redirects to GitHub's authorize URL with `scope=read:user` and a
`secrets.token_urlsafe(32)` state held in memory. States older than 600 s are
cleaned up on each login.

- `302` to GitHub.
- `500 GITHUB_CLIENT_ID not configured` when the client id is unset.

> The pending-state map is process-local, so a multi-worker or restarted server
> loses in-flight logins.

### `GET /auth/callback?code=&state=`

Exchanges the code, reads `https://api.github.com/user`, checks the allow-list,
mints the JWT, sets the `session` cookie and redirects to `UI_ORIGIN`
(default `/`).

- `302` to `UI_ORIGIN`, `Set-Cookie: session=...`.
- `400 invalid or expired state`.
- `400 GitHub token exchange failed: <error>`.
- `403 user <login> not in ALLOWED_GITHUB_LOGINS`.

The GitHub access token never reaches the browser.

### `GET /auth/me`

The decoded JWT claims. Accepts the same credentials as every `/api` route.

```json
{"sub": "62827797", "login": "harneet2512", "name": "...", "avatar_url": "...",
 "iat": 1788557646, "exp": 1788644046}
```

`200` / `401` / `403`.

### `POST /auth/logout`

`200`, and deletes the `session` cookie. It does not invalidate the JWT — there
is no revocation list.

---

## Health

### `GET /health`

Unauthenticated. Served by the app and proxied by nginx at `/health`.

```json
{"status": "ok", "commit": "9c394863"}
```

`commit` is `BUILD_SHA`, stamped into the image by `cloud/deploy.sh` through the
compose build arg, or `"unknown"`. It is the check that a deployment is not
running a stale image.

---

## Sessions

### `POST /api/sessions` — 201

```json
{
  "repo": "https://github.com/pallets/click",
  "ref": "main",
  "model": "nvidia/nemotron-3-super-120b-a12b:free",
  "gt_mode": "advisory",
  "step_limit": 60,
  "wall_seconds": 900,
  "temperature": 0.0,
  "first_message": "Fix the flaky test in tests/test_options.py"
}
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `repo` | string | required | Must match `^https://github\.com/[\w\-\.]+/[\w\-\.]+(\.git)?$`. Anything else is 400. |
| `ref` | string, ≤256 | `"main"` | Branch, tag, or full SHA. Rejected if blank, containing control characters, with leading/trailing whitespace, or starting with `-` (it would be read as a flag by `git clone`). |
| `model` | string, ≥1 | required | LiteLLM model identifier. Blank is 422; a syntactically fine model the provider will not serve is 400 (see the preflight). |
| `gt_mode` | `off` / `advisory` / `assistive` / `enforced` | `"off"` | Anything else is 422. |
| `step_limit` | int 1..500 | 60 | Model calls per turn. |
| `wall_seconds` | int 60..3600, nullable | `null` | Per-turn wall clock. Unset means "follow `TURN_WALL_SECONDS`", and is **not** stored on the row, so the server default stays configurable in one place. |
| `temperature` | float 0..2 | 0.0 | |
| `first_message` | string ≤100 000, nullable | `null` | An opening message. When set, the first turn starts by itself the moment the workspace is `idle` — no second call, no polling. Blank-after-strip is 422. |

The response is the `Session` object (below), `status: "creating"`.

**Creation preflight.** Before the row is written, `manager.check_model(model)`
sends one 1-token completion over the *same* LiteLLM route the turns will use.
An unusable model used to buy a clone, a sandbox, a GT index and a four-minute
first turn; it is now a `400 model not available: <reason>` in about three
seconds (HAR-84 G-11). `MODEL_PREFLIGHT=0` disables it.

| Status | When |
|---|---|
| 201 | Created; workspace creation continues in the background. |
| 400 | `repo must be a GitHub HTTPS URL`, or `model not available: ...`. |
| 422 | Any other field validation failure. |
| 429 | `MAX_CONCURRENT_CREATIONS` reached. The row is written and then marked `failed`, so the failure is visible rather than a silent 500. |

### `GET /api/sessions` — 200

Up to 100 sessions, newest first (`created_at DESC, rowid DESC` — rowid breaks
ties between two sessions created in the same clock tick). Workers are included;
filter on `role` / `parent_id`.

### `GET /api/sessions/{id}` — 200 / 404

### `POST /api/sessions/{id}/close` — 200

Idempotent-ish teardown: closes every live worker first, asks the running turn
to stop and waits up to 30 s for it, removes the sandbox, removes the workspace,
sets `status: "closed"` and `closed_reason`, publishes `agent_closed` on the
parent's stream if this is a worker, publishes `lifecycle closed {reason}`, and
releases every SSE subscriber. Returns the closed `Session`.

`404` when the session does not exist.

---

## Messages and turns

### `POST /api/sessions/{id}/messages` — 202

```json
{"content": "Now add a test for the empty-input case."}
```

`content` is 1..100 000 characters and must not be blank after `.strip()` —
`{"content": "   "}` used to be a 202 that burned two model calls and a
concurrency slot (HAR-84 G-12).

Response:

```json
{"message": { }, "delivery": "turn_started"}
```

| `delivery` | Meaning |
|---|---|
| `turn_started` | The session was `idle`; a turn was started. A `turn_started` frame carrying the content is on the stream. |
| `queued_for_running_turn` | A turn was already running; the message was queued as steering and will be drained at the next step boundary, with a `steering` frame. |
| `spawned` | The message was a `/spawn` command. No turn started; the `message` in the response is the server's `system_note`, and the user's own message was recorded separately. |

**`/spawn` parsing.** A message either *is* a spawn command — every non-blank
line matching `^\s*/spawn\s+(?P<task>\S.*?)\s*$` — or it is not one at all. Half
a command is a `400` rather than a turn that quietly runs the word `/spawn` past
a model. At most `MAX_TASKS_PER_SPAWN` (4) lines.

| Status | When |
|---|---|
| 202 | Accepted (any of the three deliveries). |
| 400 | A `/spawn` message with a non-`/spawn` line, or more than 4 tasks; or `model not available` for a spawn. |
| 409 | Session is `creating`, `closed` or `failed`; or a worker tried to spawn workers. |
| 422 | Blank or oversized `content`. |
| 429 | `MAX_CONCURRENT_SESSIONS` or the worker/creation caps. |

### `GET /api/sessions/{id}/messages` — 200 / 404

Every message, oldest first. Roles are `user`, `agent` and `system`.

### `POST /api/sessions/{id}/stop` — 202

```json
{"status": "stopping"}
```

Asks the running turn to end at its next step boundary, and interrupts the
command in flight so that boundary arrives at once. Also touches `updated_at`,
so a just-stopped session is not mistaken for a long-idle one by the reaper.

- `202` — accepted. If the turn worker has not built the agent yet, the stop is
  parked as `pending_stop` and applied when it does.
- `409 session has no running turn` when the status is not `running`.
- `404` unknown session.

---

## Worker agents

### `POST /api/sessions/{id}/agents` — 202

```json
{"tasks": ["add a CHANGELOG entry", "update the docstrings"],
 "model": null, "gt_mode": null}
```

`tasks` is 1..4 non-blank strings, each ≤100 000 characters. `model` and
`gt_mode` default to the parent's. Spawning is all-or-nothing.

```json
{"workers": [{ }, { }]}
```

| Status | When |
|---|---|
| 202 | All workers created; each runs its task as its own first turn. |
| 400 | `model not available`. |
| 409 | A worker tried to spawn workers, or the session is `creating`/`closed`/`failed`. |
| 422 | Empty, blank or oversized tasks; more than 4. |
| 429 | Over `MAX_WORKERS_PER_SESSION`, `MAX_CONCURRENT_CREATIONS`, or the turn budget. Nothing is created. |

### `GET /api/sessions/{id}/agents` — 200 / 404

Every session spawned by this one, oldest first, as `Session` objects (with
`role: "worker"`, `parent_id`, `task`, `report`, `applied_at`).

### `POST /api/sessions/{id}/agents/{worker_id}/apply` — 200

Merges the worker's cumulative diff into the parent workspace with `git apply
--3way`.

```json
{"worker_id": "3f1c9a20b4de", "files": ["src/click/core.py"],
 "patch_sha256": "9b1c..."}
```

Side effects on success: the worker row gets `applied_at` and `applied_sha256`,
its stored report flips `applied: true`, the parent's graph cache is dropped, a
`system_note` message is written, and both `system_note` and `agent_applied`
frames are published on the parent's stream.

| Status | When |
|---|---|
| 200 | Applied. |
| 400 | The worker has no changes, or the parent has no workspace to apply into. |
| 404 | No such worker for this session (checked by `parent_id`). |
| 409 | The parent is not `idle`; or the patch conflicts — the body is `{"detail": "...", "conflicts": ["path", ...]}`, with `conflicts` at the top level, not buried in `detail`. The parent's workspace is byte-for-byte what it was. |

### `POST /api/sessions/{id}/agents/{worker_id}/close` — 200

The same thing as closing the worker directly. Returns the worker's `Session`.
`404` when the worker does not belong to this session.

---

## Workspace views

### `GET /api/sessions/{id}/diff` — 200

The live cumulative diff against `base_sha`, including files the agent created
(`git add -A -N` first) and excluding `.gt_state/`.

```json
{"patch": "diff --git a/...", "base_sha": "8c3f...",
 "files": [{"path": "src/click/core.py", "status": "modified",
            "additions": 12, "deletions": 3, "patch": "diff --git ..."}]}
```

`status` is `added` / `modified` / `deleted`. A missing workspace returns an
empty diff rather than a 404.

### `GET /api/sessions/{id}/diff?through_event=N` — 200

The stored snapshot taken at or before `tool_result` event `N`. Adds:

| Field | Meaning |
|---|---|
| `as_of_event` | The event id this diff is the state *after*. `0` when no snapshot exists yet (and `patch`/`files` are empty). |
| `approximate` | Always `false`. This is a stored snapshot, not a reconstruction. |
| `truncated` | Present and `true` only when the stored patch hit the 512 KB cap; the per-file bodies are then empty. |

`through_event` must be `>= 0` (422 otherwise). The three fields above are
omitted entirely from the live diff (`response_model_exclude_none`).

### `GET /api/sessions/{id}/tree` — 200

```json
{"base_sha": "8c3f...", "files": [{"path": "src/click/core.py", "size": 91234}]}
```

Every tracked or untracked non-ignored file with its byte size, sorted by path,
excluding `.git/` and `.gt_state/`.

### `GET /api/sessions/{id}/graph` — 200

```json
{"base_sha": "8c3f...", "gt": true,
 "nodes": [{"id": "src/click/core.py", "path": "src/click/core.py",
            "size": 91234, "lang": "py", "dir": "src"}],
 "edges": [{"source": "src/click/core.py", "target": "src/click/types.py",
            "kind": "gt_call", "weight": 17}]}
```

| Field | Meaning |
|---|---|
| `gt` | True when GT-derived edges are included. False when `gt_status != "ready"` or reading the graph db faulted in any way. |
| `nodes[].id` | Identical to `path`; the UI keys its layout off `id`. |
| `nodes[].lang` | Extension without the dot; `""` when there is none. |
| `nodes[].dir` | First path segment; `""` for a file at the repo root. |
| `edges[].kind` | `import`, `gt_call`, `gt_ref`, `gt_import`. |
| `edges[].weight` | How many underlying relations collapsed into this file-level edge. |
| `truncated` | Present and `true` only when the graph was capped to the 5000 busiest files. |

---

## Receipts

### `GET /api/sessions/{id}/receipts` — 200

One per turn, oldest first.

```json
[{"turn_id": "cb501ff62fbd", "started_at": 1788557646.1,
  "finished_at": 1788557712.4, "n_calls": 7, "cost": 0.0,
  "wall_seconds": 66.3, "finish_reason": "reply",
  "patch_sha256": "9b1c...", "gt_status": "ready",
  "model": "nvidia/nemotron-3-super-120b-a12b:free"}]
```

`gt_status` and `model` are captured when the turn *starts*, so a receipt says
what the turn actually ran with. `cost` is always `0.0` under
`MSWEA_COST_TRACKING=ignore_errors`; `wall_seconds` is the honest budget line. A
turn still in flight has `finished_at: null` and `finish_reason: ""`.

> **In progress.** The GroundTruth typed-action package intends to add
> `gt_actions` (how many `gt_action` frames the turn emitted) and
> `gt_exact_matches` (the subset with `semantics == "exact"` **and**
> `match_count > 0`), with a `gt_actions` total on the session row. Neither
> field exists at `9c394863`.

---

## Server-sent events

### `GET /api/sessions/{id}/events` — 200 `text/event-stream`

Query: `after_id` (int, default 0). Header: `Last-Event-ID`.
Response headers: `Cache-Control: no-cache`, `Connection: keep-alive`,
`X-Accel-Buffering: no`.

Each frame:

```
id: 412
event: tool_result
data: {"id":412,"type":"tool_result","timestamp":1788558430.12,"data":{}}
```

- Every frame carries an `event:` field, so `EventSource.onmessage` never fires.
  Register a listener per type.
- `after_id` replays only events newer than it; `Last-Event-ID` does the same
  and is used only when `after_id` is absent or 0.
- A `Last-Event-ID` that is not an integer, or is negative, is a **400**.
  `after_id` itself is not range-checked in the route.
- Replay is capped at 5000 events per call.
- `: ping` every `SSE_HEARTBEAT_SECONDS` (default 15).
- The stream ends after a terminal `lifecycle` event (`closed` or `failed`), and
  a subscriber attaching to an already-terminal session gets the replay and then
  the close.

`404` when the session does not exist.

Every payload below is the `data` object of the envelope. **Every frame carries
`turn_id`** when the agent emitted it (`_emit` merges it in), and
`agent_id` when it was mirrored from a worker.

### `lifecycle`

```json
{"status": "sandbox_ready", "container": "gt-sandbox-3f1c9a20b4de",
 "image": "gt-sandbox:latest", "image_digest": "sha256:..."}
```

| `status` | Emitted when | Extra fields |
|---|---|---|
| `creating` | The creation task is queued. | |
| `cloning` | Before `git clone`. | `repo`, `ref` |
| `sandbox_starting` | `SANDBOX_MODE=docker`, before `docker run`. | |
| `sandbox_ready` | The container answered a probe exec. | `container`, `image`, `image_digest` |
| `sandbox_failed` | The sandbox would not start. The session then fails. | `error` |
| `sandbox_restarted` | A wedged container was recreated, or a stopped one restarted, mid-session. | `container` |
| `indexing` | `gt_mode != off`, before the indexer runs. | |
| `gt_ready` | The index built and a graph db came back. | `gt_mode`, `graph_db` |
| `gt_unavailable` | Indexing or engine installation failed. The session still runs. | `error` |
| `idle` | The workspace is ready, or a turn ended. | |
| `running` | A turn started. | |
| `stopped` | A turn ended because of `/stop`. Followed by `idle`. | |
| `quota_exceeded` | The workspace passed `SANDBOX_WORKSPACE_MAX_MB`; the turn is ending in `error`. | `reason` |
| `diff_snapshots_disabled` | One `compute_diff` overran the 2 s budget; no more snapshots this turn. | `reason` |
| `failed` | Workspace creation failed. Terminal. | `error` |
| `closed` | The session was closed. Terminal. | `reason` (`user` / `expired` / `failed`) |

### `turn_started`

```json
{"turn_id": "cb501ff62fbd", "message_id": "8a1d3f0c2b77",
 "role": "user", "content": "Now add a test for the empty-input case."}
```

`content` and `role` are there so **every** subscriber can render the prompt.
With only `message_id`, a second tab showed the turn and the reply but never the
question (HAR-84 G-09). On a chained turn, `content` is the pending steering
messages joined by a blank line and `message_id` is the last of them.

### `assistant`

```json
{"turn_id": "...", "content": "I need to see how the option parser is wired.",
 "actions": ["rg -n \"class Option\" src/click"],
 "step": 3, "n_calls": 3, "cost": 0.0}
```

One per model call. `step` equals `n_calls` at the time of the call.

`is_reply: true` marks the model call that produced the turn's **text reply**
rather than actions. It counts as a step — without it a live count is one short
of `turn_finished.n_calls` — but its content arrives again as `agent_reply`, so
a client must count it and not render it.

### `tool_call`

```json
{"turn_id": "...", "command": "pytest -q", "step": 3, "n_calls": 3}
```

Emitted by `_EmittingEnvironment`, so it fires for GT's own `execute_actions`
replacement too. A command that is empty after `.strip()` emits nothing
(HAR-84 G-19) — the command still runs and its output still reaches the model.

### `tool_result`

```json
{"turn_id": "...", "command": "pytest -q", "output": "42 passed",
 "returncode": 0, "is_error": false, "step": 3}
```

`output` is capped at **4000 characters** in the frame only; the agent sees the
full output. `is_error` is `returncode != 0`. Notable codes: `137` is a killed
command — a `/stop`, the wall-clock watchdog, or the container memory limit
(the last of which is given an explicit `[killed: ...]` line, HAR-84 G-20).

This is the frame diff snapshots and the workspace quota check hang off.

### `steering`

```json
{"turn_id": "...", "message_id": "...", "content": "actually, skip the docs"}
```

Emitted when a mid-turn message is drained into the transcript.

### `agent_reply`

```json
{"turn_id": "...", "message_id": "...", "content": "Done — I added ...",
 "finish_reason": "reply", "n_calls": 7, "cost": 0.0,
 "patch_sha256": "9b1c...", "files_changed": ["src/click/core.py"]}
```

The turn's answer. `finish_reason` is one of `reply`, `question`, `step_limit`,
`time_limit`, `stopped`, `error`, `submitted`.

### `turn_finished`

```json
{"turn_id": "...", "finish_reason": "reply", "n_calls": 7, "cost": 0.0,
 "patch_sha256": "9b1c...", "files_changed": ["src/click/core.py"]}
```

`patch_sha256` and `files_changed` are carried here too, so a turn that ended in
`stopped` / `step_limit` / `error` still exposes patch identity without
refetching `/receipts`. The `interrupted` variant written by `recover()` carries
only `turn_id`, `finish_reason`, `n_calls` and `cost`.

### `agent_error`

```json
{"turn_id": "...", "error": "SandboxError: sandbox gt-sandbox-... no longer exists"}
```

Named `agent_error` and not `error` on purpose: a frame named `error` is also
delivered to `EventSource.onerror` and cannot be told apart from a transport
failure.

### `system_note`

```json
{"turn_id": "...", "message_id": "...", "content": "Server restarted; turn interrupted"}
```

The product speaking in its own voice, at the point in the thread where it
happened. Emitted for: the restart notice, a `/spawn` summary, an `apply`
summary, and the note when an opening turn could not get a concurrency slot. The
same text is also a `role: "system"` message, so it survives a reload.

### Worker frames

| Type | Payload | Published on |
|---|---|---|
| `agent_spawned` | `{worker_id, task}` | The parent |
| `agent_report` | `{worker_id, message_id, finish_reason, content, patch_sha256, files_changed, n_calls, cost}` | The parent, after each of the worker's turns |
| `agent_applied` | `{worker_id, files, patch_sha256}` | The parent |
| `agent_closed` | `{worker_id, reason}` | The parent |

Plus **mirroring**: `assistant`, `tool_call`, `tool_result`, `turn_started` and
`turn_finished` frames from a worker are re-published on the parent's stream
with `agent_id: "<worker_id>"` added to `data`. A frame from a primary session
carries no `agent_id` at all, so the field is the whole protocol: a frame that
has it belongs to that worker and never to the primary turn or its step count.

### `gt_action` (in progress)

> **Not emitted at `9c394863`.** `cloud/server/gt_events.py` is untracked. This
> is the intended payload.

```json
{"turn_id": "...", "step": 2, "kind": "exact_literal_search",
 "arguments": {"literal": "class Command", "paths": ["src/click"]},
 "scope": ["src/click"], "returncode": 0,
 "semantics": "exact", "coverage": "complete", "match_count": 2,
 "omissions": [], "reason_codes": ["EXACT_COMPLETE_EQUIVALENCE"],
 "duration_ms": 41.2, "evidence_artifact_id": "call_ab12"}
```

| Field | Meaning |
|---|---|
| `step` | The model call that asked for it — the same `step` its `assistant` frame carries. A typed action is *part of* that call and never adds an `assistant` frame of its own. |
| `kind` | The typed action (`exact_literal_search`, `find_callers`, `why_this_edge`, ...). |
| `arguments` | The arguments **as dispatched**, i.e. after `typed_scopes` made a glob scope concrete. Every string truncated to 200 characters. |
| `scope` | What the producer says it actually searched, echoed from `answer["scope"]` (or `coverage["scope"]` on the compatibility producer). Empty when the producer did not say — a request is not evidence of coverage, so the requested paths are never echoed here. They are in `arguments`. |
| `returncode` | From the action's output mapping; `-1` when absent. |
| `semantics` / `coverage` | `exact`/`incomplete` and `complete`/`partial`, from `gt.evidence_artifact.v1`. `""` when the producer did not return a verdict. |
| `match_count` | Rows in the answer, counted the way GT counts its own `returned_count`. |
| `omissions` | Why the evidence is not complete (`missing_scope:src/click/**`, `capability_disabled`, `query_result_byte_limit`, ...), capped at 10. |
| `reason_codes` | From `gt.interception_decision.v1` — `EXACT_COMPLETE_EQUIVALENCE` on an answer; `SEMANTICS_NOT_EXACT` / `COVERAGE_NOT_COMPLETE` / `EVIDENCE_HAS_OMISSIONS` on an abstention. |
| `duration_ms` | Wall clock of the action batch this action belonged to. A model call almost always carries one action, in which case it is that action's own time. |
| `evidence_artifact_id` | The evidence artifact's `action_id`, or the compiled observation's sha256 when it has none. Absent when neither is available. |

---

## Schemas

### `Session`

```json
{
  "id": "3f1c9a20b4de",
  "status": "idle",
  "repo": "https://github.com/pallets/click",
  "ref": "main",
  "model": "nvidia/nemotron-3-super-120b-a12b:free",
  "gt_mode": "advisory",
  "gt_status": "ready",
  "gt_error": null,
  "created_at": 1788557646.1,
  "updated_at": 1788557712.4,
  "last_message": "Done — I added ...",
  "turns": 3,
  "steps": 21,
  "cost": 0.0,
  "total_wall_seconds": 184.7,
  "current_turn_id": null,
  "closed_reason": null,
  "parent_id": null,
  "role": "primary",
  "task": null,
  "report": null,
  "applied_at": null
}
```

| Field | Notes |
|---|---|
| `status` | `creating` / `idle` / `running` / `failed` / `closed`. |
| `gt_status` | `off` / `pending` / `ready` / `unavailable`. `pending` is the value a `gt_mode != off` session carries between creation and the index result. |
| `gt_error` | Why GT is unavailable, in the indexer's own words. Survives a reload, unlike the `gt_unavailable` frame, which scrolls away. |
| `turns` / `steps` / `cost` / `total_wall_seconds` | Running totals over finished turns. `cost` is always 0.0. |
| `current_turn_id` | Non-null only while `running`. |
| `closed_reason` | `user` / `expired` / `failed`, or null while alive. |
| `parent_id` / `role` / `task` | Worker identity. `role` is `primary` or `worker`. |
| `report` | The worker's last `WorkerReport` (`finish_reason`, `reply_excerpt` ≤400 chars, `patch_sha256`, `files_changed`, `applied`), or null. |
| `applied_at` | When this worker's patch was applied to the parent workspace. |

### `Message`

```json
{"id": "8a1d3f0c2b77", "session_id": "3f1c9a20b4de", "turn_id": "cb501ff62fbd",
 "role": "agent", "content": "Done — I added ...", "created_at": 1788557712.4,
 "meta": {"finish_reason": "reply", "n_calls": 7, "cost": 0.0,
          "patch_sha256": "9b1c...", "files_changed": ["src/click/core.py"],
          "agent_id": null}}
```

`role` is `user` / `agent` / `system`. `turn_id` is nullable — a session-level
system note has no turn to belong to. `meta.agent_id` is set only on a `role:
"agent"` message a **worker** reported into its parent's conversation, and is
absent on the parent's own replies.

### `TurnReceipt`

See [Receipts](#receipts).

### Enumerations

| Name | Values |
|---|---|
| `SessionStatusName` | `creating` `idle` `running` `failed` `closed` |
| `GtStatusName` | `off` `ready` `unavailable` `pending` |
| `GtModeName` | `off` `advisory` `assistive` `enforced` |
| `RoleName` | `user` `agent` `system` |
| `SessionRole` | `primary` `worker` |
| `FinishReason` | `reply` `question` `step_limit` `time_limit` `stopped` `error` `submitted` `interrupted` |
| `ClosedReason` | `user` `expired` `failed` |
| `Delivery` | `turn_started` `queued_for_running_turn` `spawned` |
| `FileStatus` | `added` `modified` `deleted` |
| `EdgeKind` | `import` `gt_call` `gt_ref` `gt_import` |

---

## Status codes at a glance

| Code | Meaning in this API |
|---|---|
| 200 | Read, close, apply, or a worker close. |
| 201 | Session created. |
| 202 | Accepted asynchronously: a message, a spawn, a stop. |
| 400 | A malformed repo URL, an unusable model, a half-written `/spawn`, a malformed `Last-Event-ID`, or an apply with nothing to apply. |
| 401 | No or bad credentials. |
| 403 | Authenticated but not on `ALLOWED_GITHUB_LOGINS`. |
| 404 | No such session or no such worker of this session. |
| 409 | Wrong state: messaging a `creating`/`closed`/`failed` session, stopping a session with no running turn, a worker spawning workers, applying into a non-idle parent, or a patch conflict (with `conflicts`). |
| 422 | Pydantic validation: a blank message, an unknown `gt_mode`, an out-of-range budget, a bad `ref`. |
| 429 | A concurrency cap: turns, creations, or workers per session. |
| 500 | `GITHUB_CLIENT_ID` not configured (login only). |
