# Decision log

Every decision that shaped the cloud coding agent, with the date it landed and
the commit that carries it. Dates are the commit dates on
`cloud/internal-harness`.

| # | Decision | Date | Commit |
|---|---|---|---|
| [1](#1-codespaces-over-a-plain-vm) | Deploy on Codespaces, not a plain VM | 2026-09-04 | `767f00f2`, `ceed00b9` |
| [2](#2-mini-swe-not-nano) | The engine is mini-SWE, not `nano/` | 2026-09-04 | `767f00f2` |
| [3](#3-one-transcript-across-turns) | One mini-SWE transcript across every turn | 2026-09-04 | `a26d02a6` |
| [4](#4-a-text-only-reply-ends-the-turn) | A text-only model response ends the turn | 2026-09-04 | `a26d02a6` |
| [5](#5-the-ui-on-port-80-same-origin) | Serve the UI on port 80, same-origin | 2026-09-04 | `0426bd7a` |
| [6](#6-build-the-gt-producer-from-source) | Build the GT producer from source with a patch | 2026-09-04 / 09-05 | `f4329e44`, `4e610350` |
| [7](#7-scope-normalisation-in-the-cloud-not-gt_engine) | Normalise typed scopes in `cloud/`, not `gt_engine/` | 2026-09-04 | `72b63291` |
| [8](#8-a-sandbox-per-session-with-an-allow-listed-proxy) | A sandbox container per session, plus an egress proxy | 2026-09-05 | `a64fa592` |
| [9](#9-a-turn-can-fail-a-session-cannot) | A turn can fail; only creation writes off a session | 2026-09-05 | `15f845a0` |
| [10](#10-the-gt_mode-enum-correction) | `gt_mode` is a validated enum; `engine` is gone | 2026-09-05 | `15f845a0`, `91f6f779` |
| [11](#11-a-wall-clock-budget-because-cost-is-untracked) | Budget turns in wall-clock seconds, not cost | 2026-09-05 | `24f9e0fb` |
| [12](#12-har-86-as-its-own-commit) | Keep the HAR-86 `gt_engine` fix as its own commit | 2026-09-05 | `4ebf8dbe` |
| [13](#13-workers-are-sessions) | A worker agent is a full child session | 2026-09-05 | `9c394863` |
| [14](#14-the-ui-directions) | UI: dashboard → Survey → Synapse → prompt-first → terminal | 2026-09-04 → 09-05 | `dd41057f`, `4bde9d98`, `54532f86`, `e12f5b65`, `645fe276` |
| [15](#15-the-free-model-choice) | Run on a free tool-calling model | 2026-09-04 | `docs/cloud-e2e-run.md` |
| [16](#16-drop-and-recreate-on-a-schema-bump) | Drop and recreate the database on a schema bump | 2026-09-04 | `a26d02a6` |

---

## 1. Codespaces over a plain VM

`docs/cloud-vm-substrate.md` evaluated three substrates against five
requirements (container per session, mid-run steering, persistent SSE, dynamic
repo ingress, cost transparency) and **recommended a plain VM with Docker**:
best isolation, native steering, no proxy limitations, near-zero cold start,
full control of egress policy.

**We built it on Codespaces anyway**, because it needed no infrastructure to
exist: the repo already had a devcontainer, and one `gh codespace create`
produced a public HTTPS origin with a certificate. GitHub Actions was ruled out
outright — a runner job is non-interactive, has no inbound HTTP, and therefore
cannot do steering or SSE at all. IBM/UpCloud-style plain VMs remain the
recommended end state, and the delta is small: the same compose file runs
unchanged; what goes away is port-forward registration, the per-codespace OAuth
hostname and the free-hours ceiling; what arrives is your own TLS, your own
patching, a real disk for `WORKSPACES_HOST_DIR`, and no idle auto-stop.

The cost shape is the reason it is not the end state: a 4-core machine burns 4
core-hours per wall-clock hour, so the free monthly allowance is roughly 30 h on
Free and 45 h on Pro of *running* time — fine for demand-driven demos, nowhere
near an always-on service.

## 2. mini-SWE, not nano

The repository has two agent loops: `nano/` (the smallest readable loop) and the
mini-SWE integration under `eval/` and `gt_engine/`. The product is built on
**mini-SWE**, because that is where the GroundTruth integration already lives:
`gt_engine.miniswe_runtime.install_runtime_hooks`,
`gt_engine.miniswe_integration.MiniSweAdapter` and
`gt_engine.miniswe_typed_actions` are all written against mini-SWE's
`DefaultAgent` and `LitellmModel`. Choosing nano would have meant reimplementing
the GT hook surface for a second loop, and the product's whole claim is that the
benchmarked engine *is* the product engine.

The cost of that choice is inherited: mini-SWE's `LitellmModel._query` always
sends `tools=[BASH_TOOL]` and reads `tool_calls` back, so **a model that answers
in prose instead of emitting a tool call cannot drive this harness at all** —
which is what forces decision 15.

## 3. One transcript across turns

`ConversationalAgent.messages` is built once and grows for the life of the
session. The agent's memory is its real trajectory, not a summary, and it is
persisted to `.gt_state/transcript.json` and restored on the next turn.

The alternative — one mini-SWE run per message, seeded with a summary — was
rejected because it makes "the shell you drive and the working tree you edit
survive between turns" a lie the prompt tells. The cost is that the transcript
grows without bound, which is why `_truncate_context()` collapses the oldest
tool observations past `MAX_CONTEXT_CHARS` while never touching a user message
or an agent reply.

## 4. A text-only reply ends the turn

mini-SWE raises `FormatError` when a response carries no tool call. In a
benchmark that is a malformed response to retry. In a chat product it is *the
agent talking to you*, and it ends the turn.

So `_handle_format_error` splits three ways: a provider error envelope is an
`error` and never enters the transcript (HAR-84 G-05); a text-only assistant
message is the reply, appended to the transcript and emitted as an `assistant`
frame with `is_reply: true`; anything else keeps the retry path. The
`is_reply` flag exists because that call was billed and counted before the parse
failed — without the frame, a client counting assistant frames trails
`turn_finished.n_calls` by one.

The `submit` instruction was removed from the system prompt for the same reason:
a chat session never ends by submitting a patch. The legacy marker is still
handled (`finish_reason: "submitted"`) and rendered as a file-by-file summary.

## 5. The UI on port 80, same-origin

The UI is built by Vite and served by **nginx on port 80**, which also
reverse-proxies `/api`, `/auth` and `/health` to `server:8000`. Not `vite
preview` on 5173.

The reason is OAuth. A codespace's forwarded port becomes
`https://<name>-80.app.github.dev`, and that exact origin has to be the OAuth
App's callback host. Same-origin also means `UI_ORIGIN=/` is correct, no CORS
middleware is needed at all, and the credentialed cookie works without
`SameSite` gymnastics. `proxy_buffering off` plus a 3600 s read timeout on
`/api/` is what keeps the SSE stream alive through nginx; without it the feed
buffers and the UI looks frozen.

## 6. Build the GT producer from source

The vendored `vendor/gt-index-linux-amd64` is the certified benchmark producer.
Its derivation invariant aborts the entire resolution graph on **one**
candidate lacking typed-source or propagation facts. Indexing `pallets/click`
parsed 131 files, built 1361 nodes, resolved 2757 calls — and exited 1. Every GT
session on that repo degraded to `gt_unavailable`.

Three options were available: relax the invariant at runtime (impossible — it
has no environment gate), ship the certified binary and accept that arbitrary
repositories cannot be indexed (which removes the product's differentiator), or
build a patched producer for the cloud image only. The third was chosen, with
three constraints that keep the benchmark path honest:

1. The same pinned commit, the same byte-for-byte CI build recipe.
2. `main.commitSHA` stamped `<sha>+<variant>` so `gt-index -build-info` can never
   match the certified manifest, and the build fails if provenance is incomplete.
3. The vendored binary stays in the repository, unpatched, and the product
   workflow keeps byte-comparing it.

`GT_PRODUCER_ARTIFACT`, `GT_TASK_ID` and `GT_PRODUCT_SOURCE_SHA` are left unset
so `gt_engine` stays in `local_unbound` scope — correct-or-quiet — rather than
failing closed on a binary that is deliberately not certified.

**`cloud.1` → `cloud.2` (2026-09-05, `4e610350`).** The first patch was a
hand-written one-hunk hack that only skipped in the final insert loop: the
abstained candidate still got a `CANDIDATE_TARGET` edge, a `DerivationFact` node
and VTA flow facts, so `QueryAttachedCandidates` could surface it as attached
evidence with no backing derivation, and the only trace of a skip was a log line.
`cloud.2` is the port of upstream **PR #6** (filed as issue **#5**): candidates
are partitioned before `prepareResolutionV2`, and the skip count is persisted to
`project_meta.graph_resolution_skipped_candidates`. `gt-index/internal/store/` is
byte-identical between the pinned commit and the PR's base, so the upstream diff
applies verbatim — there is no cloud-local reimplementation any more. When PR #6
merges into the pinned commit, `cloud/producer/` can be deleted.

## 7. Scope normalisation in the cloud, not `gt_engine`

HAR-85: `exact_literal_search` abstained on a complete graph. The cause was not
the graph (`nodes_fts` held 62 839/62 839 rows) but the *scope*: the producer
stats every `paths` entry as a concrete filesystem path, and planners write
globs, which name no file.

The fix could have gone in `gt_engine/` — where the typed-action model lives —
or in `cloud/`. It went in `cloud/server/typed_scopes.py`, as a
`GroundTruthLitellmModel` subclass overriding `_parse_actions`, because
`gt_engine/` is the benchmarked harness: changing how a scope is interpreted
there would change what the benchmark measures. The benchmark path is untouched;
only sessions the cloud runner builds get the normalisation.

The reduction is deliberately narrow — only strings containing a glob
metacharacter, never absolute or `..` scopes, never onto a non-existent prefix,
and always widening — so evidence stays complete and can never overclaim.

## 8. A sandbox per session, with an allow-listed proxy

Phase-1 requirement. One container per session (`gt-sandbox-<session_id>`),
started after the clone and before GT indexing, removed on close. The workspace
is bind-mounted so the server keeps indexing and diffing the *same* files the
agent edits — the container is an execution jail, not a copy of the tree.

Two sub-decisions worth recording:

- **Fail closed.** A sandbox that will not start fails the session. There is
  deliberately no fallback to local execution: it would silently drop both the
  isolation and the egress policy while looking like success.
- **`chmod`, not `chown`.** `--cap-drop ALL` takes `CAP_CHOWN` away from root
  inside the container too, and dropping every capability is worth more than tidy
  ownership. Ownership staying with the server also keeps git's dubious-ownership
  check quiet on the server's own `git diff`.

The proxy is stdlib-only, in its own image, duplicating the allow-list with a
test that asserts the copies stay identical — because it cannot import the
server package.

## 9. A turn can fail; a session cannot

Before `15f845a0`, any per-turn exception killed the whole conversation. Now
`_fail_turn` ends the *turn* in `error`, writes a reply the user can read, closes
the receipt, and returns the session to `idle`. Only a failed **workspace
creation** writes off a session, because that is the one failure where there is
nothing left to talk to.

The same principle runs through the module: GT degrades rather than failing, a
bookkeeping write that fails is swallowed (`_call_quietly`), a diff snapshot is
never worth a turn, and the reaper's bad pass never ends the loop.

## 10. The `gt_mode` enum correction

`gt_mode` was an unvalidated string, and both the docs and the UI offered
**`engine`** as the flagship mode. `engine` was never a member of
`gt_engine.gt_session.GTMode`, so every such session raised `ValueError:
'engine' is not a valid GTMode` on its first turn and silently degraded to
`gt_status: unavailable` (HAR-84 G-02).

It is now a `Literal["off","advisory","assistive","enforced"]` — a 422 at
creation rather than a broken session — and the UI list matches the server's
exactly. `assistive` and `enforced` both reach a real turn with `gt_status:
ready` and `gt: true` graphs (551 and 550 edges on `pallets/click`), which is
exactly where `engine` used to fall over. `shadow` is a real member but is not
offered: it runs the engine without letting it affect the agent, which is a
benchmark mode, not a product one.

## 11. A wall-clock budget, because cost is untracked

`app.py` sets `MSWEA_COST_TRACKING=ignore_errors` before mini-SWE's model module
is imported, because LiteLLM aborts a run when it cannot price a model and the
free OpenRouter models have no price entry. The consequence is that `cost` is
always `0.0` — so **steps were the only budget a turn had, and a step is not a
unit of time**: one `pytest` invocation can outlast fifty `grep`s.

`TURN_WALL_SECONDS` (default 900, per-session `wall_seconds` 60..3600) is the
other half. A `threading.Timer` watchdog interrupts the command in flight through
the same seam `/stop` uses, and the boundary check ends the turn with
`finish_reason: "time_limit"` and a reply that says where the agent got to.
`wall_seconds` lands on the receipt and `total_wall_seconds` on the session, and
the UI labels the cost column *untracked* rather than showing `$0.00`.

## 12. HAR-86 as its own commit

The state-dir exclusion is the **only** change to `gt_engine/**` on this branch.
It is deliberately a separate commit (`4ebf8dbe`) rather than folded into a cloud
commit, so it can be reviewed, cherry-picked or reverted on its own — it touches
the benchmarked harness, and everything else on the branch does not.

It is also strictly a correctness fix, not a behaviour change: GT was hashing its
own receipts and trajectory as part of the working-tree identity, so every typed
action taken mid-turn reported `repository_revision_mismatch` +
`working_tree_sha256_mismatch` against changes only GT had made.

## 13. Workers are sessions

A worker agent could have been a second agent inside one session, sharing the
workspace. It is instead a **full child session**: its own row, clone, container,
transcript and receipts, with `parent_id`, `role: "worker"` and `task`.

That costs more — a clone and a container per worker, and a creation slot and a
turn slot each — but it buys: workers cannot corrupt each other's or the
parent's tree; a worker's result arrives as a **patch** the user chooses to
apply; and every existing mechanism (close, the reaper, receipts, the diff
endpoint, recovery) works on a worker unchanged.

The consequences follow from that shape. Applying is a real 3-way merge that is
all-or-nothing and names its conflicts. `apply` must happen **before** `close`,
because closing deletes the clone. Workers are one level deep. And the parent's
stream is the only one a client watches: worker frames are mirrored onto it with
`agent_id`, which is the whole protocol.

## 14. The UI directions

| Direction | Verdict |
|---|---|
| **Generic dark dashboard** (`767f00f2`) — dark theme, three-pane terminal feed, session list, steering chat | **Rejected.** Called "a slop fest". It looked like every other agent tool and showed nothing about what makes this one different. |
| **"Ledger"** — paper, serif, receipts as the organising metaphor | **Rejected before implementation.** A known trope; the receipt is a feature, not an aesthetic. |
| **"Survey"** (`dd41057f`) — a squarified treemap of the workspace, cells lighting as the agent reads and edits, with a radio-log conversation | **Rejected after implementation.** A treemap shows file *size*, which is not interesting. The product's differentiator is that GroundTruth knows the *relations*. |
| **"Synapse"** (`4bde9d98`) — every file a particle, every relation a filament, force-laid-out; agent activity as signal travelling along edges; click-to-inspect like an IDE | **Accepted.** It renders what GT actually provides. The step/path-inference model from Survey was kept as `trail.ts`. |
| **Prompt-first landing** (`54532f86`) — one composer, the repository inferred from the message, model and budgets behind a gear | **Accepted.** The landing was a configuration form; "give it a task and it works" is the product. |
| **Terminal grammar** (`e12f5b65`, refined by `645fe276`) — Claude Code's own grammar: `>` prompt, `⏺`/`⎿` lines, a spinner status line with *esc to interrupt*, box-drawn banner and input, the `/` palette, `/resume`, `/theme`, dark by default with a light terminal theme | **Accepted and shipped.** The product is a Claude-Code-style coding agent, so it should look like one; the differentiators — `GroundTruth(...)` evidence lines, `Agent(worker-N · task)` cards, `Receipt(turn N)`, and the code graph as a tmux-style split pane — are rendered in that same grammar rather than bolted beside it. This supersedes the earlier light-mode-only preference; the light theme survives as an option. |

## 15. The free model choice

The deployment runs `nvidia/nemotron-3-super-120b-a12b:free` through OpenRouter's
OpenAI-compatible route. It was chosen by **probing for native tool calls**, not
by benchmark rank: mini-SWE always sends `tools=[BASH_TOOL]` and reads
`tool_calls` back, so a model that answers in prose cannot drive the harness at
all. A direct `chat/completions` probe with a `bash` tool definition returned
`finish_reason: "tool_calls"`, and across 8 turns and 22 model calls it never once
returned prose where a tool call was required. `google/gemma-4-31b-it:free` is
unusable — a persistent upstream 429.

Two consequences are accepted rather than fixed: **cost is untracked** (decision
11), and the picker offers four models with the rest as free text, so a paid model
can be used per session without a config change.

## 16. Drop and recreate on a schema bump

`SessionStore.init()` compares `PRAGMA user_version` against `SCHEMA_VERSION` and
rebuilds every table when they differ. There is no migration path.

This was chosen because the product is an internal tool whose durable artefacts —
the clone, the transcript, the trajectory, the index — live on disk in the
workspace, not in the database; the database holds session bookkeeping. Six
schema versions landed on this branch in two days, and writing six migrations for
data nobody was keeping would have been wasted work.

The cost is real and is documented under
[operations.md](operations.md#the-database-is-dropped-on-a-schema-bump):
deploying a schema-bumping commit erases every session, message, receipt and
event, and leaves the workspace directories orphaned on disk.
