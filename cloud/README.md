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
| POST | `/api/sessions` | Create a session (`repo`, `ref`, `model`, `gt_mode`, `step_limit`, `wall_seconds` (60–3600, default `TURN_WALL_SECONDS`), `temperature`) → 201 `Session`. 400 on a non-GitHub URL. |
| GET | `/api/sessions` | List sessions, newest first |
| GET | `/api/sessions/:id` | One session |
| GET | `/api/sessions/:id/messages` | Full conversation, in order |
| POST | `/api/sessions/:id/messages` | Send a message → 202 `{message, delivery}` where delivery is `turn_started` or `queued_for_running_turn`. 409 while `creating`/`closed`/`failed`. |
| GET | `/api/sessions/:id/events` | SSE stream, open across turns (`?after_id=` or `Last-Event-ID:`) |
| GET | `/api/sessions/:id/diff` | Cumulative diff vs the cloned commit, incl. untracked files. With `?through_event=N` it returns the stored snapshot taken at the latest write **at or before** event `N` instead — same shape plus `{as_of_event, approximate: false}` (and `truncated: true` when the stored patch hit the 512 KB cap). `as_of_event: 0` means nothing had been written yet. |
| GET | `/api/sessions/:id/tree` | Every file in the workspace with its byte size (`{base_sha, files:[{path,size}]}`), for the map |
| GET | `/api/sessions/:id/graph` | File relation graph (`{base_sha, gt, nodes:[{id,path,size,lang,dir}], edges:[{source,target,kind,weight}]}`). `kind` is `import` (static imports) or `gt_call`/`gt_ref`/`gt_import` (GT symbol edges collapsed to file level, only when `gt_status` is `ready`; `gt` says whether they are in). Nodes are exactly the files `/tree` returns; over 5000 files only the busiest survive and `truncated: true` is added. |
| GET | `/api/sessions/:id/receipts` | One receipt per turn |
| POST | `/api/sessions/:id/stop` | Stop the running turn at the next step boundary → 202 (409 if idle) |
| POST | `/api/sessions/:id/close` | Kill the turn, delete the workspace, status `closed`, `closed_reason: "user"` (idempotent) |
| GET | `/auth/login`, `/auth/callback`, `/auth/me`, `/auth/logout` | GitHub OAuth |
| GET | `/health` | Public liveness probe |

### Session

```
{id, status: creating|idle|running|failed|closed, repo, ref, model, gt_mode,
 gt_status: off|ready|unavailable|pending, gt_error, created_at, updated_at,
 last_message, turns, steps, cost, total_wall_seconds, current_turn_id,
 closed_reason: user|expired|failed|null}
```

`gt_error` is the reason GT is unavailable, in the indexer's own words (e.g.
`RuntimeError: index status build_failed: nonzero_exit`), and `null` whenever
`gt_status` is not `unavailable`. It lives on the row, not only in the
`gt_unavailable` lifecycle event, so a client that reloads after the event
scrolled past can still say *why* a session is running without GT.

`stopped` is a lifecycle **event**, not a status: after a stop the reply is
written and the session goes straight back to `idle`.

`closed_reason` says *why* a session ended, which `closed` alone does not:
`user` (someone pressed close), `expired` (the idle TTL reaper), `failed` (a
clone, sandbox or agent failure — recorded when the session goes `failed`, and
kept if it is closed later). It is `null` while the session is alive.

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
| `lifecycle` | `{status, ...}` — `creating`, `cloning`, `sandbox_starting`, `sandbox_ready{container, image, image_digest}`, `sandbox_failed{error}`, `indexing`, `gt_ready`, `gt_unavailable{error}`, `idle`, `running`, `stopped`, `diff_snapshots_disabled{reason}`, `failed{error}`, `closed{reason: user\|expired}` |
| `turn_started` | `{turn_id, message_id}` |
| `assistant` | `{turn_id, content, actions[], step, n_calls, cost, is_reply?}` — one per model call. `is_reply: true` marks the text-only response that *ends* the turn: it has no `actions`, and it is emitted just before `agent_reply` so a client counting `assistant` frames always matches `turn_finished.n_calls` instead of trailing it by one. The field is absent on every other frame. |
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
  boundaries, per-turn step and wall-clock budgets, observation truncation past
  `MAX_CONTEXT_CHARS`.
- `runner.py` — `SessionManager`: workspaces, turn scheduling under a per-session
  lock, receipts, diffs, restart recovery, the idle-session TTL reaper.
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
- Sessions found `running` after a restart become `idle` with a system note; the
  interrupted turn is not resumed.
- GT features require the gt-index binary and the groundtruth-mcp wheel;
  without them a session degrades to `gt_status: unavailable` and runs plain.


> **Codespaces port visibility resets.** Every time the compose containers are
> recreated (any deploy), the tunnel re-registers ports 80/8000 as *private* and
> the public URL 302s to GitHub sign-in. After each deploy run
> `gh codespace ports visibility 80:public -c <codespace-name>` from a machine
> with a `codespace`-scoped `gh` login (the codespace's own token lacks it).
