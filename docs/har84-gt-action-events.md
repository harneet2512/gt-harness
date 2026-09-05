# HAR-84 — GroundTruth typed actions on the cloud agent's event stream

Date: 2026-09-05. Branch `cloud/internal-harness` (from `9c39486`).
Codespace `gt-cloud-agent-wvrqp4rqpjp42gvp7`.

## 1. Why the UI saw nothing

With `gt_mode` on, the model gets a second tool beside `bash`: `groundtruth`.
Its calls are **typed actions**, and they are not shell commands.
`gt_engine.miniswe_runtime.install_runtime_hooks` replaces
`agent.execute_actions`; its typed branch
(`gt_engine/miniswe_runtime.py:596-745`) dispatches through
`execute_typed_action_fail_open` and every path in it ends in `continue`
**before** the shell. `environment.execute(action)` appears only at lines 751,
762 and 788 — the empty-command, submit and bash paths.

`cloud/server/conversational_agent.py::_EmittingEnvironment.execute` is where
`tool_call` / `tool_result` are emitted, precisely because GT drives
`env.execute` itself. A typed action never reaches it. So a GT session's trail
showed an `assistant` frame with nothing under it, then the next `assistant`
frame — the evidence that actually answered the question was invisible.

## 2. The seam

`cloud/server/gt_events.py::install_gt_action_events(agent)`, called by
`cloud/server/runner.py::SessionManager._install_gt` immediately **after**
`install_runtime_hooks(agent, gt_session)`.

It wraps two instance methods and modifies nothing under `gt_engine/`:

