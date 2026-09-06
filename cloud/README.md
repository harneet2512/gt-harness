# GT Cloud Coding Agent

Internal cloud coding agent powered by the GT mini-SWE harness. It is a **chat
product**: you open a session against a repo, and then you talk to an agent that
lives in a persistent clone of it — like Codex or Claude Code, with GroundTruth
underneath and a receipt on every turn.

## Model

- A **session** is one persistent repo workspace plus one conversation. It is
  created with `repo, ref, model, gt_mode` — there is no "task". Creation clones
  the repo (and builds the GT index when `gt_mode != off`) in the background:
  `creating` → `idle`.
- Every **user message** drives an **agent turn** on the *same* mini-SWE
  transcript. The agent's memory is its real trajectory, not a summary. If the
  session is `idle` the message starts a turn; if a turn is already running the
  message is delivered at the next step boundary and the agent answers it in
  context (`steering`).
- A **turn ends when the agent talks to you**: a model response with no command
  block is the reply — finished, or asking a question. It is not a format error.
  Other endings: `stopped`, `step_limit` (per-turn step budget), `time_limit`
  (per-turn wall-clock budget), `error`, and the legacy mini-SWE submit marker
  (`submitted`).
- The workspace lives for the life of the session under `WORKSPACES_DIR`
  (default `./workspaces/<session_id>`) and is removed on `close` — or by the
  idle TTL reaper, which closes a session the same way once it has been `idle`
  for `SESSION_IDLE_TTL_SECONDS`. The cumulative diff is available at any time.

## Quickstart

### 1. Prerequisites

