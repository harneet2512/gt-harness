# HAR-84 — server/ops fixes, with before/after evidence

The end-to-end red-team audit of the cloud coding agent
(`AUDIT.md`, 2026-09-05, 62 rows / 41 PASS / 21 GAP) ranked 23 defects
`G-01`…`G-23`. This is what was changed and what the deployment did afterwards.

**Deployment under test** — codespace `gt-cloud-agent-wvrqp4rqpjp42gvp7`,
`https://gt-cloud-agent-wvrqp4rqpjp42gvp7-80.app.github.dev`, build `4ebf8db`
(`/health`), working tree = `cloud/internal-harness` @ `4ebf8dbe` plus the
changes below. Model `nvidia/nemotron-3-super-120b-a12b:free`, repo
`pallets/click`, `SANDBOX_MODE=docker`.

Every number below was produced on that box, after `bash cloud/deploy.sh
--no-pull --sandbox`. Secrets were never printed: the verification scripts ran
**inside the server container** and minted their own JWTs from the
`JWT_SECRET` the process already held, or ran on the host and read it from
`cloud/.env` without echoing it. Session ids are the deployment's own 12-hex
ids and carry nothing sensitive.

---

## P0

### G-01 · `server` and `ui` never came back after a daemon restart

*Before (observed again, unprompted, at the start of this session):*

```
cloud-ui-1       Exited (255) 17 seconds ago
cloud-server-1   Exited (255) 17 seconds ago
gt-egress-proxy  Up 16 seconds
cloud-server-1  restart=no          health=none
cloud-ui-1      restart=no          health=none
gt-egress-proxy restart=unless-stopped health=none
```

The public URL served `502 Bad Gateway` until `docker compose up -d` was run by
hand. The one service with a restart policy was the one that came back.

*Change* — `cloud/docker-compose.yml`: `restart: unless-stopped` on `server`
and `ui`; a healthcheck on all three (`server`: `curl -fsS
http://127.0.0.1:8000/health`; `ui`: busybox `wget` on nginx's root;
`egress-proxy`: a TCP connect to 3128); `ui` `depends_on: server: condition:
service_healthy`. Sandboxes get `--restart unless-stopped` in `run_argv`.

*After* — the docker **daemon** was killed (`sudo pkill -x dockerd`, confirmed
gone) and restarted. **Nothing was run by hand afterwards:**

```
=== dockerd absent ===
=== starting dockerd again ===
dockerd up
gt-sandbox-be0e5333ef5f  Up 34 seconds
cloud-server-1           Up 34 seconds (healthy)
gt-sandbox-f12ddbe12824  Up 34 seconds
cloud-ui-1               Up 34 seconds (healthy)
gt-egress-proxy          Up 34 seconds (healthy)

public /health after daemon restart: 200
```

`tests/test_cloud_compose.py` asserts the policy and the healthchecks so a
compose edit cannot quietly undo it.

### G-02 · `gt_mode: engine` was broken — the flagship mode degraded on every turn

*Before:* `engine` → `201`, `gt_ready` at 24.9 s, then
`ValueError: 'engine' is not a valid GTMode` on the first turn, `gt_status:
unavailable`, `/graph` down from 551 GT edges to 156 import-only ones.
`"engine"` was **never** a member of `gt_engine.gt_session.GTMode`
(`off|shadow|advisory|assistive|enforced`), which `runner._install_gt` passes
the value to verbatim.

*Change* — `gt_mode` is `Literal["off","advisory","assistive","enforced"]` on
`SessionCreate`. `_install_gt` still passes it straight through, so what the
API accepts is exactly what `GTMode()` accepts. Documented in
`cloud/README.md` (`### gt_mode`) and in `models.py`. Errors while installing
GT for a turn still degrade (`gt_status: unavailable` + `gt_error` +
`lifecycle gt_unavailable`) — that was already right and is unchanged.

*After (live):*

```json
{"engine": 422, "banana": 422, "shadow": 422, "(empty)": 422}
```

`assistive` and `enforced` sessions: see **GT modes** below.

### G-03 · A forking workload permanently bricked a session

