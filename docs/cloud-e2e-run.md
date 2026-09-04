# Cloud coding agent — live end-to-end run (HAR-84)

- **Date:** 2026-09-04 (UTC 20:39)
- **Worktree:** `D:\gt-cloud`, branch `cloud/internal-harness`
- **Commit at time of run:** `767f00f2` (changes below are uncommitted working-tree edits)
- **Provider:** OpenRouter, OpenAI-compatible route (`OPENAI_BASE_URL=https://openrouter.ai/api/v1`)
- **Model used:** `nvidia/nemotron-3-super-120b-a12b:free`
  (LiteLLM resolves it to `openai/nvidia/nemotron-3-super-120b-a12b:free` + `api_base`, as designed)
- **Model NOT used:** `google/gemma-4-31b-it:free` — see "Model selection" below
- **Evidence directory:** `D:\tmp\claude\D--gt-harness\f4372041-b07e-4174-942f-651a5081e5a0\scratchpad\e2e\`
  (`*_stream.sse`, `*_result.json`, `*_status.json`, `server.log`, `d_validation.txt`, `e2e.db`)
- **Unit tests:** `python -m pytest tests/test_cloud_*.py` → **40 passed** (stable over 4 consecutive runs)

Port 8000 was verified free before the run (`netstat -ano | findstr :8000` → no listener), so
port 8000 was used throughout.

---

## Model selection

`google/gemma-4-31b-it:free` was tried first, as instructed, and does **not** work right now.
A direct chat-completions probe returned HTTP 429:

```
google/gemma-4-31b-it:free is temporarily rate-limited upstream. Please retry shortly,
or add your own key to accumulate your rate limits
```

Session `61d36f548f7c` was started with it anyway. mini-swe-agent's LiteLLM wrapper retries a
`RateLimitError` up to 10 times with exponential backoff capped at 60 s, so the session sat in
`running` for ~7 minutes emitting nothing beyond `lifecycle: running`, then ended `failed` with
0 steps. That is provider unavailability, not a harness bug, but it is worth knowing that a
rate-limited model produces a silent multi-minute stall with no event traffic.

Fallback `nvidia/nemotron-3-super-120b-a12b:free` worked first try and followed mini-swe's
action format cleanly in every session below. `minimax/minimax-m3:free` also responded to a
direct probe and was never needed.

The key used is referred to as `<OPENROUTER_KEY>` (the `final_openrouter_musecontributor` key);
it is not written anywhere in this document or in the captured evidence files.

---

## Commands

Server (from `D:\gt-cloud`):

```bash
export OPENAI_API_KEY=<OPENROUTER_KEY>
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export MSWEA_COST_TRACKING=ignore_errors
export DB_PATH='D:\tmp\claude\D--gt-harness\f4372041-b07e-4174-942f-651a5081e5a0\scratchpad\e2e.db'
export MAX_CONCURRENT_SESSIONS=2
python -m uvicorn cloud.server.app:app --port 8000 --log-level info

curl -s http://127.0.0.1:8000/health          # -> {"status":"ok"}
```

Session driver (per case):

```bash
curl -s -X POST http://127.0.0.1:8000/api/sessions \
     -H 'Content-Type: application/json' -d @<case>_request.json
curl -sN http://127.0.0.1:8000/api/sessions/$ID/events > <case>_stream.sse &
curl -s     http://127.0.0.1:8000/api/sessions/$ID            # poll until terminal
curl -s     http://127.0.0.1:8000/api/sessions/$ID/result
```

Cases (b) and (c) were driven by `scratchpad/e2e/drive.py`, which reads the SSE stream and fires
`POST /steer` or `POST /stop` the moment the triggering event type appears on the wire.

---

## Case (a) — GT off, happy path

| | |
|---|---|
| Session id | `03b19ef6a8c0` |
| Status | **completed** |
| terminal_outcome | `submitted` (`exit_status: Submitted`) |
| Steps (`n_calls`) | 5 |
| Cost | `0.0` (free model; OpenRouter reports no price, hence `MSWEA_COST_TRACKING=ignore_errors`) |
| Events | 19, ids 5–23, strictly increasing |

Stream head (first 15 lines):

```
id: 5
event: lifecycle
data: {"id": 5, "type": "lifecycle", "timestamp": 1788553992.618, "data": {"status": "cloning", "repo": "https://github.com/octocat/Hello-World", "ref": "master"}}

id: 6
event: lifecycle
data: {"id": 6, "type": "lifecycle", "timestamp": 1788553993.207, "data": {"status": "building_agent"}}

id: 7
event: lifecycle
data: {"id": 7, "type": "lifecycle", "timestamp": 1788553993.214, "data": {"status": "running"}}