- Python 3.12+
- Node.js 18+
- Git, and a POSIX `bash` on PATH (Git Bash is fine on Windows)
- A GitHub OAuth App ([create one](https://github.com/settings/developers))
  with callback URL `http://localhost:8000/auth/callback`
- A model provider API key (DeepSeek, OpenRouter, etc.)

### 2. Install

```bash
git clone https://github.com/harneet2512/gt-harness.git
cd gt-harness
git checkout cloud/internal-harness

pip install -e ".[cloud,miniswe]"

cd cloud/ui && npm install && cd ../..
```

### 3. Configure

```bash
cp cloud/.env.example cloud/.env
# Fill in GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, JWT_SECRET and a provider key.
```

Every knob has a comment in `cloud/.env.example`. The ones that decide whether
a bad day stays bounded:

| Variable | Default | What it bounds |
|---|---|---|
| `ALLOWED_GITHUB_LOGINS` | *(empty = anyone with a valid token)* | Re-checked on **every** request, not just at `/auth/callback`: a login not on the list is 403 even with a correctly signed JWT. |
| `JWT_TTL_SECONDS` | `86400` | How long a signed-in session lasts. Was 7 days; there is no revocation, so removing somebody from the allow-list only takes effect when their token expires. |
| `MODEL_PREFLIGHT` | `1` | One 1-token completion at session creation, over the session's own LiteLLM route. `0` skips it (tests, air-gapped runs). |
| `MODEL_REQUEST_TIMEOUT` | `300` | Per model call. LiteLLM's own retries are pinned off (`num_retries` **and** `max_retries`), so a dead model fails in seconds instead of retrying 11 times with a 60 s backoff. |
| `WORKSPACES_MIN_FREE_MB` | `2048` | Free space under `WORKSPACES_DIR` below which a new session is refused outright, with a readable reason. `0` disables it. |
| `SANDBOX_WORKSPACE_MAX_MB` | `2048` | Per-session workspace cap, measured (`du -sm`) after every write-shaped command. Over it the command is killed and the turn ends `error`. `0` disables it. **Not** a filesystem quota — see `docs/cloud-sandbox.md` §5. |
| `MAX_CONCURRENT_SESSIONS` | `3` | Turns running at once (429 past it). |
| `MAX_CONCURRENT_CREATIONS` | `3` | Clones + GT indexes running at once (429 past it). Creation used to take no slot at all. |
| `SESSION_IDLE_TTL_SECONDS` | `21600` | How long an idle session keeps its clone and container. |

### 4. Run

Terminal 1 — API server (from the repo root):
```bash
python -m uvicorn cloud.server.app:app --port 8000
```

Terminal 2 — UI dev server:
```bash
cd cloud/ui && npm run dev
```

### 5. Use it from curl

Every `/api/*` route requires a JWT, as the cookie `session` **or** as
`Authorization: Bearer <jwt>` — so curl works once you have a token from
`/auth/login`.

```bash
TOKEN=...   # the `session` cookie value after logging in
API=http://127.0.0.1:8000/api
AUTH="Authorization: Bearer $TOKEN"

ID=$(curl -s -X POST $API/sessions -H "$AUTH" -H 'Content-Type: application/json' \
     -d '{"repo":"https://github.com/octocat/Hello-World","ref":"master",
          "model":"deepseek/deepseek-v4-flash","gt_mode":"off"}' | jq -r .id)

curl -sN $API/sessions/$ID/events -H "$AUTH" &          # live feed, stays open

curl -s -X POST $API/sessions/$ID/messages -H "$AUTH" \
     -H 'Content-Type: application/json' -d '{"content":"add a hello.py"}'

curl -s $API/sessions/$ID/diff     -H "$AUTH"
curl -s $API/sessions/$ID/receipts -H "$AUTH"
curl -s -X POST $API/sessions/$ID/close -H "$AUTH"
```

## API

All `/api/*` routes require auth (401 otherwise). `/health` is public.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/sessions` | Create a session (`repo`, `ref`, `model`, `gt_mode`, `step_limit`, `wall_seconds` (60–3600, default `TURN_WALL_SECONDS`), `temperature`, `first_message`) → 201 `Session`. `first_message` starts the first turn by itself as soon as the workspace is ready — create-and-send in one call, no polling for `idle` (**422** if it is blank). **400** on a non-GitHub URL, or `model not available: <reason>` when the creation preflight (`MODEL_PREFLIGHT`) cannot get a 1-token completion out of the provider. **422** on an unknown `gt_mode`, a blank `model`, or a `ref` that is blank, has control characters or starts with `-`. **429** when `MAX_CONCURRENT_CREATIONS` clones are already in flight. |
| GET | `/api/sessions` | List sessions, newest first |
| GET | `/api/sessions/:id` | One session |
| GET | `/api/sessions/:id/messages` | Full conversation, in order |
| POST | `/api/sessions/:id/messages` | Send a message → 202 `{message, delivery}` where delivery is `turn_started` or `queued_for_running_turn`. 409 while `creating`/`closed`/`failed`; 422 when the content is blank **or only whitespace**. |
| GET | `/api/sessions/:id/events` | SSE stream, open across turns (`?after_id=` or `Last-Event-ID:`). A `Last-Event-ID` that is not a non-negative integer is **400**, not a silent replay of the whole history. |
| GET | `/api/sessions/:id/diff` | Cumulative diff vs the cloned commit, incl. untracked files. With `?through_event=N` it returns the stored snapshot taken at the latest write **at or before** event `N` instead — same shape plus `{as_of_event, approximate: false}` (and `truncated: true` when the stored patch hit the 512 KB cap). `as_of_event: 0` means nothing had been written yet. |
| GET | `/api/sessions/:id/tree` | Every file in the workspace with its byte size (`{base_sha, files:[{path,size}]}`), for the map |
| GET | `/api/sessions/:id/graph` | File relation graph (`{base_sha, gt, nodes:[{id,path,size,lang,dir}], edges:[{source,target,kind,weight}]}`). `kind` is `import` (static imports) or `gt_call`/`gt_ref`/`gt_import` (GT symbol edges collapsed to file level, only when `gt_status` is `ready`; `gt` says whether they are in). Nodes are exactly the files `/tree` returns; over 5000 files only the busiest survive and `truncated: true` is added. |
| GET | `/api/sessions/:id/receipts` | One receipt per turn |
| POST | `/api/sessions/:id/stop` | Stop the running turn at the next step boundary → 202 (409 if idle) |
| POST | `/api/sessions/:id/close` | Kill the turn, delete the workspace, status `closed`, `closed_reason: "user"` (idempotent). **Cascades**: every live worker of the session is closed the same way first. |
| POST | `/api/sessions/:id/agents` | Spawn worker agents: `{tasks: [str] (1–4), model?, gt_mode?}` → 202 `{workers: [Session]}`. All of them or none: **429** when `MAX_CONCURRENT_CREATIONS`, `MAX_CONCURRENT_SESSIONS` or `MAX_WORKERS_PER_SESSION` cannot cover the whole set, and nothing is created. **409** on a worker (workers do not spawn workers) or a session that is `creating`/`closed`/`failed`; **400** on a `model` the provider will not serve; **422** on an empty, blank or over-long (>4) task list. |
| GET | `/api/sessions/:id/agents` | This session's workers, oldest first — `Session` objects, so `task`, `report`, `applied_at` and `status` come with them |
| POST | `/api/sessions/:id/agents/:worker/apply` | 3-way merge the worker's cumulative diff into this session's workspace → 200 `{worker_id, files, patch_sha256}`. **409** `{detail, conflicts: [paths]}` when it does not merge — and the workspace is then byte-for-byte what it was. **409** unless the session is `idle`; **400** when the worker changed nothing (a closed worker has no workspace left, so apply before closing); **404** when the worker is not this session's. |
| POST | `/api/sessions/:id/agents/:worker/close` | Close one worker → 200 `Session`. Exactly the same thing as `POST /api/sessions/:worker/close`. |
| GET | `/auth/login`, `/auth/callback`, `/auth/me`, `/auth/logout` | GitHub OAuth |
| GET | `/health` | Public liveness probe |

### Session

```
{id, status: creating|idle|running|failed|closed, repo, ref, model, gt_mode,
 gt_status: off|ready|unavailable|pending, gt_error, created_at, updated_at,
 last_message, turns, steps, cost, total_wall_seconds, gt_actions, current_turn_id,
 closed_reason: user|expired|failed|null,
 parent_id: str|null, role: primary|worker, task: str|null, applied_at: float|null,
 report: {finish_reason, reply_excerpt, patch_sha256, files_changed, applied}|null}
```

`parent_id`, `role`, `task`, `report` and `applied_at` are the worker-agent
fields — see [Worker agents](#worker-agents). On a session a user created they
are `null` / `"primary"`.

`gt_error` is the reason GT is unavailable, in the indexer's own words (e.g.
`RuntimeError: index status build_failed: nonzero_exit`), and `null` whenever
`gt_status` is not `unavailable`. It lives on the row, not only in the
`gt_unavailable` lifecycle event, so a client that reloads after the event
scrolled past can still say *why* a session is running without GT.

### `gt_mode`

`off | advisory | assistive | enforced`. These are members of
`gt_engine.gt_session.GTMode`, because `runner._install_gt` hands the value
straight to `GTMode(gt_mode)`; anything else is a **422 at creation**.

> **`engine` is gone.** It was documented here, offered by the UI and accepted
> by the API, and it was never a `GTMode` member. Every `engine` session built
> its index, published `gt_ready`, and then raised
> `ValueError: 'engine' is not a valid GTMode` on its first turn — the session
> flipped to `gt_status: unavailable` with the `ValueError` in `gt_error`, and
> `/graph` dropped from 551 GT edges to 156 import-only ones (HAR-84 G-02).
> Use `assistive` or `enforced` for what `engine` was meant to mean. `shadow`
> is a benchmark mode (it runs the engine without letting it affect the agent)
> and is deliberately not offered.

Any failure while installing GT for a turn still degrades rather than failing:
`gt_status` becomes `unavailable`, `gt_error` records why, and a
`lifecycle gt_unavailable {error}` event carries it live.

`stopped` is a lifecycle **event**, not a status: after a stop the reply is
written and the session goes straight back to `idle`.

`closed_reason` says *why* a session ended, which `closed` alone does not:
`user` (someone pressed close), `expired` (the idle TTL reaper), `failed` (a
clone, sandbox or agent failure — recorded when the session goes `failed`, and
kept if it is closed later). It is `null` while the session is alive.

`gt_actions` is how many GroundTruth **typed actions** this session has run,
summed over its turns — see [GroundTruth typed actions](#groundtruth-typed-actions).
It stays `0` for a `gt_mode: off` session.

`total_wall_seconds` is the sum of the finished turns' durations. Under
`MSWEA_COST_TRACKING=ignore_errors` — which the server sets, because LiteLLM
aborts a run it cannot price — `cost` is always `0.0`, so time is the only
honest budget line a session has.

### Budgets

A turn has two ceilings, both per turn and both checked at a step boundary:

| Budget | Set by | Ending |
|---|---|---|
| model calls | `step_limit` (1–500, default 60) | `step_limit` |
| wall clock | `wall_seconds` (60–3600), default `TURN_WALL_SECONDS` (900) | `time_limit` |

Two more endings are not budgets at all:

* **`error`** — the turn failed and the *session survives*. A provider failure,
  a sandbox that could not be recreated, a workspace over its quota or a bug in
  one turn ends that turn with `finish_reason: "error"`, an `agent_error` event,
  a reply that says what happened, a receipt, and the session back to `idle`.
  Only a failed **workspace creation** fails a session now (HAR-84 G-04).
* **`interrupted`** — the server restarted while the turn was running.
  `recover()` closes the receipt, publishes `turn_finished
  {finish_reason: "interrupted"}` and a `system_note` carrying *"Server
  restarted; turn interrupted"*, then `lifecycle idle` (HAR-84 G-08).

A step is not a unit of time: one `pytest -x` is one step and ten minutes. When
the wall deadline passes with a command still running, the command is killed
first — the same interrupt `/stop` uses, leaving a returncode-137 observation —
so the loop reaches its next boundary immediately instead of after the command
finally returns. Both endings write a reply that says where the agent got to
and invite `continue`; the session goes back to `idle` and the transcript is
intact, so the next turn resumes with a fresh budget.

### Idle sessions

A session holds a full repo clone (and, under `SANDBOX_MODE=docker`, a
container). `SESSION_IDLE_TTL_SECONDS` (default 21600 = 6 h, `0` disables)
bounds that: every `SESSION_REAP_INTERVAL_SECONDS` (default 300) a background
task closes every session that has been `idle` — never `running`, never
`creating` — for longer than the TTL, through exactly the `/close` path, and
records `closed_reason: "expired"`. `recover()` runs one pass at startup, so a
server that was down for a week does not come back holding the disk. `updated_at`
moves on every message, turn end and stop, so the TTL measures idleness rather
than age.

### Events

Frames are `id: N` / `event: <type>` / `data: {"id","type","timestamp","data"}`,
plus a `: ping` comment heartbeat every 15s.

| Type | `data` |
|---|---|
| `lifecycle` | `{status, ...}` — `creating`, `cloning`, `sandbox_starting`, `sandbox_ready{container, image, image_digest}`, `sandbox_failed{error}`, `sandbox_restarted{container}`, `indexing`, `gt_ready`, `gt_unavailable{error}`, `idle`, `running`, `stopped`, `quota_exceeded{reason}`, `diff_snapshots_disabled{reason}`, `failed{error}`, `closed{reason: user\|expired}` |
| `turn_started` | `{turn_id, message_id, role: "user", content}` — `content` is the user's message text. It is on the event so *every* subscriber can render the prompt; with only `message_id` a second tab showed the turn and the reply but never the question (HAR-84 G-09). |
| `assistant` | `{turn_id, content, actions[], step, n_calls, cost, is_reply?}` — one per model call. `is_reply: true` marks the text-only response that *ends* the turn: it has no `actions`, and it is emitted just before `agent_reply` so a client counting `assistant` frames always matches `turn_finished.n_calls` instead of trailing it by one. The field is absent on every other frame. |
| `tool_call` | `{turn_id, command, step, n_calls}` |
| `tool_result` | `{turn_id, command, output (≤4000 chars), returncode, is_error, step}` |
| `gt_action` | `{turn_id, step, kind, arguments, scope[], returncode, semantics, coverage, match_count, omissions[], reason_codes[], duration_ms, evidence_artifact_id?}` — one per GroundTruth **typed action**. A typed action is not a shell command, so it has no `tool_call`/`tool_result` pair. See [GroundTruth typed actions](#groundtruth-typed-actions). |
| `steering` | `{turn_id, message_id, content}` |
| `agent_reply` | `{turn_id, message_id, content, finish_reason, n_calls, cost, patch_sha256, files_changed}` |
| `turn_finished` | `{turn_id, finish_reason, n_calls, cost, patch_sha256, files_changed}` — `finish_reason` is one of `reply`, `question`, `step_limit`, `time_limit`, `stopped`, `submitted`, `error`, `interrupted`. The `interrupted` one comes from `recover()` and carries no patch fields. |
| `agent_error` | `{turn_id?, error}` — named `agent_error`, never `error`, which collides with `EventSource`'s native error event |
| `system_note` | `{turn_id?, message_id, content}` — a message from the *server*, not the agent: *"Server restarted; turn interrupted"* (from `recover()`, alongside that turn's `turn_finished {finish_reason: "interrupted"}`), the answer to a `/spawn` message, and *"applied worker &lt;id&gt;: N files"*. |
| `agent_spawned` | `{worker_id, task}` — on the **parent's** stream, one per worker, as it is created |
| `agent_report` | `{worker_id, message_id, finish_reason, content, patch_sha256, files_changed, n_calls, cost}` — on the parent's stream when a worker's turn ends. `content` is the worker's whole reply; the same text is in the parent's `messages` as a `role: "agent"` message with `meta.agent_id`. |
| `agent_applied` | `{worker_id, files, patch_sha256}` — a worker's patch landed in the parent's workspace |
| `agent_closed` | `{worker_id, reason}` — a worker closed, whether by its own `/close`, the parent's, or the idle reaper |

### GroundTruth typed actions

With `gt_mode` on, the model gets a second tool beside `bash`: `groundtruth`,
whose calls are **typed actions** (`exact_literal_search`, `find_callers`,
`why_this_edge`, …). A typed action is answered by a deterministic producer,
not by a shell: `gt_engine.miniswe_runtime.install_runtime_hooks` replaces
`agent.execute_actions` and dispatches the typed branch through
`execute_typed_action_fail_open`, which **never** calls `env.execute`. That is
why a typed action produces no `tool_call`/`tool_result` pair — the environment
proxy those come from is not on its path — and why the trail used to show a
model call with nothing under it.

Each one now emits exactly one `gt_action` frame instead:

```json
{"turn_id": "…", "step": 2, "kind": "exact_literal_search",
 "arguments": {"literal": "class Command", "paths": ["src/click"]},
 "scope": ["src/click"], "returncode": 0,
 "semantics": "exact", "coverage": "complete", "match_count": 2,
 "omissions": [], "reason_codes": ["EXACT_COMPLETE_EQUIVALENCE"],
 "duration_ms": 41.2, "evidence_artifact_id": "call_ab12"}
```

renderable as
`⏺ GroundTruth(exact_literal_search "class Command" in src/click) ⎿ 2 matches · exact · complete`.

| Field | Meaning |
|---|---|
| `step` | the model call that asked for it — the same `step` its `assistant` frame carries. A typed action is **part of** that call, so it never adds an `assistant` frame of its own. |
| `arguments` | the action's arguments *as dispatched*, i.e. after `typed_scopes` made a glob scope concrete. Every string is truncated to 200 characters. |
| `scope` | what the producer says it **actually searched**, echoed from `answer["scope"]` (or `coverage["scope"]` on the compatibility producer). Empty when the producer did not say — a request is not evidence of coverage, so the requested paths are never echoed back here. They are in `arguments`. |
| `semantics` / `coverage` | `exact`/`incomplete` and `complete`/`partial`, from `gt.evidence_artifact.v1`. `coverage` is `""` when the producer put a mapping there instead of a verdict. |
| `match_count` | rows in the answer, counted the way GT counts its own `returned_count`. |
| `omissions` | why the evidence is not complete (`missing_scope:src/click/**`, `capability_disabled`, `query_result_byte_limit`, …), capped at 10. |
| `reason_codes` | from `gt.interception_decision.v1` — `EXACT_COMPLETE_EQUIVALENCE` on an answer, `SEMANTICS_NOT_EXACT`/`COVERAGE_NOT_COMPLETE`/`EVIDENCE_HAS_OMISSIONS` on an abstention. |
| `duration_ms` | wall clock of the action batch this action belonged to. A model call almost always carries one action, in which case it is that action's own time. |
| `evidence_artifact_id` | the evidence artifact's `action_id`, or the compiled observation's sha256 when it has none. Absent when neither is available. |

The frame is emitted after the action ran and **before the next model call**,
from `cloud/server/gt_events.py`. Nothing under `gt_engine/` is modified: the
seam is `model.format_observation_messages(message, outputs, …)`, which GT's
own `execute_actions` calls once at the end of the batch with the normalised
action requests on one side and their results on the other. Wrapping that
rather than `execute_typed_action_fail_open` also covers the three answers GT
synthesises without calling the router at all (`query_fanout_refused`,
`capability_disabled`, `query_turn_budget_exceeded`).

A frame that cannot be built never breaks the turn: the emitter swallows its
own failures, and the observation still reaches the model unchanged.

### Receipts

`GET /api/sessions/:id/receipts` — one per turn, oldest first:

```
{turn_id, started_at, finished_at, n_calls, cost, wall_seconds,
 gt_actions, gt_exact_matches, finish_reason, patch_sha256, gt_status, model}
```

`gt_actions` counts the `gt_action` frames that turn emitted.
`gt_exact_matches` counts the subset that actually **answered** —
`semantics == "exact"` *and* `match_count > 0`. An exact abstention over an
empty scope is a GT action, not an answer, so the two numbers together say
whether GT was used *and* whether it paid. The session row carries the
`gt_actions` total.

## Worker agents

A session can spawn **worker agents** and watch them work: one task in, one
child session out, each with its own clone, sandbox, transcript and receipts.

### The model

A worker **is a session**. It carries its parent's `repo`, `ref`, `model`,
`gt_mode` and per-session knobs (`step_limit`, `wall_seconds`, `temperature`),
plus `parent_id`, `role: "worker"` and the `task` it was spawned with. That
task is its opening message: the first turn starts by itself the moment the
clone is `idle` — spawning and sending are one action, not two calls with a
poll in between. `POST /api/sessions` takes the same path through
`first_message`.

Workers are autonomous but not disposable. When a worker's turn ends — with
**any** `finish_reason` — it *reports* to its parent and goes back to `idle`.
It is a normal session while it sits there: `POST
/api/sessions/:worker/messages` gives it another turn (and another report),
`/diff`, `/tree`, `/graph`, `/receipts` and `/events` all work on it, and the
idle-session reaper treats it like anything else. Closing the parent closes
every live worker first.

> A worker cannot spawn workers (409). One level keeps the parent's stream the
> only stream a client has to watch.

### Spawning

```bash
curl -s -X POST $API/sessions/$ID/agents -H "$AUTH" \
     -H 'Content-Type: application/json' \
     -d '{"tasks":["port the parser to the new AST","write the tests for it"]}'
```

1–4 tasks, one worker each, **all of them or none**: the creation slots for the
whole set are taken up front, and if `MAX_CONCURRENT_CREATIONS`,
`MAX_CONCURRENT_SESSIONS` or `MAX_WORKERS_PER_SESSION` (default 4 live workers
per session) cannot cover it the call is a 429 and nothing is created. `model`
and `gt_mode` may be overridden per spawn; a `model` the provider will not
serve is a 400 from the same preflight session creation uses.

> **The defaults are smaller than four.** `MAX_CONCURRENT_CREATIONS` and
> `MAX_CONCURRENT_SESSIONS` are both 3, so a four-task spawn is a 429 out of
> the box. Raise both to `1 + MAX_WORKERS_PER_SESSION` (5) if you want a
> session and a full set of workers all working at once.

The chat box does it too. A message to a primary session whose **first
non-blank line** is `/spawn <task>` is the spawn call: no turn is started, no
model sees it, and the 202 comes back as `{message, delivery: "spawned"}` where
`message` is the server's `system_note` listing the new workers.

```
/spawn port the parser to the new AST
/spawn write the tests for it
```

Every non-blank line has to be a `/spawn` line: `/spawn fix it` followed by
prose is a **400**, not a turn that quietly runs the word `/spawn` past a
model. A message that merely mentions `/spawn` later on is an ordinary turn.

### Watching them: one stream

The parent's `/events` stream carries everything:

* `agent_spawned` when each worker is created,
* the worker's own `turn_started`, `assistant`, `tool_call`, `tool_result`,
  `gt_action` and `turn_finished` frames, **mirrored** onto the parent's stream
  with an extra `agent_id: "<worker id>"` field — so a graph can draw each
  worker's trail in its own colour from one subscription,
* `agent_report` when a worker's turn ends,
* `agent_applied` and `agent_closed`.

`agent_id` is the whole protocol: a frame that has it belongs to that worker, a
frame without it is the primary session's own. It is **absent** (not `null`) on
primary-session frames, and only the six frame types above are mirrored —
`lifecycle`, `agent_reply`, `agent_error`, `steering` and `system_note` stay on
the worker's own stream. Mirrored frames are re-published, so they have their
own event ids on the parent's stream; the worker's `/events` still has the
originals, and the two id spaces are unrelated.

A stream is not a record: the report is also written into the parent's
`messages` as a `role: "agent"` message with `meta.agent_id`,
`meta.finish_reason`, `meta.patch_sha256` and `meta.files_changed`, so a reload
shows it, and onto the worker's own row as `report`.

### Taking the work

```bash
curl -s -X POST $API/sessions/$ID/agents/$WORKER/apply -H "$AUTH"
```

This 3-way merges the worker's cumulative diff (the same patch its `/diff`
serves) into the **parent's** workspace, which must be `idle`. On success the
parent's own `/diff` contains the worker's files, an `agent_applied` event and
a `system_note` *"applied worker &lt;id&gt;: N files"* land on its stream, and
the worker row keeps `applied_at` + `applied_sha256` (and `report.applied`
becomes `true`).

On conflict the answer is **409 `{detail, conflicts: ["path", ...]}`** and the
parent's workspace is untouched — no conflict markers, no half-applied tree.
Both halves of that are deliberate:

* `git apply --3way` implies `--index`, and after `compute_diff`'s `add -N`
  every file the session has edited differs from the index, so the parent's own
  work is fully staged first and the index is put back (`reset` + `add -N`)
  afterwards. Neither step touches a file on disk.
* `git apply --3way --check` exits **0** for a patch it would only apply *with
  conflict markers*, so the pre-apply tree is recorded with `write-tree` and
  restored with `read-tree -u --reset` if the real apply still conflicts.

Applying does not tell the parent's *agent* anything — the system note is for
the human. If the parent should reason about the merged code, say so in the
next message.

## Architecture

```
Browser (React) ←→ FastAPI ←→ ConversationalAgent (mini-SWE) + GT engine
                      ↕                    ↕
                   SQLite         one repo clone per session
```

- `conversational_agent.py` — `DefaultAgent` subclass: one transcript across
  many turns, text-only responses treated as replies, steering/stop at step
  boundaries, per-turn step and wall-clock budgets, observation truncation past
  `MAX_CONTEXT_CHARS`.
- `runner.py` — `SessionManager`: workspaces, turn scheduling under a per-session
  lock, receipts, diffs, restart recovery, the idle-session TTL reaper, and
  worker agents (spawn, report, mirror, apply, cascading close).
- `gt_events.py` — GroundTruth typed actions as `gt_action` frames: the wrapper
  the runner installs after `install_runtime_hooks`, and the payload builder.
- `typed_scopes.py` — makes a planner's glob scope (`src/click/**`) concrete
  before the deterministic producer stats it as a literal path (HAR-85).
- `store.py` — SQLite: `sessions`, `messages`, `turns`, `events`,
  `diff_snapshots`. The schema is drop-and-recreate on version change (dev
  tool).
- `prompts.py` — chat system prompt + session brief, derived from mini-SWE's
  `mini.yaml` action format.
- `environment.py` — `CloudLocalEnvironment`: credential-scrubbed, real bash.
- `sandbox.py` — `DockerSandboxEnvironment` and the sandbox lifecycle: one
  container per session, an internal network, an allow-listed egress proxy.

### Per-step diffs

The turn worker takes a real `git diff` right after every command that looks
like a write (`workspace.looks_like_write`, the regex ported from the UI's
`trail.ts` — **the two must stay in sync**, and a test compares them), keyed by
the id of the `tool_result` event it followed. That is what
`/diff?through_event=N` serves, so "the tree at step N" is a recorded fact
rather than something the client reconstructs from the files a step touched.

Snapshots are a convenience and never a tax on the agent: the patch text is
capped at 512 KB (`truncated: true` past that, and the per-file bodies are
dropped), and if a single `compute_diff` takes longer than 2 s the rest of that
turn runs without snapshots and a `lifecycle {status:
"diff_snapshots_disabled", reason}` event says so.

## Sandboxing (`SANDBOX_MODE`)

`SANDBOX_MODE=local` (the default) runs agent commands in the server process's
own machine account, as before. `SANDBOX_MODE=docker` gives every session its
own container:

- `gt-sandbox-<session_id>`, started right after the clone and removed on
  `close()`; orphans are reaped at startup. The workspace is **bind-mounted** at
  `/workspace`, so the server keeps writing `.gt_state/` and indexing the same
  files the agent edits.
- uid 1000 (`agent`), `--memory 2g --cpus 2 --pids-limit 512`, tmpfs `/tmp`,
  `no-new-privileges`, `--cap-drop ALL`, and **no Docker socket**.
- Egress: the sandbox network is `--internal` (no route off-host, no external
  DNS). The only way out is the `gt-egress-proxy` container, which serves
  github.com/\*.github.com/codeload/objects.githubusercontent.com, plus
  pypi/files.pythonhosted/registry.npmjs while `SANDBOX_ALLOW_REGISTRIES=1`.
  Everything else gets 403 — **including the model API**, which the server
  calls, not the sandbox.
- Fail closed: a sandbox that will not start fails the session. There is no
  silent fallback to local execution.

Build the images and run it:

```bash
export BUILD_SHA="$(git rev-parse --short HEAD)"
docker compose -f cloud/docker-compose.yml up -d --build
docker compose -f cloud/docker-compose.yml --profile build build sandbox-image
```

`--build` is not optional. Without it compose reuses whatever image is already
tagged, which is how a round of QA ended up testing a UI two commits behind the
server. `bash cloud/deploy.sh` does all of the above and then prints the commit,
the served bundle name and `/health` — use it instead of typing the commands.

Lifecycle events: `sandbox_starting`, `sandbox_ready{container, image,
image_digest}`, `sandbox_failed{error}`. Full design, commands and evidence:
[docs/cloud-sandbox.md](../docs/cloud-sandbox.md).

## Deploy to a Codespace

The internal deployment runs on GitHub Codespaces: no infrastructure, and a
public HTTPS origin with a certificate for free. Rationale, cost and the plain-VM
differences: [docs/cloud-vm-substrate.md](../docs/cloud-vm-substrate.md).

**1. Create the codespace on this branch.** `.devcontainer/devcontainer.json`
asks for 4 cores, docker-in-docker (the server needs a Docker daemon to run
sandboxes) and `forwardPorts: [80, 8000]`.

```bash
gh codespace create -R harneet2512/gt-harness -b cloud/internal-harness    -m standardLinux32gb
NAME=$(gh codespace list --json name,repository -q '.[0].name')
gh codespace ssh -c "$NAME"
```

**2. Write `cloud/.env`** inside the codespace. Same keys as
`cloud/.env.example`, with two that matter here:

```bash
UI_ORIGIN=/                       # the UI is same-origin behind nginx
WORKSPACES_HOST_DIR=/srv/gt-workspaces
```

Leave `CORS_ORIGINS` empty — cross-origin is exactly what port 80 avoids.

**3. Bring the stack up.**

```bash
bash cloud/deploy.sh --sandbox      # first deploy: build the sandbox image too
```

`cloud/deploy.sh` pulls (`--no-pull` skips it, for a dirty tree), rebuilds with
`BUILD_SHA` stamped into both images, restarts, and then prints the commit, the
served JS bundle name and `GET /health`. Later deploys are just
`bash cloud/deploy.sh` — add `--sandbox` whenever `cloud/sandbox/Dockerfile`
changes. By hand it is:

```bash
export BUILD_SHA="$(git rev-parse --short HEAD)"
docker compose -f cloud/docker-compose.yml up -d --build
docker compose -f cloud/docker-compose.yml --profile build build sandbox-image
curl -s localhost/health         # {"status":"ok","commit":"<sha>"}
```

The commit is the check that matters: `/health`'s `commit`, the `build <sha>`
line on the sign-in card and in the session switcher, and the `SYNAPSE ui build
<sha>` line the SPA logs on boot must all agree. If the bundle filename under
`/usr/share/nginx/html/assets/` did not change after a UI edit, the browser or
compose is serving a stale image.

`ui` (nginx) listens on **80** and proxies `/api`, `/auth` and `/health` to
`server:8000`; nothing but port 80 needs to be reachable.

**4. Make port 80 public.**

```bash
gh codespace ports visibility 80:public -c "$NAME"
gh codespace ports -c "$NAME"          # confirm it is listed
```

If `https://$NAME-80.app.github.dev` returns a **404 with an empty body**, that
is the Codespaces edge saying the port is not registered, not the app failing:
a codespace only auto-registers ports it observes, and a stack started over SSH
with no VS Code client attached is not observed. `forwardPorts` in the
devcontainer fixes it at creation; for a codespace already running, hold a
tunnel from your workstation as an interim —

```bash
gh codespace ports forward 80:18080 -c "$NAME" &   # then use localhost:18080
```

— and expect it to drop (`websocket: close 1006`) on a flaky link.

**5. Point the OAuth App at that origin.** In
[GitHub → Developer settings → OAuth Apps](https://github.com/settings/developers):

| Field | Value |
|-------|-------|
| Homepage URL | `https://<codespace-name>-80.app.github.dev` |
| Authorization callback URL | `https://<codespace-name>-80.app.github.dev/auth/callback` |

The callback host must match the forwarded origin exactly. A codespace rebuilt
under a new name gets a new hostname, so the OAuth App has to be updated with
it — one more reason a plain VM with a stable DNS name is the end state.
Restrict who can log in with `ALLOWED_GITHUB_LOGINS`.

**6. Open it**, sign in with GitHub, and start a session. Stop the codespace
when you are done (`gh codespace stop -c "$NAME"`): compute billing stops,
storage does not.

## Known Limitations

- `SANDBOX_MODE=local` (the default) has no container isolation — the agent
  runs shell commands in the server process's machine account. Set
  `SANDBOX_MODE=docker` for per-session isolation and the egress policy.
- Single server, SQLite, no horizontal scaling.
- Sessions found `running` after a restart become `idle`, the receipt is closed
  with `finish_reason: "interrupted"`, and a `system_note` event carries the
  note; the interrupted turn is **not resumed**.
- `/stop` is honoured at a step boundary. While a command is running it lands in
  well under a second (the command is killed); while a *model call* is in
  flight it waits for that call, because the LiteLLM call is synchronous and not
  cancellable. `MODEL_REQUEST_TIMEOUT` is the only bound on that worst case.
- `SANDBOX_WORKSPACE_MAX_MB` is a watermark checked between commands, not a
  kernel quota: a single write larger than the cap still lands on disk and is
  caught immediately afterwards.
- A worker's patch can only be applied while its workspace still exists, so
  `apply` before `close` — closing a worker deletes its clone like any other
  session's. Applying is also not transactional against the parent's *agent*: it
  changes files, not the transcript.
- Workers are one level deep (a worker cannot spawn workers), and `apply` merges
  one worker at a time; applying two workers that touched the same lines gives
  the second one a 409.
- GT evidence artifacts (`gt.evidence_artifact.v1`) are not persisted into the
  session transcript, so GT's contribution is visible in `/graph` but not
  receipted in the trajectory.
- GT features require the gt-index binary and the groundtruth-mcp wheel;
  without them a session degrades to `gt_status: unavailable` and runs plain.


> **Codespaces port visibility resets.** Every time the compose containers are
> recreated (any deploy), the tunnel re-registers ports 80/8000 as *private* and
> the public URL 302s to GitHub sign-in. After each deploy run
> `gh codespace ports visibility 80:public -c <codespace-name>` from a machine
> with a `codespace`-scoped `gh` login (the codespace's own token lacks it).

## Documentation

The full as-built documentation lives in [`docs/cloud/`](../docs/cloud/):

| Document | What is in it |
|---|---|
| [Index and overview](../docs/cloud/README.md) | What the product is, the thesis, and a map of these docs. |
| [Architecture](../docs/cloud/architecture.md) | Components and boundaries, the session state machine, the turn loop, the event bus, diff snapshots, the file graph, worker agents, GroundTruth, concurrency. |
| [User guide](../docs/cloud/user-guide.md) | Signing in, the prompt-first landing, steering, stop, workers, the graph, receipts, GT modes, slash commands and keys. |
| [API reference](../docs/cloud/api.md) | Every REST endpoint and status code, every SSE event and payload field, the schemas, and the auth rules. |
| [Operations](../docs/cloud/operations.md) | Prerequisites, the OAuth app, every environment variable, `deploy.sh`, Codespaces specifics, images, restart policy, the reaper, quotas, logs, verification and recovery. |
| [Security](../docs/cloud/security.md) | The threat model as built: what is isolated, what is not, secrets, egress, resource caps, authorisation and the known gaps. |
| [Testing and CI](../docs/cloud/testing-and-ci.md) | Every suite and what it covers, how to run them, what skips, the CI workflow, live verification and the QA rounds. |
| [Decisions](../docs/cloud/decisions.md) | The dated decision log, with the commit that carries each one. |
| [Changelog](../docs/cloud/changelog.md) | Every commit on this branch, grouped by day. |
| [Known limitations](../docs/cloud/known-limitations.md) | Everything deferred or not done, with the reason and where it lives. |