*Before:* 600 forks hit `--pids-limit 512`; the orphans were reparented to pid
1 (`sleep infinity`), which never reaps, so **every** later `docker exec`
returned rc 128 `OCI runtime exec failed: … nsexec: unable to spawn stage-1`.
The raw runc string was pasted into the transcript as three ordinary tool
results and the session stayed `idle` — usable-looking, unusable. An ordinary
session already carried 3 × `[sleep] <defunct>`.

*Change* — `--init` in `run_argv` (tini as pid 1). `DockerSandboxEnvironment`
treats an exec-level failure as a **sandbox-health** failure: recreate the
container (`remove_sandbox` + `start_sandbox` on the same workspace, emitting
`lifecycle sandbox_restarted {container}`), retry the command once, and only
then return an observation of `sandbox unavailable` with rc 137 and end the
turn `error`. runc's text is never a tool output.

*After* — the audit's exact fork bomb, then a plain command in the same
session:

```
python3 -c 'import os; [os.fork() for _ in range(600)]'  rc=1
   Traceback … BlockingIOError: [Errno 11] Resource temporarily unavailable
echo STILL_ALIVE                                        rc=0  STILL_ALIVE
```

and the container afterwards:

```
PID STAT CMD
  1 Ss   /sbin/docker-init -- sleep infinity
  7 S    sleep infinity
defunct processes: 0
Init=true Restart=unless-stopped Pids=512 Mem=2147483648
```

The pid limit still throttles the turn; it no longer destroys the session.

---

## P1

### G-04 · Any per-turn exception killed the whole session

*Before:* after a daemon restart, one message to an `idle` session →
`SandboxError: sandbox … is not running` → `lifecycle failed` → 409 forever,
while the workspace, the clone and the transcript were all still on disk. A
bad model did the same thing.

*Change* — three parts. `ensure_running` `docker start`s a stopped-but-present
container (and re-runs `prepare_workspace`); the sandbox is re-checked at the
top of **every** turn, not only when the agent is first built, and a container
that is gone is rebuilt on the same workspace; and `_turn_worker` ends the
**turn** on any exception — `finish_reason: "error"`, reply
`"This turn failed: <short error>"`, an `agent_error` event, a closed receipt,
and the session back to `idle`. Only a failed *workspace creation* still fails
a session.

*After:* the sandbox was stopped by hand (`docker stop` — which
`unless-stopped` deliberately does not undo) and the session messaged:

```
BEFORE MESSAGE: exited exit=143
… turn_finished reply … "The command executed successfully, listing the
   contents of /workspace: CHANGES.md LICENSE.txt README.md docs examples
   pyproject.toml src tests uv.lock"
AFTER MESSAGE: running
```

and after the daemon restart above, the same session answered a new turn
normally (`finish_reasons: [… "reply"]`). The turn-level error path is shown
under G-07b, where a quota kill ends one turn and leaves the session `idle`.

### G-05 · A model-provider failure was laundered into a normal agent reply

*Before:* the stored agent message was
`[ERROR: Agent failed (Function process_single_item_agent timed out after 90.0
seconds), API failed (API request returned None after all retries)]` with
`meta.finish_reason: "reply"` and a receipt that recorded the turn as a
success.

*Change* — `provider_failure_reason()` recognises mini-swe's error envelope
(`[ERROR: Agent failed…`, `API failed`, `API request returned None`,
`litellm.APIError…`) and the empty-content-plus-recorded-exception shape,
*before* the text-only-response path in `_handle_format_error`. The turn ends
`error` with an `agent_error` event and the reply *"The model provider failed:
&lt;reason&gt;. Try again."*; the envelope never enters the transcript as the
agent's own words.

*Evidence:* five tests in `tests/test_cloud_conversational_agent.py`, including
the verbatim string from session `b53220a8c98d`, and a negative case (an answer
that merely *mentions* an API failure is still a reply). Not forced on the live
box — doing so would mean pointing the deployment at a broken provider.

### G-06 · `ref` accepted a commit SHA per the docs, but a SHA always failed

*Before:* a full SHA → `failed` in 0.4 s, *"Remote branch … not found in
upstream origin"*. `git clone --depth 1 --branch <sha>` cannot resolve a commit
id — `--branch` takes a *name*.

