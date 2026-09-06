# External agents

Adapters that put a **local** Claude Code or Codex session onto the cloud UI as
a live card, with its tool calls, the files it is working in, and its subagents
nested underneath. They run on the user's own machine, in the user's own
account, and post over HTTPS. Nothing here runs on the server.

Everything below describes the code in `cloud/adapters/`. Where it says a thing
was *measured*, it was measured on this machine against **Claude Code 2.1.263**
and **Codex CLI 0.153.3** — not read off a blog post, and in two places not read
off the documentation either, because the documentation was wrong or silent.

- [What you get](#what-you-get)
- [The event contract](#the-event-contract)
- [The three adapters](#the-three-adapters)
- [Setting it up: Claude Code](#setting-it-up-claude-code)
- [Setting it up: Codex](#setting-it-up-codex)
- [Configuration](#configuration)
- [What each adapter can and cannot see](#what-each-adapter-can-and-cannot-see)
- [How the host contracts were verified](#how-the-host-contracts-were-verified)
- [Security model](#security-model)
- [Failure modes](#failure-modes)

---

## What you get

One local session becomes a tree of cards in one cloud session: the main agent,
and one nested card per subagent, each with a one-line activity description and
a token count when the host actually reports usage.

Measured, from a real `claude -p` run against a capturing server — a parent and
one `Explore` subagent, with the child's own tool calls landing on the child:

```
agent1  claude-code · proj                      Waiting for input        39 964 tokens
  agent2  List files in the project directory   Finished                 17 086 tokens
          ├ Bash   Running ls
          └ Read   Reading sample.txt           files: ["sample.txt"]
```

And from a real Codex session with seven concurrent subagents, which is where
the nesting earns its keep:

```
agent1  codex · gt-harness
  ├ Ramanujan · root/output_spec_review        ├ Hypatia · root/bounded_context
  ├ Feynman   · root/output_standards_review   ├ Maxwell · root/capability_admission
  ├ Raman     · root/layout_spec_review        └ Faraday · root/dense_cache
  └ Ohm       · root/layout_standards_review
```

Files arrive repo-relative (`gt_engine/indexer.py`), activities as phrases
(`Running powershell.exe git`, `Editing provider_limits.py`), and the token
counts were monotonic across 2.77 M tokens.

---

## The event contract

Three routes. The adapters speak only these.

| Step | Request |
|---|---|
| **Register** | `POST {origin}/api/sessions/{session_id}/external-agents` with the user's session cookie or JWT. Body `{"agent_kind": "claude-code"｜"codex"｜"other", "label": str, "task": str｜null, "cwd": str｜null, "parent_agent_id": str｜null}`. Answers `201 {"agent": {...}, "ingest_token": str, "ingest_url": str}`. |
| **Stream** | `POST {ingest_url}` — that is `{origin}/api/external-agents/{agent_id}/events` — with `Authorization: Bearer {ingest_token}`, body `{"events": [...]}`, **at most 100 events and 256 KB per batch**. |
| **Finish** | `POST {origin}/api/external-agents/{agent_id}/finish` with the same bearer token, body `{"status": "done"｜"error", "summary": str｜null}`. |

A **subagent is its own external agent** with `parent_agent_id` set to the
parent's id. There is no separate subagent route.

The four event shapes:

| `type` | Fields |
|---|---|
| `assistant` | `text` |
| `tool_call` | `name`, `command` (`null` when there is none), `files` (`[str]`), `activity` |
| `tool_result` | `name`, `ok` (bool), `output`, `files` |
| `status` | `state` (`working`｜`idle`｜`done`｜`error`), `note`, `activity`, `tokens` |

`activity` is a short human phrase, capped at 200 characters, in the voice of a
fleet list: *Editing routes.py*, *Running pytest tests*, *Delegating to Explore:
find the endpoints*. It is derived from the tool and its target, never from the
raw command line.

`tokens` is a **cumulative** count and the server ignores a decrease. It is
**omitted, never synthesised**: an event with no `tokens` key means the host
reported no usage, not that the agent used none. `Bridge._monotonic_tokens` drops
a value that would go backwards rather than sending it.

`files` must be repo-relative. The server rejects absolute paths and `..`, and
`to_repo_relative()` is the single place the conversion happens — it is applied
inside `tool_call()` and `tool_result()` so no caller can forget it. A path
outside the reported `cwd` is **dropped**, not clamped. Conversion is lexical
(`os.path.normpath`), so a file an `Edit` is about to create still converts, and
a symlink is not silently followed out of the repository.

---

## The three adapters

Everything is standard library only, Python ≥ 3.10, so a single file can be
copied onto a machine that has no virtualenv.

| File | What it is |
|---|---|
| [`cloud/adapters/gt_cloud_bridge.py`](../../cloud/adapters/gt_cloud_bridge.py) | The transport all three share. Registration (with reuse), a bounded queue, batching and coalescing on a background thread, retry with backoff, the path conversion, `finish()`. |
| [`cloud/adapters/claude_code/gt_cloud_hook.py`](../../cloud/adapters/claude_code/gt_cloud_hook.py) | The hook. Reads one hook payload on stdin, posts the matching events. **Serves both Claude Code and Codex** — their hook payloads are the same shape. |
| [`cloud/adapters/codex/gt_cloud_codex.py`](../../cloud/adapters/codex/gt_cloud_codex.py) | The Codex rollout tailer. Follows the session transcript on disk and the subagent rollout files spawned under it. Needs no configuration inside Codex. |
| [`cloud/adapters/gt_cloud_tail.py`](../../cloud/adapters/gt_cloud_tail.py) | The generic JSONL tailer, for a tool with neither hooks nor a transcript we parse. The honest fallback. |
| [`cloud/adapters/payloads.py`](../../cloud/adapters/payloads.py) | Pulling a command and a set of paths out of a tool payload, by reading fields *if present* rather than asserting a schema. |
| [`cloud/adapters/claude_code/transcript.py`](../../cloud/adapters/claude_code/transcript.py) | Reading a token count out of a Claude Code transcript, since its hooks carry none. |

The bridge's guarantees, which the rest depends on:

- **Every public entry point swallows its own exceptions** and returns a value.
  The only place an exception surfaces is the debug log, and only when
  `GT_CLOUD_DEBUG=1`.
- **Every network call has a timeout of at most 3 s** — **1.5 s in hook mode**,
  with retries set to 0 — and `GT_CLOUD_TIMEOUT` can only lower it, never raise
  it. A hook is inside somebody's tool call; a tailer is not, so the tailers
  keep the more patient budget.
- **A down deployment stops costing anything, quickly.** Three consecutive
  network or 5xx failures against an origin open a **circuit breaker**, recorded
  in a file beside the registrations and keyed by origin, and every later
  invocation returns without touching the network until it expires
  (`GT_CLOUD_BREAKER_SECONDS`, default 300 s). One attempt after the window
  either closes it or re-opens it immediately. A 401, 403, 404 or 410 opens it
  at once and for four times as long, because retrying a revoked token has no
  upside. A plain 400 opens nothing: that is our bug, not the deployment's.
- **The queue is bounded** (`GT_CLOUD_QUEUE_MAX`, default 2000). When it is
  full it drops the **oldest** and counts the drop; the next batch carries a
  `status` note saying how many were lost, and so does the finish summary. It
  never grows without limit and it never blocks the producer.
- **5xx and network failures are retried** with exponential backoff. **4xx is
  not** — a 401, 403, 404 or 410 disables the bridge quietly, because a revoked
  token cannot be fixed by trying harder.
- **Batches arrive in the order they were taken.** Taking a batch off the queue
  and posting it happen under one lock, so a `finish()` flushing on the caller's
  thread cannot overtake a batch the flush thread is already sending. Without
  this the two race: it showed up on a real Codex session as an apparently
  non-monotonic token count, and there is a regression test for it.
- **One host session is one card.** A registration is written to a state file
  under the OS temp directory, keyed by the host agent's own session id, so the
  twentieth hook invocation reuses what the first one registered. Registration
  is taken under a directory mutex, so two hooks firing at once do not create
  two cards.

---

## Setting it up: Claude Code

1. Copy the `cloud/adapters/` directory onto the machine that runs Claude Code
   (or point at it in a checkout — the hook adds its own parent directory to
   `sys.path`, so it runs as a plain script).

2. Set three environment variables in the shell you start `claude` from:

   ```bash
   export GT_CLOUD_ORIGIN=https://your-deployment.example.com
   export GT_CLOUD_SESSION=<the cloud session id to attach to>
   export GT_CLOUD_TOKEN=<your session JWT>
   ```

   The JWT is the same credential the browser holds; `/auth/me` accepts it and
   so does the registration route. Without these three the hook is a no-op that
   exits 0, so it is safe to leave configured when you are not reporting.

3. Merge the `hooks` block from
   [`cloud/adapters/claude_code/settings.snippet.json`](../../cloud/adapters/claude_code/settings.snippet.json)
   into `~/.claude/settings.json`, or into `.claude/settings.json` for one
   repository, replacing the path. On Windows use forward slashes and keep the
   quotes so a directory with spaces still works:

   ```json
   "command": "python \"C:/Users/you/gt-cloud/cloud/adapters/claude_code/gt_cloud_hook.py\""
   ```

Nine events are registered: `SessionStart`, `UserPromptSubmit`, `PreToolUse`,
`PostToolUse`, `PostToolUseFailure`, `SubagentStart`, `SubagentStop`, `Stop`,
`SessionEnd`.

Three details in the snippet are deliberate:

- **The tool matcher is `"*"`, not a list of tool names.** A list would silently
  miss `PowerShell` on Windows — where Claude Code does not register the `Bash`
  tool at all — plus MCP tools and anything added in a later version.
- **Every entry sets `"timeout": 15`.** `SessionEnd` hooks get a **1.5 second**
  budget by default, which is shorter than one network call, and the budget only
  rises to the longest timeout configured in a settings file.
- The hook is the same script for every event. It dispatches on
  `hook_event_name`.

---

## Setting it up: Codex

Two mechanisms, and they compose. **The tailer is the dependable one**; the hook
is lower-latency.

### The tailer (recommended, no configuration inside Codex)

```bash
export GT_CLOUD_ORIGIN=https://your-deployment.example.com
export GT_CLOUD_SESSION=<cloud session id>
export GT_CLOUD_TOKEN=<your session JWT>

python cloud/adapters/codex/gt_cloud_codex.py            # follow the newest session
python cloud/adapters/codex/gt_cloud_codex.py --file <rollout.jsonl> --from-start
```

It finds the newest rollout under `$CODEX_HOME/sessions` (`~/.codex/sessions`,
`%USERPROFILE%\.codex\sessions` on Windows), registers it, and follows it. By
default it starts from the **end** of the file: a session that has been running
for an hour should not replay an hour of tool calls into a fresh card. Pass
`--from-start` to replay, `--once` to drain and exit, `--no-subagents` to report
only the thread you named.

Subagents need nothing extra. Codex writes each subagent thread to its own
rollout file whose first line declares its parentage, so the watcher registers
each new one with `parent_agent_id` set to the agent it registered for that
file's `parent_thread_id`, labelled with Codex's own `agent_nickname`. A
subagent file whose parent is *not* a thread we are following is skipped rather
than shown at the root, because putting it at the root would misrepresent the
tree.

### The hook (optional, same script as Claude Code)

Codex reads hooks from `~/.codex/hooks.json` — its own file, not `config.toml`.
Merge [`cloud/adapters/codex/hooks.snippet.json`](../../cloud/adapters/codex/hooks.snippet.json)
and set `GT_CLOUD_AGENT_KIND=codex` so the card is labelled as Codex.

Codex 0.153.3 ships JSON Schemas for its hook payloads and their fields are
Claude Code's, so **the same script handles both**. Its tool *names* differ
(`exec`, `apply_patch`, `shell_command`), which is exactly why every matcher is
`"*"` and why `payloads.py` reads fields rather than switching on tool names.

Codex's `notify` setting in `config.toml` — an array of a program plus event
names, e.g. `notify = ["/path/to/program", "turn-ended"]` — also exists and is
real, but it fires **once per turn** with no tool detail. It is not used here
and is not a substitute for either mechanism above.

---

## Configuration

| Variable | Meaning |
|---|---|
| `GT_CLOUD_ORIGIN` | Deployment origin. Required. |
| `GT_CLOUD_SESSION` | Cloud session id to attach the card to. Required for registration. |
| `GT_CLOUD_TOKEN` | The user's JWT, used **only** to register. |
| `GT_CLOUD_AGENT_TOKEN` + `GT_CLOUD_AGENT_ID` | An already-registered agent. Registration is skipped and events stream straight in. This is what a child process is handed; it never sees the user's JWT. |
| `GT_CLOUD_AGENT_KIND` | `claude-code` (default for the hook), `codex`, or `other`. |
| `GT_CLOUD_DEBUG` | `1` turns on the debug log. Off, the adapters are completely silent. |
| `GT_CLOUD_DEBUG_LOG` | Where that log goes. Default `<tempdir>/gt-cloud-adapter.log`. |
| `GT_CLOUD_STATE_DIR` | Where registrations are cached. Default `<tempdir>/gt-cloud-adapters`. |
| `GT_CLOUD_TIMEOUT` | Per-request timeout, **capped at 3.0 s**, and at **1.5 s** in hook mode. Lowering it works; raising it does not. |
| `GT_CLOUD_BREAKER_SECONDS` | How long the circuit breaker stays open. Default 300; ×4 for a fatal status; `0` disables it. |
| `GT_CLOUD_FLUSH_INTERVAL` | Coalescing interval for the background thread. Default 1.5 s. |
| `GT_CLOUD_QUEUE_MAX` | Bounded queue size. Default 2000. |
| `GT_CLOUD_RETRIES` / `GT_CLOUD_BACKOFF` | Retry count (default 2) and base backoff (default 0.4 s). |
| `GT_CLOUD_HOOK_DEADLINE` | How long one hook invocation may spend reporting. Default 3 s; the watchdog exits 1.5 s after it. |
| `CLAUDE_PROJECT_DIR` | Set by Claude Code. Used as the root that file paths are made relative to, in preference to `cwd`, which moves during a session. |

---

## What each adapter can and cannot see

### The Claude Code hook

**Sees**, all measured: every tool call and its result, with the tool's own
`tool_input`; file paths from `file_path` and from the tool's response
(`Glob`'s `filenames`, `Read`'s `file.filePath`); the prompt, as the agent's
`task`; the final reply of each turn from `last_assistant_message`; subagent
start and stop; a per-agent token count read from the transcript.

**Attribution is exact, not inferred.** A tool event that fires inside a
subagent carries that subagent's `agent_id`, and it is the same id that
`SubagentStart` and `SubagentStop` carry. Measured: a `Glob` run by an `Explore`
subagent arrived with `agent_id: "ad3891f2b37c94b26"`, matching its
`SubagentStart`. No `agent_id` means the main agent. That is the whole rule.

**Does not see, or sees badly:**

| Gap | Detail |
|---|---|
| **A subagent's label can be swapped between siblings.** | `PreToolUse` on the `Agent` tool carries the human `description`; `SubagentStart`, which fires next, carries the id but only the agent *type*, and nothing links the two. The description is parked and claimed by the next `SubagentStart` of the same type. Two subagents of the **same type** spawned in one batch can therefore trade labels. Their tool calls are still attributed correctly — only the name on the card is at risk. |
| **The main card is not finished in headless mode.** | `SessionEnd` was **not observed** in a `claude -p` run; only `Stop`, which reports `idle`. In `-p` the main card is left idle rather than done. Interactive sessions do fire `SessionEnd`. |
| **A backgrounded subagent finishes at `SubagentStop`, not at the tool call.** | Since v2.1.198 subagents run in the background by default: `PostToolUse` on `Agent` returns immediately with `status: "async_launched"` and no usage. Measured. Only a *foreground* call returns `status: "completed"` with `totalTokens`. |
| **Which files a shell command touched is a guess.** | `extract_paths_from_command` splits the command and keeps path-shaped tokens. `grep -r foo src/` reports `src`; `cat a.py > b.py` reports both without knowing which was written. It is a hint about *where* the agent is working, not a record of what changed. Structured tool inputs (`Edit`, `Write`, `Read`) are exact. |
| **A file outside the project root is invisible.** | Dropped by the path conversion, by design. Editing outside the repository shows as a tool call with no files. |
| **One POST per hook invocation.** | Hooks are separate short-lived processes, so the bridge's coalescing cannot help them; the batching and the background thread only do real work in the two tailers. Against a healthy deployment this is one request per tool call. |
| **No usage before the first model response.** | The token count comes from the transcript, which has no assistant record yet at the first tool call. `tokens` is omitted, not zero. |

**How the token count is computed**, since it is the one number that could
mislead: the transcript at `transcript_path` is JSONL whose `{"type":"assistant"}`
records carry `message.usage`. The reported figure is `input_tokens +
cache_creation_input_tokens + cache_read_input_tokens + output_tokens` from the
**last** such record — one record, not a sum across records, because each
record accounts for one API call and the last call re-reads the whole
conversation out of the prompt cache; summing them all would multiply-count the
cached prefix. A subagent has its own transcript, handed over directly by
`SubagentStop` as `agent_transcript_path` and otherwise derived as
`<project>/<session_id>/subagents/agent-<agent_id>.jsonl` and checked on disk.

### The Codex tailer

**Sees:** command executions with Codex's own pre-parsed file paths
(`parsed_cmd[].path`), file changes with the exact set of paths patched, agent
messages, plans, turn start and completion, cumulative token usage, and the full
subagent tree with each child's nickname and depth.

**Does not see:**

| Gap | Detail |
|---|---|
| **Nothing until the line is flushed to disk.** | The card lags the terminal by however long Codex buffers, plus the poll interval (default 0.5 s). |
| **Reasoning and user messages are deliberately dropped.** | `Reasoning` is the model's private scratchpad and `UserMessage` is the human's own text. Neither belongs on a card that shows what the agent is *doing*. |
| **Only `event_msg` lines are read.** | Codex's normalised event stream — the same items its own UI renders — is far steadier than the raw `response_item` tool-call records underneath it. A tool that appears only as a `response_item` is not reported. |
| **Shell commands are invisible in `legacy` history mode.** | Codex has two history modes, declared as `session_meta.history_mode`, and they surface different records. `paginated` sends everything as `item_completed`, including `CommandExecution`. `legacy` — **the default**, and 340 of the 786 rollout files on the machine this was written on — has no `item_completed`; file edits arrive as `patch_apply_end` and replies as `agent_message`, both of which are read, but command executions appear only as raw `response_item` / `custom_tool_call` records whose argument is a JavaScript snippet, and those are **not** parsed. On a legacy session you see edits, replies, subagents and tokens, but not the commands. |
| **A compressed rollout is skipped.** | Codex can compress rollout files; only `rollout-*.jsonl` is picked up. A compressed session reports nothing rather than reporting garbage. |
| **A subagent whose parent is not being followed is skipped.** | Better a missing card than a lie about the tree. |
| **Nesting is clamped at depth 4.** | `source.subagent.thread_spawn.depth`. |
| **`--once` and a rotated file.** | If Codex replaces the rollout file, the tailer restarts from offset 0 rather than reading garbage, which can re-send recent events. |

### The generic tailer

Sees exactly what it is fed, and nothing else. `--map` renames fields
(`--map "name=tool,command=cmd"`); it deliberately **cannot compute** values,
because a mapping language that could would be a way to smuggle logic into a
config file, and the failure mode of a wrong expression is a card full of
plausible nonsense. If a rename is not enough, write four lines of Python that
print the contract shape and pipe them in. Malformed, empty and unrecognised
lines are counted and skipped; the finish summary reports the three counts.

---

## How the host contracts were verified

Neither vendor's documentation was taken on trust, and in two places it was
wrong or absent.

| Claim | How it was established |
|---|---|
| Claude Code's `PostToolUse` result field is **`tool_response`**, not `tool_output` | Claude Code hooks reference, `#posttooluse-input`. A first pass through the documentation reported `tool_output`; the reference's own JSON example says `tool_response`, and the live capture confirms it. This is why the adapter reads fields defensively. |
| The subagent-spawning tool is **`Agent`**, not `Task`; its input is `description`, `prompt`, `subagent_type`, `model` | Hooks reference `#agent`, and measured live: `"tool_name": "Agent"` with exactly those input keys. `Task` is accepted as the older spelling. |
| Hooks fire **inside** a subagent and carry `agent_id` / `agent_type` | Measured. A `Glob` inside an `Explore` subagent arrived with `agent_id: "ad3891f2b37c94b26"`, `agent_type: "Explore"` — the same id as its `SubagentStart` and `SubagentStop`. Parent-level events carry neither key. |
| Subagents are backgrounded by default | Measured: `tool_response` was `{"isAsync": true, "status": "async_launched", "agentId": "ad3891f2b37c94b26", ...}`. |
| The transcript's usage shape | Measured: `{"input_tokens": 8, "cache_creation_input_tokens": 227, "cache_read_input_tokens": 39541, "output_tokens": 60, ...}` on a `{"type":"assistant"}` record. |
| A subagent transcript lives at `<project>/<session>/subagents/agent-<id>.jsonl` | Found on disk after the same run. |
| **Codex has a hooks system**, at `~/.codex/hooks.json`, with Claude Code's config shape | The file exists on this machine with `hooks.<Event>[].matcher` and `hooks[].type: "command"`. |
| Codex's hook payload fields | **Extracted from the shipped binary.** Codex 0.153.3 embeds JSON Schemas titled `pre-tool-use.command.input`, `post-tool-use.command.input`, `subagent-start`/`-stop`, `session-start`/`-end`, `stop`, `user-prompt-submit`, `permission-request`, `pre-`/`post-compact` and `interrupt`. `post-tool-use.command.input` requires `cwd`, `hook_event_name`, `model`, `permission_mode`, `session_id`, `tool_input`, `tool_name`, `tool_response`, `tool_use_id`, `transcript_path`, `turn_id` — Claude Code's fields plus `model` and `turn_id`, the latter annotated in the schema itself as "Codex extension". |
| Codex's rollout format and subagent parentage | Read from real rollout files. Each line is `{"timestamp", "type", "payload"}` (plus `ordinal` in paginated mode only, and an occasional `metadata`); a subagent's `session_meta` payload carries `thread_source: "subagent"`, `parent_thread_id`, `agent_nickname`, `agent_path` and `source.subagent.thread_spawn.{parent_thread_id, depth, agent_nickname}`. |
| Codex's two history modes | Measured across all 786 rollout files on this machine: every file with `session_meta.history_mode: "paginated"` (396) carries `item_completed` records and an `ordinal` on each line; every `legacy` file (340) carries neither, and uses `agent_message` / `patch_apply_end` / `sub_agent_activity` instead. 51 older files declare no `history_mode` and behave as legacy. Both paths are implemented. |
| Codex's hook tool names | Codex maps its tools onto Claude-Code-style hook names with aliases: shell reports as `Bash`, edits as `apply_patch` (aliases `Write`, `Edit`), subagents as `spawn_agent` (alias `Agent`). The adapter accepts all of these and, more importantly, never switches on a tool name to find a path or a command. |
| Codex publishes these schemas | The same schemas extracted from the binary are checked into `openai/codex` at `codex-rs/hooks/schema/generated` as `<event>.command.input.schema.json`. The binary was used as the source of truth here because it is the version actually installed. |
| Both adapters end to end | Run against a real capturing server: a real `claude -p` session produced a parent card and a correctly-nested, correctly-attributed `Explore` child; a real Codex session produced one parent and seven nested subagents with repo-relative paths and monotonic token counts. |

**Not verified, and treated as unknown:** whether `SessionEnd` fires in an
interactive Claude Code session — it did not in `-p`; whether Codex's hooks fire
with the same `agent_id` attribution inside a subagent as Claude Code's do (the
schema has the field, but no Codex subagent hook payload was observed, which is
the other reason the rollout tailer is the recommended Codex route); and the
`tool_response` shape for every individual tool, which neither vendor documents
exhaustively and which `payloads.py` therefore probes rather than assumes.

---

## Security model

| Concern | How it is handled |
|---|---|
| **The ingest token is a secret scoped to one agent.** | It authorises writing events to **one** card and finishing it. It is never the user's JWT. `Bridge.child_env()` hands a child process the ingest token and agent id and nothing else, so a spawned process cannot register new agents or read anything. |
| **The user's JWT is used only to register.** | It is sent to the registration route and nowhere else. Streaming and finishing use the ingest token. |
| **The registration cache is a secret on disk.** | The state file under the temp directory holds an ingest token. It inherits the temp directory's permissions and nothing more. It expires after 24 h and is deleted on `finish()`. On a shared machine, set `GT_CLOUD_STATE_DIR` somewhere only you can read. |
| **Ingested text is data, never instructions.** | Everything the adapters send is text an agent produced or a file path it named. Nothing in a `tool_result`, an `assistant` text or a `note` is interpreted by the adapters, and the server and UI must treat it the same way. An agent that reads a hostile file will send its contents; that is a display concern, not an execution one. |
| **Paths are display-only.** | `files` is a list of strings for the UI to render and to light up in the graph. The server does not open them, and the conversion drops anything outside the reported `cwd`, so a path traversal has nothing to traverse to. |
| **Secrets in commands are not redacted.** | A command line containing a token is reported as the agent ran it. The `activity` phrase names only the program, but `command` carries the line. Do not point an adapter at a session where that matters. |
| **No inbound channel.** | The adapters only POST. Nothing the server returns is executed, and a response body is read only for the agent id and ingest token. |
| **The debug log is off by default.** | It is written only when `GT_CLOUD_DEBUG=1`, and it records messages and exception text — not tokens. |

---

## Failure modes

| Failure | What happens |
|---|---|
| Nothing configured | The hook exits 0 in a few milliseconds and reports nothing. The tailers print one line to stderr and exit 2. |
| **Server unreachable — the case that matters** | A hook makes **one** attempt of at most **1.5 s**, so a tool call pays at most that; on the **third** consecutive failure the circuit breaker opens and every tool call for the next five minutes pays a single file read. Measured end to end against a black-holed address (a host that hangs rather than refusing), invoking the hook as a real subprocess: **2.06 s, 2.66 s, 2.22 s, then 0.55 s, 0.47 s, 0.39 s** of wall clock. About 0.4 s of every one of those is the Python interpreter starting; after the breaker opens, essentially all of it is. Exit code 0 throughout. |
| Deployment comes back | The first invocation after the window probes it. Success closes the breaker and reporting resumes; failure re-opens it at once, without starting the count again. |
| Token revoked, or the agent closed on the server | The first 401/403/404/410 disables that bridge quietly, clears its queue, **and opens the breaker for four times the normal window** — retrying a revoked token has no upside. No retry storm. |
| The breaker file is corrupt, or the state directory cannot be written | Treated as "no memory": the adapter reports normally and rebuilds the state. A lost cache is never allowed to become a failed hook. Tested with empty, truncated, wrong-typed and binary content, and with a state directory that is a file. |
| The queue fills | The oldest events are dropped and counted; the next batch carries a `status` note with the count, and so does the finish summary. |
| A single enormous tool output | Truncated at emit time with a `... [N more characters]` marker, so one 500 KB output cannot blow the 256 KB batch ceiling. |
| Garbage on the hook's stdin | Exit 0. Tested with empty input, `not json`, a JSON array and `null`. |
| Something in the adapter hangs anyway | A watchdog thread exits the hook process with code **0** after `GT_CLOUD_HOOK_DEADLINE + 3 s`. It exits 0 deliberately: a watchdog must not turn a slow report into a failed tool call. |
| Two hook processes race to register | The second waits on a directory mutex and then finds the first one's state file. A lock older than 15 s is assumed abandoned and taken over — a duplicate card is a smaller problem than a wedged hook. |
| A half-written line in a transcript | Skipped. Both tailers hold an incomplete trailing line over to the next poll, and the token reader ignores a record it cannot parse. |
| The host agent's exit code | Never touched. Every failure path in the hook returns 0 with empty stdout, which is "no decision" for every Claude Code and Codex hook event. |

---

## Tests

[`tests/test_cloud_adapters.py`](../../tests/test_cloud_adapters.py) — 45 tests,
no network and no mocked transport: a real `http.server` on an ephemeral port is
the receiving end, so the `urllib` path under test is the one that runs on a
user's machine. The Claude Code payload fixtures are captured from a real
2.1.263 run, not written by hand.

```
python -m ruff check cloud/adapters/ tests/test_cloud_adapters.py
python -m pytest tests/test_cloud_adapters.py -q
```
