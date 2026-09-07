# GT Cloud: a coding agent that runs on GroundTruth

**Branch:** [`cloud/internal-harness`](https://github.com/harneet2512/gt-harness/tree/cloud/internal-harness) · **head:** [`37489dc5`](https://github.com/harneet2512/gt-harness/commit/37489dc5)

Most coding agents rediscover your repository every turn. GT-Harness already does the opposite — graph the repo, retrieve exact evidence, hand the model less context but better context, and receipt what happened. That stack was built for benchmarks (Terminal-Bench, DeepSWE). This branch ships the missing surface: **a cloud agent that executes GT-Harness on a real checkout.**

## What it is

You sign in with GitHub. You point it at a repo. You type a task.

The agent clones the tree, indexes it with GroundTruth when you ask it to, and works in a persistent workspace — optionally inside a Docker sandbox with locked-down egress. Every turn streams live: bash, GT typed actions, diffs, steering, stop. When the turn ends you get a receipt, not a vibe.

The UI is Synapse: a terminal you already know how to read, and a particle graph of the codebase so you can see *which files the agent is touching* while it works.

## Why this exists

GT-Harness was never just an eval harness. The mini-SWE loop plus GroundTruth’s typed actions is already a coding agent — it just lived behind benchmark runners. The cloud agent is that loop with a session, a workspace, and a browser.

What you get that chat-only agents don’t:

- **Execution, not advice** — real shell, real git, real apply
- **GroundTruth in the loop** — `off / advisory / assistive / enforced`, with typed actions on the stream
- **Receipts** — one per turn, with steps, wall time, GT action counts
- **A map** — file-relation graph from imports and GT edges, not a file tree cosplay

## What’s in the box

**Workers.** `/spawn` a few child agents on subtasks. They report back. You apply their patches into the parent with a 3-way merge, or you don’t.

**External agents.** Run Claude Code or Codex on your laptop with a one-file adapter. Their sessions — and their subagents — show up in the same UI, same hues, same graph. The cloud agent doesn’t have to be the only agent in the room.

**Ops that don’t pretend.** Idle sessions get reaped. Turns have wall-clock budgets. Sandboxes fail closed. Codespaces deploy is `deploy.sh`, not a myth.

## Recent work on this branch

| | |
|---|---|
| [`37489dc5`](https://github.com/harneet2512/gt-harness/commit/37489dc5) | Several agents at once stay legible on the particle graph |
| [`b0c0c656`](https://github.com/harneet2512/gt-harness/commit/b0c0c656) | Stale tunnel URLs can silently serve someone else’s tunnel — documented |
| [`b17da228`](https://github.com/harneet2512/gt-harness/commit/b17da228) | External agents can register their own subagents |
| [`05fccf9b`](https://github.com/harneet2512/gt-harness/commit/05fccf9b) | Three defects found only against a live Claude Code run |
| [`0a807933`](https://github.com/harneet2512/gt-harness/commit/0a807933) | Claude Code and Codex (and subagents) visible in the cloud UI |
| [`0c55de9d`](https://github.com/harneet2512/gt-harness/commit/0c55de9d) | Devcontainer base that actually starts with docker-in-docker |

Full branch vs main: https://github.com/harneet2512/gt-harness/compare/main...cloud/internal-harness

## Start here

- Product / quickstart: [`cloud/README.md`](../../cloud/README.md)
- Architecture: [`docs/cloud/architecture.md`](./architecture.md)
- External agents: [`docs/cloud/external-agents.md`](./external-agents.md)
- Tests: [`docs/cloud/testing-and-ci.md`](./testing-and-ci.md)