*Change* — a 40-hex ref goes straight to `git init` + `git fetch --depth 1
origin <sha>` + `git checkout FETCH_HEAD`; anything else falls back to that
path when the clone fails and the ref still looks like a commit.

*After* — `pallets/click` at `874ca2bc1c30d93a4ac6e36a15ed685eafe89097`
(tag 8.1.7):

```json
{"create": 201, "status": "idle", "seconds": 5.1, "tree_files": 146,
 "base_sha": "874ca2bc1c30d93a4ac6e36a15ed685eafe89097",
 "base_sha_matches": true}
```

The audit's own repro SHA `b63ace5a…` is not a commit in that repository —
GitHub answers `upload-pack: not our ref` — which is now reported as
*"could not clone the repository: ref not found in the repository"* (G-22).

### G-07 · No disk quota — one session could fill the host

*Before:* `dd if=/dev/zero of=/workspace/big bs=1M count=3000` wrote all
3.1 GB; host `/` went 82 % → 92 %. Memory, CPU, pids and tmpfs were capped;
disk was not, at all.

*Change (a)* — `WORKSPACES_MIN_FREE_MB` (default 2048) is checked before the
clone; below it the session fails immediately with a readable reason.
*Change (b)* — `SANDBOX_WORKSPACE_MAX_MB` (default 2048), measured with
`du -sm` on the turn worker after **every** command (the audit's repro was a
single-command turn, so any stride would have missed it; a measurement that
overruns 2 s raises the stride to 10 for the rest of the session). Over the
cap, the command in flight is killed and the turn ends `error`.
*Change (c)* — `docs/cloud-sandbox.md` §5 *Disk* now says plainly that a true
filesystem quota needs the workspaces directory on a dedicated volume, and why
`--storage-opt size=` is not available here.

*After (a)* — server restarted with `WORKSPACES_MIN_FREE_MB=99999999`:

```json
{"status": "failed", "closed_reason": "failed",
 "error": "RuntimeError: not enough free disk to start a session: 5779 MB free
  under the workspaces directory, 99999999 MB required (WORKSPACES_MIN_FREE_MB)"}
```

*After (b)* — a session whose workspace really was 1822 MB
(`du -sm /srv/gt-workspaces/f12ddbe12824` → `1822`), cap set to 1024 MB, one
ordinary `ls`:

```json
{"cap_env": "1024", "post": 202, "status": "idle",
 "events": [["lifecycle","running",""],
            ["lifecycle","quota_exceeded",
             "workspace quota exceeded (1822 MB > 1024 MB cap)"],
            ["agent_error","",""],
            ["turn_finished","error",""],
            ["lifecycle","idle",""]],
 "last_reply": "This turn failed: workspace quota exceeded (1822 MB > 1024 MB cap)",
 "finish_reasons": ["reply", "error"]}
```

That single result is also the G-04 proof: the turn ends `error`, the receipt
records it, the session goes back to `idle` and takes the next message.

### G-08 · A restart-interrupted turn never ended on the wire

*Before:* `turn_started` (event 632) had no matching `turn_finished`; no event
carried the restart note; the receipt kept `finish_reason: ""` and
`finished_at: null` forever; a live tab showed *"Turn 4 · 1 step"* for 300 s.

*Change* — `recover()` now, for each session found `running`: closes the
receipt with `finish_reason: "interrupted"` (preserving whatever `n_calls` /
`cost` it had), publishes `turn_finished {turn_id, finish_reason:
"interrupted", n_calls, cost}`, publishes a new `system_note {turn_id,
message_id, content}` event for *"Server restarted; turn interrupted"*, and
then `lifecycle idle`. `interrupted` is a `FinishReason`.

*After:* see the block below (produced by restarting the `server` container
under a running `sleep 240` turn).

*After* — a `sleep 240` turn on session `7ec71bf95cdc`, then
`docker compose restart server` underneath it. Read back from the store and
from the event replay a reconnecting client gets:

