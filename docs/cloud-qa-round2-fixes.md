# Cloud agent — round-2 QA fixes, measured (HAR-84)

Evidence for the three defects the round-2 QA report raised against the live
codespace, re-measured on that same codespace after the fix. Every number below
is from a real run against `https://github.com/pallets/click` with
`nvidia/nemotron-3-super-120b-a12b:free`, `gt_mode: advisory`, `step_limit 30`.
Secrets are never printed; the JWT used by the scripts is minted inside the
server container from `JWT_SECRET` and is not recorded here.

## Deployment under test

| | |
|---|---|
| Codespace | `gt-cloud-agent-…` (account redacted), repo at `/workspaces/gt-harness` |
| Branch / commit | `cloud/internal-harness` @ `8ce9500`, plus the uncommitted `cloud/**` changes for this issue |
| Deploy command | `bash cloud/deploy.sh --no-pull` (`--sandbox` on the first pass) |
| `GET /health` | `{"status":"ok","commit":"8ce9500"}` |
| Served UI bundle | `index-meuN_FTs.js` → `index-BCsBJUxp.js` → `index-ClspR7a7.js` |

The codespace was three commits behind (`a64fa59`) when this started; it was
fast-forwarded to `8ce9500` before the fix files were copied on.

**The sessions table was recreated.** `SCHEMA_VERSION` went to 4 in `1d08976a`
(per-step diff snapshots), which had never been deployed here. `SessionStore.init`
compares `PRAGMA user_version` and drops/recreates every table when it differs,
so the four pre-existing rows in the `db-data` volume were dropped on the first
start of the new server image. Expected, and the reason `diff_snapshots` exists
at all on this box.

## P0-1 — tool frames under GT

**Before:** every GT session persisted `assistant` 4–20 with `tool_call` 0 and
`tool_result` 0. `gt_engine/miniswe_runtime.py:968` replaces
`agent.execute_actions`, and the replacement calls `env.execute` itself, so
`ConversationalAgent.execute_actions` — where the frames were emitted — never
ran. Empty trail rows, no graph halo, no live diff.

**After:** emission moved to `_EmittingEnvironment`, a proxy around `env` owned
by the agent. Both callers go through it.

Session `c8c0fd32a3d3` — `gt_mode: advisory`, `gt_status: ready`, turn took 94 s:

```
agent_reply: 1     assistant: 24      lifecycle: 10
tool_call: 23      tool_result: 23    turn_started: 1    turn_finished: 1
```

An earlier run on the same build (`04dd1840c605`, 151 s) gave `assistant 27`,
`tool_call 26`, `tool_result 26`. **`tool_call == tool_result`, both non-zero,
in every GT run.**

### Per-step diff snapshots

`_snapshot_diff` keys off `tool_result` event ids, so it was starved by the same
bug. `diff_snapshots` after the run:

```
event_id  patch bytes
151       0        (pip install pytest — matched the write regex, tree unchanged)
154       599      src/click/core.py
163       599
169       599
```

`GET /diff?through_event=154` returns a 599-byte patch touching
`src/click/core.py`, mid-turn — the turn ran on to event 172. The live
`GET /diff` agrees.

One extra fix was needed to get there. The model edited the file with
`cat > /tmp/replace.sed <<'EOF'` + `sed -i -f`, and in the first run with
`python3 -c "…open(…,'w')…"`. `looks_like_write` had no case for `python -c`, so
that write produced no snapshot and no edit tick. `python3?\s+-c\b` was added to
the regex in `cloud/server/workspace.py` **and** its verbatim twin in
`cloud/ui/src/trail.ts` (the two are compared literally by
`tests/test_cloud_workspace.py`).

## P2-4 — stop interrupts the running command

**Before:** ~18.5 s. The stop was honoured only at the step boundary, so a long
command ran to completion first.

