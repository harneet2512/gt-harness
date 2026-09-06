# User guide

How to use the cloud coding agent. This describes the UI **as committed at
`9c394863`**; the terminal re-skin that supersedes parts of it is in progress
and is described at the end, under
[In progress: the terminal grammar](#in-progress-the-terminal-grammar).

- [Signing in](#signing-in)
- [The landing page: type a task](#the-landing-page-type-a-task)
- [Settings](#settings)
- [Talking to the agent](#talking-to-the-agent)
- [Steering mid-turn](#steering-mid-turn)
- [Stopping a turn](#stopping-a-turn)
- [Worker agents](#worker-agents)
- [The code graph and the inspector](#the-code-graph-and-the-inspector)
- [Changes and receipts](#changes-and-receipts)
- [GroundTruth modes](#groundtruth-modes)
- [Slash commands](#slash-commands)
- [Keyboard](#keyboard)
- [Closing a session](#closing-a-session)
- [In progress: the terminal grammar](#in-progress-the-terminal-grammar)

---

## Signing in

Open the deployment URL. Everything is behind GitHub OAuth: the sign-in card
offers **Continue with GitHub**, which is a full-page navigation to
`/auth/login` (the OAuth redirect has to leave the SPA). After the callback you
are returned to the app with an `HttpOnly` session cookie; the GitHub access
token never reaches the browser.

If the deployment sets `ALLOWED_GITHUB_LOGINS`, a login that is not on it gets a
403 — at the callback and on **every** subsequent request. Sessions last
`JWT_TTL_SECONDS` (default one day).

The card also prints `build <sha>`. It must match `/health`'s `commit`; if it
does not, the browser or compose is serving a stale bundle — see
[operations.md](operations.md#proving-what-is-deployed).

---

## The landing page: type a task

The landing page is a **prompt, not a form**. One composer, placeholder *"What
should I work on?"*. You type the task; the repository, the model and the
budgets are inferred or remembered.

### How the repository is chosen

`cloud/ui/src/repoUrl.ts`, in order:

1. **A GitHub URL anywhere in the message.** "Fix the flaky test in
   `https://github.com/pallets/click`" is a complete instruction — it names the
   work *and* the repository — and the page must not answer it with a form.
   Nothing is stripped from the message: the URL is a fact the agent may want
   too. Accepted forms:

   | You type | Repo | Ref |
   |---|---|---|
   | `https://github.com/pallets/click` | `pallets/click` | none |
   | `https://github.com/owner/name@cloud/internal-harness` | `owner/name` | `cloud/internal-harness` |
   | `.../tree/<ref>` (refs with slashes included) | that repo | `<ref>` |
   | `.../blob/<ref>/path/to/file.py` | that repo | `<ref>` |
   | `.../pull/12`, `.../issues/3` | that repo | none — no ref is guessed |

   A trailing `.git`, and sentence punctuation glued to the URL, are trimmed.

2. **The chip under the composer**, if you picked a repository explicitly.
3. **The most recent session's repo** — work where you last worked.
4. **`?repo=` / `?ref=` query parameters**, which pre-select the chip.

If none of those name a repository, the agent answers *"Which repository should
I work in? Paste a GitHub URL."* and **keeps your intent**: the next message
carrying a URL starts the session with **both texts**, in the order you typed
them (`launch.ts:combinePrompt`).

### What sending does

One action creates the session, navigates to `/sessions/<id>`, shows your prompt
already on screen, and posts it the moment the workspace reaches `idle` — the
server answers 409 until then, so the page holds it rather than failing. While
that happens the page shows the three creation steps in order:

```
cloning <repo>…  →  sandbox…  →  indexing…  →  workspace ready
```

(`launch.ts:CREATION_STEPS`, mapped from the `lifecycle` frames.) `indexing`
only appears when `gt_mode != off`.

If session creation itself fails — an unusable model, a private repository, a
host with no disk — the prompt stays in the box and the error is shown.

---

## Settings

Behind the gear next to the composer; also reachable with `/settings`. Four
things, remembered in `localStorage` under `synapse:prefs` and merged onto the
defaults field by field, so one stale key cannot take the prompt down:

| Setting | Default | Range |
|---|---|---|
| Model | `nvidia/nemotron-3-super-120b-a12b:free` | The picker lists four; anything else is free text. |
| GroundTruth mode | `advisory` | `off` / `advisory` / `assistive` / `enforced` |
| Step limit | 60 | 1..500 model calls per turn |
| Wall-clock budget | *(unset)* | 60..3600 s; unset means "use the server's `TURN_WALL_SECONDS`" |

Settings apply at **session creation**. Changing them does not retune a session
that is already running.

---

## Talking to the agent

The session page is a transcript. Each turn shows the agent's reasoning, each
command it ran and that command's output, inline, in order. A turn is one
exchange: your message goes in, the agent works, and the turn ends **when the
agent talks to you** — either because it finished, or because it has a question.

The header carries the session status:

| Status word | Meaning |
|---|---|
| Preparing | The workspace is still being made (with the current phase named). |
| Working | A turn is running, with a live step count and a stopwatch. |
| Waiting for you | The last reply was a question. |
| Idle | Ready for the next message. |
| Failed / Closed | Terminal, with the reason. |

A **step** is one model call. That is the same number as
`turn_finished.n_calls`, and the definition is enforced in `trail.ts`.

Other ways a turn can end, all shown honestly rather than as an error:

- **Step limit** — the reply says where the agent got to and offers *continue*.
- **Time limit** — the same, quoting the budget in minutes.
- **Stopped** — you pressed stop.
- **Error** — the reply says what failed; the **session survives** and stays
  usable.
- **Interrupted** — a server restart cut the turn short. The turn card closes
  with an *"interrupted by a server restart"* chip and a system note is written
  in place. The turn is **not** resumed.

---

## Steering mid-turn

Send a message while a turn is running. It is delivered at the agent's next
**step boundary** and appended to the same transcript, so the agent answers it in
context rather than in a new turn. The composer says so, and the message appears
in the thread as a steering line.

If the message arrives in the instant the turn is ending, it is not lost: the
server flips the session to `idle` first, drains again, and chains a follow-up
turn if anything was waiting.

---

## Stopping a turn

The **Stop** control in the header, `/stop`, or `Ctrl`/`Cmd`+`Shift`+`Backspace`.

Stop is honoured at the next step boundary, and the command in flight is killed
so that boundary arrives immediately — measured at 0.16 s on the live
deployment. The turn ends with `finish_reason: "stopped"`, the reply is
*"Stopped."*, and the session goes back to `idle`, fully usable.

The one case where it is slow: if the **model** is thinking rather than a command
running, the stop waits for that call, because the LiteLLM call is synchronous
and not cancellable. `MODEL_REQUEST_TIMEOUT` (default 300 s) is the only bound
on that. See [known-limitations.md](known-limitations.md).

---

## Worker agents

A worker is a second agent on the same repository and ref, with its own clone,
its own container and its own transcript, given one task and left to it.

Spawn from the chat box:

```
/spawn add a CHANGELOG entry for the parser fix
/spawn update the docstrings in src/click/types.py
```

Every non-blank line must be a `/spawn` line — a half-written command is refused
rather than run past a model as prose. At most four tasks per message, at most
four live workers per session, and a worker cannot spawn workers.

The message does not start a turn. The server records what you asked and answers
with a system note listing the workers it created. Each worker then runs its task
as its own first turn, without being messaged.

**Watching them.** You do not subscribe to anything else: a worker's frames
arrive on *your* session's stream tagged with `agent_id`, so each worker draws
its own trail. When a worker finishes a turn it **reports** into your
conversation — the reply, the files it changed, and the patch's sha256 — and the
report survives a reload because it is a real message, not only a frame.

**Taking the work.** Applying a worker's diff merges it into your workspace with
a 3-way merge. It is all-or-nothing:

- The session must be `idle` (409 otherwise).
- A worker with no changes is a 400.
- On conflict you get the **list of paths** git could not merge, and your
  workspace is byte-for-byte what it was.
- Apply **before** closing the worker: closing deletes its clone like any other
  session's, and the patch goes with it.
- Applying changes files, not the transcript. Your agent does not know it
  happened unless you tell it.

> **In progress.** At `9c394863` the committed UI answers `/spawn` with
> *"spawning worker agents is coming — the server side is being built"*. The
> server side is complete and the API works; the browser wiring is the
> in-progress package. Until it lands, spawn and apply through the API — see
> [api.md](api.md#worker-agents).

---

## The code graph and the inspector

The graph is the thing this product has that a terminal does not: **every file in
the workspace as a particle, every relation between two files as a filament**,
laid out by a force simulation so related code clusters.

Edges come from two places (see
[architecture.md](architecture.md#6-the-file-relation-graph)): static imports
parsed out of the source, and — when GroundTruth indexed the repo — GT's own
symbol edges (`gt_call`, `gt_import`, `gt_ref`) collapsed to file level.

- Files the agent **reads** flare and decay over six steps; files it **edits**
  are tinted; its current position carries a halo while working.
- The panel opens on the first turn that touches three or more files, and
  whatever you choose after that is remembered per session.
- Layout and camera persist per session in `localStorage`, and an unchanged
  graph does not restart the simulation — measured at 0.00 px drift across a
  turn with no relation change, and 1.9 px after a reload.
- Click a particle for the **inspector**: that file's live diff (refetched as the
  agent writes), its relations, and the exact steps that touched it. Clicking a
  step scrubs the graph back to that moment.

The **scrubber** replays the turn step by step. Its diff is the real stored
snapshot taken at that write (`/diff?through_event=N`), not a reconstruction.

The header badge names the GT mode and its status: `ready`, `indexing…`,
`unavailable` (with the reason, which survives a reload) or `off`.

---

## Changes and receipts

The bottom panel has three tabs. It opens on **Changes**, because every step of
the turn is already inline in the transcript.

| Tab | What it shows |
|---|---|
| Trail | The steps of the turn, as rows. |
| Changes | The cumulative diff: every file the session has changed, with a two-tone patch. |
| Receipts | One row per turn. |

A **receipt** is the turn's record: turn id, start and end, model calls, wall
seconds, `finish_reason`, the sha256 of the patch as of that turn, the GT status
the turn actually ran with, and the model it actually used. Cost is shown as
*untracked* rather than `$0.00`, because it is: pricing is disabled for the free
models this deployment uses.

---

## GroundTruth modes

Chosen at session creation, in the gear. What each mode does is the GT engine's
own behaviour — this product only selects it.

| Mode | What it does |
|---|---|
| `off` | No GroundTruth. No index is built, the graph has import edges only, and the agent is a plain mini-SWE agent. |
| `advisory` | Evidence is offered; the agent may ignore it. |
| `assistive` | Evidence is delivered and preferred. |
| `enforced` | GT controls tool routing, fail-closed: an answer is given only when the evidence is exact and complete, otherwise the engine abstains. |

`shadow` is a real GT mode but is deliberately not offered: it runs the engine
without letting it affect the agent, which is a benchmark mode, not a product
one. `engine` was offered once and **was never a GT mode at all** — every such
session raised on its first turn and degraded silently. It is now a 422 at
creation (HAR-84 G-02).

### What "unavailable" means

`gt_status: unavailable` means the index could not be built, or the engine could
not be installed on the agent. The session still works — it runs as a plain
agent, the graph falls back to import edges, and `gt: false` is reported
honestly. The reason is shown as a notice and persisted, so it is still there
after a reload. Common causes: the repository has nothing the indexer can index,
the indexer exited non-zero, or the `gt-index` binary / `groundtruth-mcp` wheel
is missing from the image.

---

## Slash commands

Six, all client-side, with autocomplete while you type the name. A message that
merely *starts* with a slash (`/usr/bin/env`, `/api/sessions returns 409`) is a
message, not a command: only a known name followed by end-of-line or a space
counts.

| Command | Does |
|---|---|
| `/stop` | Stop the turn in flight. |
| `/close` | Close the session and discard its workspace. |
| `/graph` | Show or hide the graph panel. |
| `/settings` | Model, ground truth and the per-turn budgets. |
| `/spawn <task>` | Hand a task to a worker agent. One per line. |
| `/help` | List these commands. |

On the landing page, only `/help`, `/settings` and `/spawn` mean anything; the
rest answer *"There is no session yet."*

The command you typed is echoed into the transcript the way a shell echoes it,
above whatever it did.

---

## Keyboard

| Key | Does |
|---|---|
| `Enter` | Send. |
| `Shift`+`Enter` | Newline. |
| `Ctrl`/`Cmd`+`K` | Focus the composer. |
| `Ctrl`/`Cmd`+`G` | Toggle the graph panel. |
| `Ctrl`/`Cmd`+`Shift`+`Backspace` | Stop the running turn. |
| `Tab` / `Enter` | Accept the highlighted slash-command suggestion. |
| `↑` / `↓` | Move through slash-command suggestions. |
| `Escape` | Dismiss the suggestions; close an overlay or the inspector on a narrow screen. |

Below 1100 px the conversation becomes a toggleable drawer and the inspector a
slide-over with a scrim; below 760 px the layout stacks.

---

## Closing a session

`/close`, or the Close control in the header (with a confirm). Closing kills the
turn, removes the sandbox container, **deletes the workspace**, and closes the
row. Everything the agent wrote is gone unless you took the diff first.

Sessions also close themselves: a session that has been `idle` for
`SESSION_IDLE_TTL_SECONDS` (default 6 h) is closed by the reaper in exactly the
same way, with `closed_reason: "expired"`. The switcher and the header say
which — *by you*, *expired*, or *failed* — and offer to start again.

---

## In progress: the terminal grammar

> This is the **target**, not what `9c394863` serves. It is being implemented in
> `cloud/ui/src/**` by another agent, alongside the worker-agent wiring above.
> The components exist in the working tree (`TermLine`, `TermStatus`,
> `TermOutput`, `TermActivity`, `TermWorker`, `TermSettings`, `Box`,
> `ResumePicker`, `theme.ts`, `palette.ts`, `gt.ts`) but are not committed.

The product is a Claude-Code-style coding agent, so the UI should look like one.
The whole transcript reduces to two line shapes, and everything — the agent's
prose, a command, a GroundTruth query, a worker, a receipt — is one of them:

```
⏺ I need to see how the option parser is wired.
⏺ Bash(rg -n "class Option" src/click)
  ⎿  src/click/core.py:2103:class Option(Parameter):
     … +37 lines (click to expand)
⏺ GroundTruth(exact_literal_search "class Option" in src/click)
  ⎿  2 matches · exact · complete
```

| Element | Target |
|---|---|
| Prompt | `>` at the input, in a box drawn with characters (`╭─╮ │ ╰─╯`), not CSS borders. |
| Activity | `⏺` for a thing that happened, `⎿` for what it said. Output clipped at six lines with an expander. |
| Status | One animated line between the transcript and the input: `✻ Working… (12s · 3 steps · esc to interrupt)`, with a five-glyph spinner and a verb that follows what the agent is doing (Thinking / Reading / Editing / Running / Checking). |
| GroundTruth | A typed GT action is **not** a shell command and must not read as one. It gets its own line with the kind, the arguments, the scope actually searched, and the semantics/coverage verdict. Until the server emits `gt_action`, the same line is recovered structurally from the `tool_call` command, which is the JSON of the typed action. |
| Workers | A worker is a line in the same grammar with its own tail of activity, a spawn number, and an apply action. |
| `/resume` | The session list, full screen, driven from the keyboard (`↑↓` moves, `⏎` opens, `esc` closes) — replacing the permanent sidebar rail. A list you ask for costs nothing when you are not asking for it. |
| `/settings` | Drawn as a box in the transcript, at the point where it was asked for. |
| Theme | Dark by default, with a light terminal theme behind a `/theme` toggle. Every colour is a custom property on `:root`, so switching is one attribute and the canvas re-reads the same variables. |
| The graph | Kept, rendered as a box-framed pane in the same grammar — a tmux-style split rather than a dashboard panel. |

`/resume` and `/theme` are not in the committed `slash.ts` command list; they
are part of this package.