| Wrapped | Why |
|---|---|
| `agent.execute_actions` (GT's replacement, by then) | stamps the batch start for `duration_ms` |
| `model.format_observation_messages(message, outputs, template_vars)` | the one call that sees **both** halves |

`format_observation_messages` is called once by GT's own `execute_actions`
(`miniswe_runtime.py:926-956`), after the whole batch has run and before the
next model call, with `message["extra"]["actions"]` — the *normalised* requests,
already through `cloud/server/typed_scopes.py` — and `outputs`, positionally
aligned (GT appends exactly one output per action, in order).

Three alternatives were rejected:

* `_parse_actions` on the scope-normalising model — sees the request, never the
  result.
* wrapping `agent.execute_actions` alone — it returns `add_messages(*formatted,
  *directives)`, i.e. formatted messages, not the result dicts, so `returncode`
  and `extra` would have to be re-derived from tool-message text.
* monkeypatching `gt_engine.miniswe_typed_actions.execute_typed_action_fail_open`
  — narrowest per action and the only way to get exact per-action timing, but it
  is a process-global patch of the benchmark harness *and* it misses the three
  answers GT synthesises without calling the router at all
  (`query_fanout_refused`, `capability_disabled`, `query_turn_budget_exceeded`).

The cost of the chosen seam is that `duration_ms` is the wall clock of the
action *batch*. A model call almost always carries one action, in which case it
is that action's own time; §4 below shows a two-action batch, where both frames
carry the same figure. It is documented that way in `cloud/README.md`.

## 3. What is emitted

One `gt_action` frame per typed action, after it ran and before the next model
call, with no extra `assistant` frame (the typed action is part of the model
call that requested it, and `step` says which). Field-by-field meanings are in
`cloud/README.md` → *GroundTruth typed actions*.

`gt_action` joins `MIRRORED_EVENT_TYPES`, so a worker's typed actions land on
its parent's stream tagged `agent_id`.

Receipts gained `gt_actions` and `gt_exact_matches` (`semantics == "exact"`
**and** `match_count > 0` — an exact abstention over an empty scope is a GT
action, not an answer), and the session row gained the `gt_actions` total.
Store schema 6 → 7, drop-and-recreate as usual.

## 4. Live verification

Not a redeploy: the stack at `9c39486` was left running on :8000 and :80. The
working tree's `cloud/` was copied to `/tmp/gt8010/cloud` on the codespace and
mounted into a throwaway container from the *same server image*, so the
vendored `groundtruth` wheel and `gt-index` were the real ones:

```
docker compose -f cloud/docker-compose.yml run --rm --no-deps -d -p 8010:8010 \
  -v /tmp/gt8010/cloud:/app/cloud \
  -e SANDBOX_MODE=local -e DB_PATH=/tmp/gt8010.db \
  -e WORKSPACES_DIR=/tmp/gt-ws-8010 --name gt8010 \
  server uvicorn cloud.server.app:app --host 0.0.0.0 --port 8010
```

Session `662e538012c4` — `pallets/click@main`, `gt_mode: advisory`, model
`deepseek/deepseek-v4-flash`, message *"Which module defines the Command class
and what calls its invoke method? Answer briefly."*

Frame order on `/events` (one turn, two model calls):

```
turn_started, assistant, gt_action, gt_action, assistant, agent_reply, turn_finished
```

Both typed actions came back exact:

```json
{"id": 10, "type": "gt_action", "data": {
  "turn_id": "2de252ff46ce", "step": 1,
  "kind": "exact_literal_search",
  "arguments": {"literal": "class Command", "paths": ["src/click/core.py"]},
  "scope": ["src/click/core.py"],
  "returncode": 0, "semantics": "exact", "coverage": "complete",
  "match_count": 2, "omissions": [],
  "reason_codes": ["EXACT_COMPLETE_EQUIVALENCE"],
  "duration_ms": 229.052,
  "evidence_artifact_id": "call_7df0a8e260014c4ca9acec82"}}

{"id": 11, "type": "gt_action", "data": {
  "turn_id": "2de252ff46ce", "step": 1,
  "kind": "exact_literal_search",
  "arguments": {"literal": "invoke(", "paths": ["src/click/core.py"]},
  "scope": ["src/click/core.py"],
  "returncode": 0, "semantics": "exact", "coverage": "complete",
  "match_count": 14, "omissions": [],
  "reason_codes": ["EXACT_COMPLETE_EQUIVALENCE"],
  "duration_ms": 229.052,
  "evidence_artifact_id": "call_5184bd95d5be438e8af30043"}}
```

Receipt and session row:

```
{"turn_id":"2de252ff46ce","n_calls":2,"wall_seconds":7.699,
 "gt_actions":2,"gt_exact_matches":2,"finish_reason":"reply",
 "gt_status":"ready","model":"deepseek/deepseek-v4-flash"}
session: turns=1 steps=2 gt_actions=2
```

The agent's reply — *"`Command` is defined in `src/click/core.py` (line 959);
`invoke` (line 1401) is called by `BaseCommand.main()` (1552) and
`Group.invoke()` (2032, 2063)"* — is grounded in those two typed answers, and
the UI can now render them as

```
⏺ GroundTruth(exact_literal_search "class Command" in src/click/core.py)
  ⎿ 2 matches · exact · complete
```

Both frames carry the same `duration_ms`: they were one batch, exactly as §2
says.

The session was closed, the throwaway container removed, and
`/tmp/gt-ws-8010`, `/tmp/gt8010*` deleted. The deployed stack was never
restarted.

## 5. Tests

* `tests/test_cloud_gt_events.py` — 24 tests. The payload builder against real
  `gt.compiled_observation.v1` shapes (exact answer, abstention, enum-valued
  semantics, mapping-valued `coverage`, argument truncation, omission cap,
  unparseable output, every `match_count` shape, producer scope never echoing
  the request back), the HAR-85 scope-normalised case (`src/click/**` →
  `src/click` in both `arguments` and `scope`), and ordering/tallies through the
  real `ConversationalAgent` turn loop with a `FakeGtRuntime` standing in for
  GT's `execute_actions` replacement.
* `tests/test_cloud_chat.py` — two HTTP-level tests: a scripted turn with one
  typed action yields exactly one `gt_action` frame in
  `assistant, gt_action, assistant, tool_call, tool_result, assistant` order
  with `gt_actions: 1` / `gt_exact_matches: 1` on the receipt and `gt_actions: 1`
  on the session; a `gt_mode: off` turn reports zeros.
* `tests/test_cloud_agents.py` — a worker's `gt_action` frames are mirrored onto
  the parent with `agent_id`, and carry none on the worker's own stream.

The only stub is `execute_typed_action_fail_open` (the vendored `groundtruth`
wheel, present only in the server image); its real `(request, result)` contract
and the `gt.compiled_observation.v1` payload shape are reproduced verbatim.

```
python -m ruff check cloud/ tests/test_cloud_*.py   -> clean
python -m pytest tests/test_cloud_*.py -q           -> 333 passed, 4 skipped
                                                       (306 before)
```