**After:** `request_stop()` calls `env.interrupt()` through the proxy.
`CloudLocalEnvironment` kills the tracked process group; `DockerSandboxEnvironment`
reuses the timeout path's `docker exec -u 0 <cid> pkill -KILL -u 1000`. The
killed command returns `{"returncode": 137, "exception_info": "interrupted by
user stop"}` and the loop reaches the boundary immediately.

Session `13f135b23715`, prompt `Run sleep 120 and then tell me you are done.`
The agent issued `sleep 120 && echo "I am done."`; `POST /stop` was sent as soon
as its `tool_call` appeared on the stream:

| from `POST /stop` | |
|---|---|
| `tool_result` `returncode: 137` | **+0.06 s** |
| `turn_finished` (`finish_reason: stopped`) | **+0.15 s** |
| `lifecycle {"status":"idle"}` | **+0.16 s** |

Reply: `Stopped.` Well under the 10 s bar, down from ~18.5 s.

The UI now says so too: `useSessionData.stop()` sets a pending flag the moment
the button is pressed and `StatusLine` reads "Stopping…" until `turn_finished`.

## P0-0 — deploy hygiene

`docker compose up -d` had reused a stale `cloud-ui` image and served a SPA two
commits behind the server, with nothing in the artefacts naming a commit.

- `cloud/deploy.sh` — `git pull --ff-only` (skippable with `--no-pull` for a
  dirty tree), `up -d --build`, optional `--profile build build sandbox-image`
  via `--sandbox`, then prints the commit, the served bundle name and `/health`,
  retrying `/health` for up to 60 s and warning when it does not report the SHA
  just built.
- `BUILD_SHA` is a build arg on both images (`${BUILD_SHA:-unknown}` /
  `${BUILD_SHA:-dev}` in compose). Server: `ENV BUILD_SHA` → `/health` returns
  `{"status":"ok","commit":"<sha>"}`. UI: vite `define: { __BUILD_SHA__ }` →
  shown small on the sign-in card and in the session-switcher footer, and logged
  once on boot as `SYNAPSE ui build <sha>`.
- `cloud/README.md` and `cloud/sandbox/verify.sh` document
  `docker compose -f cloud/docker-compose.yml up -d --build` (never without
  `--build`), and `verify.sh` now prints `/health` before it starts so its log
  says which build it verified.

Both deploys on the codespace changed the bundle hash and reported the matching
commit, which is the check that was missing.

## P2-9 / P2-10

- **P2-9.** The composer hint was bound to `isRunning` and stayed for the rest
  of the turn. It is now bound to a set of queued-but-undelivered message ids:
  a message accepted as `queued_for_running_turn` is added, the `steering` frame
  for that `message_id` removes it, and `turn_finished` clears the set.
- **P2-10.** `.bar` and `.legend` wrap instead of clipping (`height: 36px` +
  `overflow: hidden` + a mask gradient are gone, `min-height: 36px` and
  `flex-wrap: wrap` replace them). `CO-TOUCH` can no longer be sliced.

## Sandbox regression

`bash cloud/sandbox/verify.sh off` — session `fe10ce1e0231`, passing end to end:

- `SANDBOX.txt` written in the container, read on the host at
  `/srv/gt-workspaces/<id>/SANDBOX.txt`: `uid=1000(agent) gid=1000(agent)`,
  hostname `740d5ac61c4c`.
- Egress: `curl -sI https://openrouter.ai` → `403` with
  `X-Egress-Policy: host is not on the egress allow-list`;
  `curl -sI https://github.com` → `200`. Proxy log shows
  `DENY CONNECT openrouter.ai:443` and `ALLOW CONNECT github.com:443`.
- On close the sandbox container and `/srv/gt-workspaces/<id>` are both gone.

All four sessions created for this run (`04dd1840c605`, `c8c0fd32a3d3`,
`2c851fc9515e`, `13f135b23715`) plus verify.sh's own were closed.

## Gates

`python -m ruff check cloud/ tests/test_cloud_*.py`,
`python -m pytest tests/test_cloud_*.py -q -p no:warnings`, and in `cloud/ui`
`npx tsc --noEmit && npm run build`.