```json
{"session": "7ec71bf95cdc", "status": "idle", "current_turn_id": null,
 "receipts": [{"turn": "8bf4272a0fba", "finish_reason": "interrupted",
               "finished_at_set": true, "n_calls": 0}],
 "system_messages": [{"turn_id": "8bf4272a0fba",
                      "content": "Server restarted; turn interrupted"}],
 "wire_tail": [["lifecycle", "running", ""],
               ["turn_started", "", "Run exactly this one command and then stop: sleep 240"],
               ["turn_finished", "interrupted", ""],
               ["system_note", "", "Server restarted; turn interrupted"]]}
```

Every one of the four things the audit found missing is there: the
`turn_finished`, its `interrupted` reason, the note as an *event*, and a
receipt that is closed.

### G-09 · Two tabs disagreed: the second never showed the user's prompt

*Before:* `turn_started` carried only `{turn_id, message_id}`, so a subscriber
that had not sent the message had no text to render — tab B showed the turn and
the reply but never the question, for 181 s.

*Change* — `turn_started` now carries `role: "user"` and `content`. Documented
in the README events table. (The UI half of E-12 belongs to
`cloud/ui/src/chatState.ts` and is not in this change set.)

*After:*

```json
{"count": 1, "has_content": true,
 "content": "Reply with exactly: TURNSTARTED_OK", "role": "user"}
```

### G-10 · No per-user authorisation; `ALLOWED_GITHUB_LOGINS` was not enforced

*Before:* a JWT signed with `JWT_SECRET` for `{"sub":"1234","login":
"eve-not-allowed"}` read and wrote every session in the deployment. The
allow-list was checked once at `/auth/callback` and never again, and the token
lasted 7 days with no revocation.

*Change* — `require_user` re-checks `_allowed_logins()` on every request (403
when the login is not on it), and `JWT_TTL_SECONDS` (default **86400**, was
604800) with an `iat` claim bounds how long a removed user keeps access.

*After* (the deployment has a non-empty allow-list):

```json
{"allow_list_configured": true, "allowed_login_sessions": 200,
 "unlisted_login_sessions": 403, "unlisted_login_me": 403, "no_token": 401}
```

### G-11 · An invalid `model` bought a full session and a 4-minute wait

*Before:* `model: ""` → 201, clone + sandbox + `idle`; the first turn spent
**249.5 s** (litellm retrying 11 times with a 4→60 s backoff despite
`num_retries: 0`) and then failed the session.

*Change* — a creation preflight (`MODEL_PREFLIGHT=1` by default, off in tests)
does one 1-token completion over the session's own LiteLLM route → 400
`model not available: <reason>`. A blank `model` is 422. `ref` is rejected
(422) when blank, whitespace-only, containing control characters, or starting
with `-` (which `git clone --branch` would read as a flag). Retries are pinned
off at **both** layers (`num_retries` *and* `max_retries`) and
`MODEL_REQUEST_TIMEOUT` (300 s) bounds a call.

*After:*

```json
{"code": 400, "seconds": 3.1,
 "detail": "model not available: BadRequestError: litellm.BadRequestError:
  OpenAIException - not/a/real/model-xyz is not a valid model ID"}
```

```json
{"blank model": 422,
 "ref": {"''": 422, "'   '": 422, "'main\\n'": 422, "'--upload-pack=x'": 422}}
```

249.5 s and a dead session → 3.1 s and nothing built.

---

## P2

### G-12 · A whitespace-only message started a real turn

`{"content": "   "}` was 202 and burned two model calls and a concurrency slot;
`""` was correctly 422. A `field_validator` now rejects a blank after
`.strip()`. *After:* `422`, session still `idle`, no model constructed.

### G-13 · `gt_mode` was an unvalidated string

Fixed with G-02 (the same `Literal`). `"banana"` → 422.

### G-14 · Stop is only honoured at a step boundary — **partially fixed**

0.48 s when a command is in flight (the command is killed), but **46.8 s** when
a model call is. Fully fixing it means making the LiteLLM call cancellable —
it is a synchronous call inside the turn worker, so a `/stop` cannot reach it
— which is a real change to how turns are executed and is **not** in this
change set. What *is* fixed: `MODEL_REQUEST_TIMEOUT` (default 300 s) now bounds
that worst case explicitly instead of leaving it to the provider, and the
README says so under *Known Limitations*.

