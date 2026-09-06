# User guide

How to use the cloud coding agent. This describes the UI as committed at
`e12f5b65` — the Claude Code terminal look, with worker agents wired end to end.

- [The grammar](#the-grammar)
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
- [Theme](#theme)
- [Closing a session](#closing-a-session)

---

## The grammar

The whole page is a terminal transcript, and it is made of two line shapes.
Everything — the agent's prose, a command, a GroundTruth query, a worker, a
receipt — is one of them (`cloud/ui/src/components/TermLine.tsx`):

```
⏺ I need to see how the option parser is wired.
⏺ Bash(rg -n "class Option" src/click)
  ⎿  src/click/core.py:2103:class Option(Parameter):
     … +37 lines (click to expand)
⏺ GroundTruth(exact_literal_search "class Option" in src/click)
  ⎿  2 matches · exact · complete
⏺ Agent(worker-1 · Add a docstring to Command.invoke)
  ⎿  $ rg -n "def invoke" src/click/core.py
  ⎿  ✓ reported · 2 files · a80d4c46  [apply] [open]
⏺ Receipt(turn 3) · 7 calls · 66s · untracked · a80d4c46 · GT ready
```

`⏺` is a thing that happened; `⎿` is what it said. Command output is clipped
at six lines with an expander (`TermOutput.tsx`). Boxes — the landing banner,
`/settings`, `/resume` — are drawn with characters (`╭─╮ │ ╰─╯`), not CSS
borders, so they behave like a terminal's (`Box.tsx`).

A **GroundTruth line is not a shell command and never reads as one**
(`gt.ts`). It is formatted from the server's `gt_action` frame — the kind, the
arguments, the scope actually searched, the semantics and coverage verdict and
the match count — and an abstention says so: `⎿ abstained:
COVERAGE_NOT_COMPLETE`.

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

The landing page is a **prompt, not a form**: a box-drawn banner naming what the
agent is pointed at (repo, GT mode, model), four tips, and one composer. You
type the task; the repository, the model and the budgets are inferred or
remembered (`TermBanner.tsx`, `LandingPage.tsx`).

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

2. **The most recent session's repo** — work where you last worked. The banner
   and the line under the composer both name whatever is currently in force.
3. **`?repo=` / `?ref=` query parameters**, which pre-select it.

If none of those name a repository, the agent answers *"Which repository should
I work in? Paste a GitHub URL."* and **keeps your intent**: the next message
carrying a URL starts the session with **both texts**, in the order you typed
them (`launch.ts:combinePrompt`). A `/spawn` typed with no repository known gets
its own version of that question, and asks you to send the `/spawn` lines again
once the repository is set.

### What sending does

One action creates the session and starts the first turn: the prompt is sent as
`first_message`, so the server runs it itself the moment the workspace is ready
(`launch.ts:createAndStart`, which falls back to creating and posting separately
against a server that does not accept the field). The page navigates to
`/sessions/<id>` with your prompt already on screen. While
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

`/settings` — a form drawn as a box **in the transcript**, at the point where you
asked for it (`TermSettings.tsx`). Four things, remembered in `localStorage`
under `synapse:prefs` and merged onto the defaults field by field, so one stale
key cannot take the prompt down:

| Setting | Default | Range |
|---|---|---|
| Model | `nvidia/nemotron-3-super-120b-a12b:free` | The picker lists four; anything else is free text. |
| GroundTruth mode | `advisory` | `off` / `advisory` / `assistive` / `enforced` |
| Step limit | 60 | 1..500 model calls per turn |
| Wall-clock budget | *(unset)* | 60..3600 s; unset means "use the server's `TURN_WALL_SECONDS`" |

Settings apply at **session creation**. Opened inside a live session the box says
so: changing them does not retune a session that is already running, only the
next one you start.

---

## Talking to the agent

The session page is a transcript. Each turn shows the agent's reasoning, each
command it ran and that command's output, inline, in order, in the grammar
above. A turn is one exchange: your message goes in, the agent works, and the
turn ends **when the agent talks to you** — either because it finished, or
because it has a question.

Your own messages are `>` lines, the way a shell echoes a prompt.

While a turn runs there is exactly one animated thing on the page — the status
line between the transcript and the input (`TermStatus.tsx`):

```
✻ Working… (12s · 3 steps · esc to interrupt)
```

A five-glyph spinner (`✻ ✽ ✶ ✳ ✢`) and a verb that follows what the agent is
actually doing, read from the last command it ran, so a long step still reads as
motion:

| Verb | When |
|---|---|
| Thinking | A model call is in flight; no command yet. |
| Reading | `cat`, `ls`, `rg`, `grep`, `find`, `git log/show/status/diff`, `sed -n`, … |
| Editing | A write-shaped command — the same test the server uses for diff snapshots. |
| Checking | `pytest`, `tox`, `npm test`, `make test`, `ruff`, `mypy`, `eslint`, `tsc`, … |
| Running | Anything else. |

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

Press **`esc`** while a turn is running — the status line says so — or type
`/stop`.

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

The draft is checked before the round trip (`slash.ts:parseSpawn`): a line that
is not a `/spawn`, a `/spawn` with no task, or more than four of them is
refused in the transcript with the reason, rather than sent and answered 400.
The message does not start a turn — the server records what you asked and
answers with a note listing the workers it created. Each worker then runs its
task as its own first turn, without being messaged.

**Watching them.** You do not subscribe to anything else: a worker's frames
arrive on *your* session's stream tagged with `agent_id`, and that tag is the
whole protocol — a frame that carries it belongs to that worker and never to
your own turn or step count. Each worker becomes one call in your transcript
(`TermWorker.tsx`):

```
⏺ Agent(worker-1 · Add a one-line docstring to Command.invoke)
  ⎿  $ rg -n "def invoke" src/click/core.py
     … +7 earlier commands
  ⎿  ✓ reported · 2 files · a80d4c46  [apply] [open]
```

Its own activity is folded to the last three rows — the primary transcript is
what you are reading, and a worker that ran forty commands must not take forty
lines of it. Click the fold to see the rest. A worker's GroundTruth queries are
drawn as GroundTruth lines there too, not as shell commands.

The status mark is `…` running, `✓` reported or applied, `·` closed.

**Colours.** Each worker gets one of four hues by spawn order, wrapping past the
fourth (`workers.ts:WORKER_HUES`). None of them is the primary agent's orange or
the edited-file teal, so a worker's trail on the graph is never mistaken for the
session's own.

When a worker finishes a turn it **reports** into your conversation — the reply,
the files it changed, and the patch's sha256 — and the report survives a reload,
because it is a real message and not only a frame.

**Taking the work.** `[apply]` merges that worker's diff into your workspace
with a 3-way merge; `[open]` navigates into the worker's own session, which has
a *back to parent* link at the top. Apply is all-or-nothing:

- The session must be `idle` — `[apply]` is disabled while a turn is running.
- A worker with no changes is refused.
- On conflict you get the **list of paths** git could not merge, on the card,
  and your workspace is byte-for-byte what it was.
- Apply **before** closing the worker: closing deletes its clone like any other
  session's, and the patch goes with it.
- Applying changes files, not the transcript. Your agent does not know it
  happened unless you tell it.

`/resume` nests workers under the session that spawned them, labelled with their
task, rather than listing them as four things you started.

---

## The code graph and the inspector

The graph is the thing this product has that a terminal does not: **every file in
the workspace as a particle, every relation between two files as a filament**,
laid out by a force simulation so related code clusters. It opens as a
**tmux-style split pane** beside the transcript, with a rule-drawn pane title —
`── graph · 166 files · GT ready · 2 workers ──` — rather than as a dashboard
panel. `Ctrl`/`Cmd`+`G` or `/graph` toggles it.

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
- Each live worker draws its own trail in its own hue, and the pane title counts
  them; a worker's trail can be isolated from the rest.

The **scrubber** replays the turn step by step. Its diff is the real stored
snapshot taken at that write (`/diff?through_event=N`), not a reconstruction.

GT status is stated where it matters rather than as a permanent badge: the
banner names the mode before a session exists, the graph pane title says
`GT ready`, and every `Receipt(turn N)` line ends with the GT status that turn
actually ran with. An unavailable index is a line in the transcript carrying the
reason, which survives a reload.

---

## Changes and receipts

The bottom panel has three tabs. It opens on **Changes**, because every step of
the turn is already inline in the transcript.

| Tab | What it shows |
|---|---|
| Trail | The steps of the turn, as rows. |
| Changes | The cumulative diff: every file the session has changed, with a two-tone patch. |
| Receipts | One row per turn. |

A **receipt** is the turn's record, and it is also a line in the transcript —
one `⏺ Receipt(turn N)` closing every finished turn, with the model calls, the
elapsed time, the cost, the patch sha and the GT status. Clicking it selects
that turn for the scrubber. The Receipts tab is the same data as a table, with
`gt_actions` / `gt_exact_matches` alongside.

Cost reads *untracked* rather than `$0.00`, because it is: pricing is disabled
for the free models this deployment uses, and wall-clock seconds are the honest
budget line.

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

Eight, all client-side, with a `/` palette that completes while you type the
name. A message that merely *starts* with a slash (`/usr/bin/env`,
`/api/sessions returns 409`) is a message, not a command: only a known name
followed by end-of-line or a space counts. `?` on an empty line opens the whole
palette.

| Command | Does |
|---|---|
| `/stop` | Stop the turn in flight. |
| `/close` | Close the session and discard its workspace. |
| `/graph` | Show or hide the code graph — `ctrl+g`. |
| `/resume` | Pick up a previous session — `ctrl+r`. |
| `/settings` | Model, ground truth and the per-turn budgets, as a box in the transcript. |
| `/spawn <task>` | Hand a task to a worker agent — one `/spawn` line per worker, up to 4. |
| `/theme [dark\|light]` | Switch the terminal theme; no argument toggles. |
| `/help` | List these commands. |

On the landing page, `/help`, `/settings`, `/resume`, `/theme` and `/spawn` all
work; `/stop`, `/close` and `/graph` answer *"There is no session yet."*

The command you typed is echoed into the transcript the way a shell echoes it,
above whatever it did — a multi-line `/spawn` is echoed whole, one line per
worker.

`/resume` is the session list, full screen, driven from the keyboard: `↑↓`
moves, `⏎` opens, `esc` closes, workers nested under the session that spawned
them with their task. It replaces a permanent sidebar, on the principle that a
list you ask for costs nothing when you are not asking for it.

---

## Keyboard

| Key | Does |
|---|---|
| `Enter` | Send. |
| `Shift`+`Enter` | Newline. |
| `esc` | Interrupt the running turn. |
| `?` (on an empty composer) | Open the command palette. |
| `Ctrl`/`Cmd`+`K` | Focus the composer. |
| `Ctrl`/`Cmd`+`G` | Toggle the code graph. |
| `Ctrl`/`Cmd`+`R` | Open `/resume`. |
| `Tab` / `Enter` | Accept the highlighted palette entry. |
| `↑` / `↓` | Move through the palette, or through `/resume`. |
| `esc` (with the palette or a box open) | Dismiss it. |

The hint under the composer says the ones that matter:
`? for shortcuts · /help · ⏎ send · shift+⏎ newline · esc interrupt`.

Below 1100 px the graph becomes an overlay and the inspector a slide-over with a
scrim; below 760 px the layout stacks.

---

## Theme

Two terminals: **dark by default**, with a light terminal theme behind
`/theme light` (`/theme` alone toggles). The choice is remembered in
`localStorage` under `synapse:theme`.

Every colour the page paints is a custom property on `:root`, so switching is
one attribute on `<html>` and nothing re-renders; the graph canvas cannot read a
CSS variable, so it re-reads the palette once per theme change rather than per
frame (`theme.ts`, `palette.ts`).

---

## Closing a session

`/close`, which asks *"Close this session? The workspace is discarded."* first.
Closing kills the turn, removes the sandbox container, **deletes the
workspace**, and closes the row — along with every live worker under it.
Everything the agent wrote is gone unless you took the diff first.

Sessions also close themselves: a session that has been `idle` for
`SESSION_IDLE_TTL_SECONDS` (default 6 h) is closed by the reaper in exactly the
same way, with `closed_reason: "expired"`. The transcript and `/resume` both say
which — *by you*, *expired*, or *failed*.
