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
  Other endings: `stopped`, `step_limit` (per-turn budget), `error`, and the
  legacy mini-SWE submit marker (`submitted`).
- The workspace lives for the life of the session under `WORKSPACES_DIR`
  (default `./workspaces/<session_id>`) and is removed on `close`. The
  cumulative diff is available at any time.

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
| POST | `/api/sessions` | Create a session (`repo`, `ref`, `model`, `gt_mode`, `step_limit`, `temperature`) → 201 `Session`. 400 on a non-GitHub URL. |
| GET | `/api/sessions` | List sessions, newest first |
| GET | `/api/sessions/:id` | One session |
| GET | `/api/sessions/:id/messages` | Full conversation, in order |
| POST | `/api/sessions/:id/messages` | Send a message → 202 `{message, delivery}` where delivery is `turn_started` or `queued_for_running_turn`. 409 while `creating`/`closed`/`failed`. |
| GET | `/api/sessions/:id/events` | SSE stream, open across turns (`?after_id=` or `Last-Event-ID:`) |
| GET | `/api/sessions/:id/diff` | Cumulative diff vs the cloned commit, incl. untracked files |
| GET | `/api/sessions/:id/tree` | Every file in the workspace with its byte size (`{base_sha, files:[{path,size}]}`), for the map |
| GET | `/api/sessions/:id/graph` | File relation graph (`{base_sha, gt, nodes:[{id,path,size,lang,dir}], edges:[{source,target,kind,weight}]}`). `kind` is `import` (static imports) or `gt_call`/`gt_ref`/`gt_import` (GT symbol edges collapsed to file level, only when `gt_status` is `ready`; `gt` says whether they are in). Nodes are exactly the files `/tree` returns; over 5000 files only the busiest survive and `truncated: true` is added. |
| GET | `/api/sessions/:id/receipts` | One receipt per turn |
| POST | `/api/sessions/:id/stop` | Stop the running turn at the next step boundary → 202 (409 if idle) |
| POST | `/api/sessions/:id/close` | Kill the turn, delete the workspace, status `closed` (idempotent) |
| GET | `/auth/login`, `/auth/callback`, `/auth/me`, `/auth/logout` | GitHub OAuth |
| GET | `/health` | Public liveness probe |

### Session

```
{id, status: creating|idle|running|failed|closed, repo, ref, model, gt_mode,
 gt_status: off|ready|unavailable|pending, created_at, updated_at,
 last_message, turns, steps, cost, current_turn_id}
```

`stopped` is a lifecycle **event**, not a status: after a stop the reply is
written and the session goes straight back to `idle`.

### Events

Frames are `id: N` / `event: <type>` / `data: {"id","type","timestamp","data"}`,
plus a `: ping` comment heartbeat every 15s.

| Type | `data` |
|---|---|
| `lifecycle` | `{status, ...}` — `creating`, `cloning`, `indexing`, `gt_ready`, `gt_unavailable{error}`, `idle`, `running`, `stopped`, `failed{error}`, `closed` |
| `turn_started` | `{turn_id, message_id}` |
| `assistant` | `{turn_id, content, actions[], step, n_calls, cost}` |
| `tool_call` | `{turn_id, command, step, n_calls}` |
| `tool_result` | `{turn_id, command, output (≤4000 chars), returncode, is_error, step}` |
| `steering` | `{turn_id, message_id, content}` |
| `agent_reply` | `{turn_id, message_id, content, finish_reason, n_calls, cost, patch_sha256, files_changed}` |
| `turn_finished` | `{turn_id, finish_reason, n_calls, cost, patch_sha256, files_changed}` |
| `agent_error` | `{turn_id?, error}` — named `agent_error`, never `error`, which collides with `EventSource`'s native error event |

## Architecture

```
Browser (React) ←→ FastAPI ←→ ConversationalAgent (mini-SWE) + GT engine
                      ↕                    ↕
                   SQLite         one repo clone per session
```

- `conversational_agent.py` — `DefaultAgent` subclass: one transcript across
  many turns, text-only responses treated as replies, steering/stop at step
  boundaries, observation truncation past `MAX_CONTEXT_CHARS`.
- `runner.py` — `SessionManager`: workspaces, turn scheduling under a per-session
  lock, receipts, diffs, restart recovery.
- `store.py` — SQLite: `sessions`, `messages`, `turns`, `events`. The schema is
  drop-and-recreate on version change (dev tool).
- `prompts.py` — chat system prompt + session brief, derived from mini-SWE's
  `mini.yaml` action format.
- `environment.py` — `CloudLocalEnvironment`: credential-scrubbed, real bash.

## Known Limitations

- No container isolation per session — the agent runs shell commands in the
  server process's machine account.
- Single server, SQLite, no horizontal scaling.
- Sessions found `running` after a restart become `idle` with a system note; the
  interrupted turn is not resumed.
- GT features require the gt-index binary and the groundtruth-mcp wheel;
  without them a session degrades to `gt_status: unavailable` and runs plain.