id: 8
event: lifecycle
data: {"id": 8, "type": "lifecycle", "timestamp": 1788553993.218, "data": {"status": "running"}}
```

Stream tail (last 10 lines):

```
data: {"id": 21, "type": "assistant", "timestamp": 1788553999.009, "data": {"content": null, "actions": ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"], "n_calls": 5, "cost": 0.0}}

id: 22
event: tool_call
data: {"id": 22, "type": "tool_call", "timestamp": 1788553999.009, "data": {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "n_calls": 5}}

id: 23
event: lifecycle
data: {"id": 23, "type": "lifecycle", "timestamp": 1788553999.042, "data": {"status": "completed", "exit_status": "Submitted", "n_calls": 5, "cost": 0.0}}
```

The `ls -la` observation in this stream (`drwxr-xr-x 1 Lenovo 197121 ...`) is direct evidence
that commands ran under **bash**, not `cmd.exe`.

Patch:

```diff
diff --git a/hello.py b/hello.py
new file mode 100644
index 0000000..75d9766
--- /dev/null
+++ b/hello.py
@@ -0,0 +1 @@
+print('hello world')
```

This required a fix — see bug #1. `.gt_state/trajectory.json` correctly stays out of the patch.

---

## Case (b) — steering mid-run

| | |
|---|---|
| Session id | `4c5bd518a305` |
| Status | **completed** |
| terminal_outcome | `submitted` |
| Steps | 4 |
| Cost | `0.0` |
| Events | 17, ids 26–42 |

`POST /steer` was fired as soon as the first `tool_result` (id 32) arrived and returned
`202 {"status":"queued"}`. Event order:

```
id 30  assistant     (explore)
id 31  tool_call
id 32  tool_result   <-- POST /steer fired here
id 33  assistant     (explore, step already in flight)
id 34  tool_call
id 35  tool_result
id 36  steering      <-- drained at the NEXT step boundary
id 37  assistant     (create NOTES.md)
id 38  tool_call
id 39  tool_result
id 40  assistant     (submit)
id 41  tool_call
id 42  lifecycle     completed / Submitted
```

Steering event payload:

```
id: 36
event: steering
data: {"id": 36, "type": "steering", "timestamp": 1788554118.417, "data": {"content": "Stop exploring. Create a file NOTES.md containing one line 'steered', then submit."}}
```

That the steering event lands after id 35 rather than immediately after id 32 is the point: the
agent was mid-step when the message was queued, and `SteerableAgent` drains the queue only at the
top of the loop. Steering is genuinely load-bearing — the agent abandoned exploration and did the
new instruction.

Patch:

```diff
diff --git a/NOTES.md b/NOTES.md
new file mode 100644
index 0000000..786c688
--- /dev/null
+++ b/NOTES.md
@@ -0,0 +1 @@
+steered
```

---

## Case (c) — stop

| | |
|---|---|
| Session id | `7892360e1d75` (after fixes) |
| Status | **stopped** |
| terminal_outcome | `user_stopped` |
| Steps | 1 |
| Cost | `0.0` |
| Events | 8, ids 52–59 |
| Patch | `null` (agent only ran `ls -la` before being stopped — correct) |

`POST /stop` fired on the first `assistant` event, returned `202 {"status":"stopping"}`.

Stream (complete; 8 events):

```
id: 52  lifecycle  {"status": "cloning", ...}
id: 53  lifecycle  {"status": "building_agent"}
id: 54  lifecycle  {"status": "running"}
id: 55  lifecycle  {"status": "running"}
id: 56  assistant  {"actions": ["ls -la"], "n_calls": 1}   <-- POST /stop fired here
id: 57  tool_call  {"command": "ls -la"}
id: 58  tool_result {"output": "total 3525\ndrwxr-xr-x 1 Lenovo 197121 ...
id: 59  lifecycle  {"status": "stopped", "exit_status": "UserStopped", "n_calls": 1, "cost": 0.0}
```

The **first** attempt at this case (session `01cca8acb76c`) exposed two bugs — see #2 and #3.
It ended with session status `completed` and two contradictory terminal lifecycle events
(`stopped` at id 50 immediately followed by `completed` at id 51). Both are fixed; the re-run
above emits exactly one terminal lifecycle event with the right status.

---

## Case (d) — validation

Captured verbatim in `scratchpad/e2e/d_validation.txt`. All five checks pass.

| Request | Expected | Got |
|---|---|---|
| `POST /api/sessions` with `repo: https://gitlab.com/foo/bar` | 400 | **400** `{"detail":"repo must be a GitHub HTTPS URL"}` |
| `POST /api/sessions` with no `task` | 422 | **422** `{"detail":[{"type":"missing","loc":["body","task"],...}]}` |
| `POST /api/sessions/03b19ef6a8c0/steer` (finished session) | 409 | **409** `{"detail":"session is not running"}` |
| `GET /api/sessions/doesnotexist` | 404 | **404** |
| `GET /api/sessions/doesnotexist/events` | 404 | **404** |
| `GET /api/sessions/doesnotexist/result` | 404 | **404** |
| `POST /api/sessions/doesnotexist/steer` | 404 | **404** |
| `POST /api/sessions/doesnotexist/stop` | 404 | **404** |

---

## Case (e) — GT advisory (best effort)

| | |
|---|---|
| Session id | `80f3958b4a90` |
| `gt_mode` | `advisory` |
| Status | **completed** |
| terminal_outcome | `submitted` |
| Steps | 5 |
| Cost | `0.0` |
| Events | 20, ids 60–79 |

**GT was unavailable.** A `gt_unavailable` lifecycle event was emitted, with this error text:

```
id: 62
event: lifecycle
data: {"id": 62, "type": "lifecycle", "timestamp": 1788554295.486, "data": {"status": "gt_unavailable", "error": "ModuleNotFoundError: No module named 'groundtruth.runtime.adapters'"}}
```

No `gt_ready` event was seen. Note what this does and does not prove:

- The **model** side of GT resolved — `gt_engine.miniswe_typed_actions.GroundTruthLitellmModel`
  imported fine, so the session ran on the GT model wrapper, not the plain `LitellmModel`.
- The **runtime hook** side did not — `install_runtime_hooks` / the indexer chain failed on a
  missing `groundtruth.runtime.adapters` module. The gt-index binary was never reached.
- The degradation path works: the session did not crash, emitted a clear diagnostic event, and
  completed normally with the same 5-step trajectory and identical patch as case (a).

I did not attempt to install the GT runtime; per the brief this case was time-boxed.

---

## Server bugs found and fixed

All in `cloud/server/`. Line numbers are post-fix.

1. **`runner.py:335-364` — `_extract_patch` silently dropped every file the agent created.**
   A bare `git diff` does not see untracked files, so case (a) — whose entire task is "create
   `hello.py`" — returned `patch: null` on a fully successful run. Fixed by marking the tree
   intent-to-add (`git add -A -N`) before diffing, with a pathspec that excludes the harness's
   own scratch directory so `.gt_state/trajectory.json` never leaks into a patch:
   - `runner.py:347` — `pathspec = [".", f":(exclude){_STATE_DIRNAME}"]`
   - `runner.py:350` — `git add -A -N -- . :(exclude).gt_state`
   - `runner.py:357` — `git diff -- . :(exclude).gt_state`
   - `runner.py:17` — `_STATE_DIRNAME` constant, reused at `runner.py:238` so the two cannot drift.

2. **`runner.py:151` — a user-stopped session was recorded as `completed`.**
   `final_status` was a two-way branch (`failed` if the terminal outcome was in
   `_FAILURE_TERMINALS`, else `completed`), which has no room for a third terminal state.
   `user_stopped` is neither a success nor a failure. Case (c) reported `status: completed`
   alongside `terminal_outcome: user_stopped`. Fixed with an explicit mapping:
   - `runner.py:381` — `_TERMINAL_TO_STATUS = {"user_stopped": "stopped"}`
   - `runner.py:151-153` — `final_status = _TERMINAL_TO_STATUS.get(terminal, ...)`

3. **`steerable_agent.py:130` — two contradictory terminal lifecycle events on a stop.**
   The stop branch emitted `lifecycle: stopped`, then fell through to a final emit that
   hard-coded `"status": "completed"`. The live SSE stream hid this (the event bus closes the
   stream on the first terminal status), but both events were persisted, so any client replaying
   stored events — a reconnect with `after_id`, or the UI loading history — would see the run as
   `completed`. Fixed by deleting the early emit and deriving the single terminal status from
   `exit_status`:
   - `steerable_agent.py:59-61` — the duplicate `_emit` in the stop branch removed
   - `steerable_agent.py:126-133` — `"status": "stopped" if exit_status == "UserStopped" else "completed"`

4. **`store.py:94` — `list_sessions` ordering was non-deterministic.**
   `ORDER BY created_at DESC` with a float timestamp ties when two sessions are created inside
   one clock tick, so `GET /api/sessions` could return them in either order (this made
   `tests/test_cloud_store.py::test_list_sessions_newest_first` fail intermittently). Fixed with
   `ORDER BY created_at DESC, rowid DESC`.

---

## Hardening changes (task 1)

**New: `cloud/server/environment.py` — `CloudLocalEnvironment(LocalEnvironment)`.**

- *Credential isolation.* `_SENSITIVE_SHELL_ENV`, `_is_sensitive_env_name`,
  `_scrub_sensitive_mapping`, `execution_env()` and `get_template_vars()` are ported from
  `scripts/miniswe_gt_run.py`'s `CredentialIsolatedLocalEnvironment` rather than imported, because
  that module has heavy import side effects. Provider / AWS / Google / HuggingFace / GitHub
  credentials, plus anything matching `*_API_KEY`, `*_ACCESS_TOKEN`, `*_AUTH_TOKEN`, `*_PASSWORD`,
  `*_SECRET`, are removed from the child process environment and from the template variables
  rendered into the prompt. They remain visible to the model client process, which needs them.
- *Real bash.* Stock `LocalEnvironment` runs `subprocess.Popen(command, shell=True)`, which is
  `cmd.exe` on Windows — every heredoc, `&&`, and quoting convention in mini-swe's prompt would
  break. `CloudLocalEnvironment._run` invokes `[bash, "-c", command]` with `shell=False`. `bash`
  is resolved from `PATH` (`shutil.which`) and falls back to `C:\Program Files\Git\bin\bash.exe`
  and two sibling paths on Windows. `bash -c` is used rather than `bash -lc` to avoid pulling in
  the login profile's PATH.
- *Contract preserved.* `execute()` returns the same `{"output", "returncode", "exception_info"}`
  dict (plus `extra` on failure), kills the process group on timeout exactly as the base class
  does, and calls `self._check_finished(output)` so `Submitted` still propagates.

Verified standalone before the live run: `bash` resolved to `C:\Program Files\Git\usr\bin\bash.EXE`;
`echo $0` returned `/usr/bin/bash`; `uname -s` returned `MINGW64_NT-10.0-26200`;
`OPENAI_API_KEY`, `GITHUB_TOKEN` and a synthetic `MY_SECRET` all expanded to empty inside the
command and were absent from `get_template_vars()`; `$PATH` survived; a 1 s timeout on `sleep 5`
returned `returncode: -1` with the expected `exception_info`; and
`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` raised `Submitted`.

Wired in at `runner.py:201` (import) and `runner.py:232` (construction), replacing `LocalEnvironment`.

**`app.py:11` — `os.environ.setdefault("MSWEA_COST_TRACKING", "ignore_errors")`.**

Placement matters and was checked rather than assumed. `LitellmModelConfig.cost_tracking` is a
*class attribute* whose default is `os.getenv("MSWEA_COST_TRACKING", "default")`, evaluated when
`minisweagent/models/litellm_model.py` is first imported. `app.py`'s module-level imports reach
`minisweagent.agents.default` via `deps -> routes -> runner -> steerable_agent`; that chain was
verified **not** to pull in `litellm_model` (which `runner._build_agent` imports lazily), so the
setdefault at the top of `app.py`, above `from . import deps`, executes first in every path. The
subsequent imports carry `# noqa: E402`. Without this, every free OpenRouter model aborts the run,
since none of them have a LiteLLM price entry.

---

## Caveats and things that did not work

- **`google/gemma-4-31b-it:free` is unusable right now** (upstream 429). Not a harness problem,
  but the failure mode is bad: a rate-limited model produces a ~7-minute silent stall with no
  events at all, then `failed` with 0 steps. There is no `provider_retry` or `waiting` event to
  tell a UI what is happening. Worth adding.
- **GT advisory mode is not actually exercised.** `groundtruth.runtime.adapters` is missing in
  this environment, so only the graceful-degradation path was proven, not the GT integration
  itself.
- **Cost is `0.0` everywhere.** That is a true value for free models, not a measurement — cost
  tracking is deliberately suppressed. These runs say nothing about cost accounting on a priced
  model.
- **All sessions used one tiny repo** (`octocat/Hello-World`, one 14-byte README) and a trivial
  task. Nothing here exercises a large clone, a long trajectory, concurrency against
  `MAX_CONCURRENT_SESSIONS`, or a `step_limit` being hit.
- **`_running_count` is mutated from worker threads** (`runner.py:74`, `runner.py:174`) and read
  in `launch` without a lock, and the concurrency check is a plain `>=` rather than a semaphore.
  Two simultaneous `POST /sessions` can both pass the check. Not hit during this run; not fixed,
  since it is out of scope and a real fix means restructuring to an `asyncio.Semaphore`.
- **This worktree was being edited concurrently** by another agent during the run (UI components,
  `cloud/Dockerfile`, and a new `tests/test_cloud_routes.py` written at 16:39 local). One test-suite
  run caught that file mid-write and reported a spurious failure. The final suite state —
  40 passed, four consecutive clean runs — is the one to trust. I touched only
  `cloud/server/{environment,runner,app,steerable_agent,store}.py`.