### G-15 · Theoretical lost-message window at turn end

Between `take_pending_steering()` and `_set_status(idle)` the row still said
`running`, so a message landing there was queued as steering nothing would
drain. The worker now flips the row to `idle` **first** and then drains again
under a lock `post_message` also takes around its own status read, so either
the sender sees `running` (and the post-flip drain finds the message) or it
sees `idle` (and starts its own turn). Not reproducible over the network before
or after — the window was sub-millisecond — so this is a code-level fix with a
code-level argument.

### G-16 · The `step_limit` reply carried no progress

*"Where I am: no progress recorded yet"* after two real steps, because
`_last_thought()` scans `self.messages` for assistant text and this model
leaves none in the shape the transcript keeps. It now falls back to the last
non-empty assistant text the agent *emitted*.

### G-17 · A malformed `Last-Event-ID` silently replayed the whole history

Now 400. *After:* `{"not-a-number": 400, "-4": 400}`.

### G-18 · GT evidence artifacts absent from the transcript — **deferred**

`.gt_state/transcript.json` holds `exact_literal_search` × 6 and zero
`gt.evidence_artifact.v1`, zero typed actions, zero `abstain` records.
Persisting them means changing what `gt_engine.miniswe_runtime` /
`MiniSweAdapter` write, i.e. editing `gt_engine/**`, which is out of scope for
this change set. Recorded in the README's *Known Limitations* so it is not
mistaken for working.

### G-19 · An empty-command `tool_call` frame under GT

`_EmittingEnvironment.execute` no longer emits `tool_call`/`tool_result` for a
command that is empty after `.strip()`; the command still runs and its output
is still returned.

### G-20 · An OOM-killed command reported nothing

*Before:* rc `137`, output `""` — the agent had to guess it hit the 2 GiB cap.
*Change:* rc 137 with no output at all is mapped to an explicit observation.
*After:*

```
python3 -c 'x = bytearray(3_000_000_000)'   rc=137
  [killed: the command hit the container memory limit (SANDBOX_MEMORY) or was
   killed from outside]
agent reply: "The command attempted to allocate a 3 GB bytearray, which
  exceeded the available memory and was killed (return code 137)."
```

### G-21 · Session creation was unbounded

`MAX_CONCURRENT_SESSIONS` gated *turns* only, so four simultaneous creations —
four clones, four sandboxes, four GT indexes — all succeeded in 1.2 s on a
4-core box. Creation now takes a slot from a separate
`MAX_CONCURRENT_CREATIONS` (default 3) semaphore; past it, 429. A separate
counter, so a burst of creations cannot starve running turns of theirs.

### G-22 · Failure text leaked host paths

*Before:* the clone error handed to the client contained
`/srv/gt-workspaces/<session id>`, and *"could not read Username for
'https://github.com'"* — git's phrasing for "private or non-existent".
*After:*

```json
{"error": "RuntimeError: could not clone the repository: repository not found,
  or it is private and the server has no credentials for it",
 "leaks_host_path": false, "mentions_username_prompt": false}
{"error": "RuntimeError: could not clone the repository: ref not found in the
  repository", "leaks_host_path": false}
```

### G-23 · Ops headroom and blindness

Healthchecks on all three services (G-01). `docker builder prune -af` was run
on the box during this session and reclaimed **4.756 GB** (host `/` 77 % →
64 % at that moment). The remaining structural point — 32 GB of codespace disk
shared between the images, the database and every workspace — is bounded now by
`WORKSPACES_MIN_FREE_MB` and `SANDBOX_WORKSPACE_MAX_MB` rather than by nothing.

---

## Not in this change set (UI)

These are real and were reported by the audit, but they live in `cloud/ui/**`,
which another agent owns. They landed separately in `91f6f779`
(*fix(HAR-84): UI side of the audit gaps*) — including
`GT_MODES = ["off","advisory","assistive","enforced"]` in `cloud/ui/src/api.ts`,
which matches the server's `Literal` exactly. The deployment verified above
still serves the *pre-`91f6f779`* bundle, because the box is checked out at
`4ebf8db` and only the server-side files were copied onto it; a normal
`bash cloud/deploy.sh` from an up-to-date checkout picks the new UI up.

For the record, the UI halves were:

* **E-04 / G-08 (browser half)** — after a server restart the tab returns to
  *Idle* but the turn card stays *"Turn 4 · 1 step"* and the system note is
  never rendered. The server side now emits everything the UI needs:
  `turn_finished {finish_reason: "interrupted"}` and `system_note`.
* **E-12 / G-09 (browser half)** — `chatState.ts:applyEvent` must read the new
  `turn_started.content` / `role` instead of only `linkMessage(message_id)`.
* **F-01 (UI half)** — `NewSessionForm.tsx:18` offered
  `GT_MODES = ["off","advisory","engine"]`. `engine` is now a **422**; the list
  should read `["off","advisory","assistive","enforced"]`. *(That file no longer
  exists: `54532f86` — prompt-first entry — removed the creation form, and the
  GT mode picker now lives in the settings gear, reading the corrected
  `GT_MODES` from `cloud/ui/src/api.ts`. The reference is kept for the audit
  record.)*
* **G-7** — the Codespaces first-visit *"You are about to access a development
  port"* interstitial. Not a defect in this product.

---

## GT modes and the sandbox verifier

`bash cloud/sandbox/verify.sh off` and `bash cloud/sandbox/verify.sh advisory`
both pass on the deployment. The egress policy is unchanged and still holds —
from the proxy log during those two runs:

```
egress-proxy allow-list: github.com,*.github.com,codeload.github.com,
  objects.githubusercontent.com,pypi.org,files.pythonhosted.org,registry.npmjs.org
DENY  CONNECT openrouter.ai:443 403 host is not on the egress allow-list
ALLOW CONNECT github.com:443
```

and the sandbox wrote to the host workspace as uid 1000
(`-rw-r--r-- codespace codespace 64 /srv/gt-workspaces/<id>/SANDBOX.txt`,
`uid=1000(agent)`), with the workspace gone after close.

> `verify.sh` itself needed one change for G-10: it minted its token for the
> login `verify`, which nobody allow-listed, so every request would now be 403.
> It borrows the first `ALLOWED_GITHUB_LOGINS` entry instead.

**The two modes that replace `engine`.** Both complete a real turn and are
still `gt_status: ready` *after* it — which is exactly where `engine` used to
fall over:

| | `assistive` | `enforced` |
|---|---|---|
| session | `7e875f40a86f` | `836bcfd759e5` |
| index | 22.4 s | 20.4 s |
| `gt_status` after create | `ready` | `ready` |
| `gt_status` **after the turn** | `ready` | `ready` |
| `gt_error` | `null` | `null` |
| receipt `gt_status` / `finish_reason` | `ready` / `reply` | `ready` / `reply` |
| `/graph` | `gt: true`, **551** edges | `gt: true`, **550** edges |
| edge kinds | `gt_call, gt_import, gt_ref, import` | `gt_call, gt_import, gt_ref, import` |

551 edges is the number the audit recorded for a *working* GT session; a
degraded one gave 156 import-only edges. The answers were correct
("ParamType is an abstract base class that defines how Click validates and
converts parameter values…").

## Sessions

Every session created during this verification was closed; the final
`GET /api/sessions` state is recorded below.

```
open sessions:      0
sandbox containers: 0
workspace dirs:     0
host /              32G  24G  6.2G  80%     (77% / 7.2G free at the start,
                                             after `docker builder prune -af`
                                             reclaimed 4.756 GB mid-session)
cloud-ui-1        Up (healthy)
cloud-server-1    Up (healthy)
gt-egress-proxy   Up (healthy)
ui / -> 200        public /health -> 200 {"status":"ok","commit":"4ebf8db"}
```

One leak was found *by* this verification and fixed: a clone that fails now
removes the directory it created. The fetch-by-SHA path (G-06) runs `git init`
itself, and a session whose clone failed never gets a `workspace_path` on its
row, so `close()` had nothing to delete — two 1 MB orphans were sitting under
`/srv/gt-workspaces` after the first pass. Re-run afterwards: two failed clones,
**zero** orphans (`tests/test_cloud_workspace.py::
test_a_failed_clone_leaves_nothing_on_disk`).
