# GT cloud coding agent — as-built documentation

As-built documentation for the internal cloud coding agent on branch
`cloud/internal-harness` (Linear **HAR-84**, with **HAR-85** and **HAR-86**
alongside it). Everything here describes the code that is committed at
`9c394863` (*feat(HAR-84): worker coding agents — spawn, report, apply*).

Two packages are being changed by other agents while this was written and are
marked **in progress** wherever they appear:

| In-progress package | Where | Documented as |
|---|---|---|
| Claude-Code terminal re-skin + worker-agent wiring in the browser | `cloud/ui/src/**` | current committed UI, plus a target section per doc |
| GroundTruth typed-action events | `cloud/server/gt_events.py`, `tests/test_cloud_gt_events.py` | intended behaviour only — the file is untracked at `9c394863` |

## What this is

A **Claude-Code-style cloud coding agent whose engine is the mini-SWE +
GroundTruth harness**. You sign in with GitHub, type a task, and an agent works
in a persistent clone of a repository — in a container, on a machine you do not
own — while you watch every command it runs and steer it mid-turn.

The thesis of the ticket is that the harness this repository already has (the
mini-SWE agent loop plus GroundTruth's repository intelligence, benchmarked on
Terminal-Bench 2.0 and DeepSWE) is a *product*, not only an evaluation rig. The
same `DefaultAgent` step loop that scores on a benchmark, given a persistent
transcript, a session-scoped workspace and a chat surface, is a coding agent.
Nothing about the engine was forked to make that true: `ConversationalAgent`
subclasses mini-SWE's `DefaultAgent`, and GT is installed onto it through
`gt_engine.miniswe_runtime.install_runtime_hooks` exactly as the benchmark
harness installs it.

What that buys, and what nothing else in the category has: a **receipt per
turn**, a **file-relation graph** built from GT's own symbol edges, and an
agent that can be told *"the evidence must be exact and complete or abstain"*
(`gt_mode: enforced`) rather than asked to be careful.

## The docs

| Document | What is in it |
|---|---|
| [architecture.md](architecture.md) | Components and boundaries, the session state machine, the turn loop and its step boundaries, the event bus and SSE contract, diff snapshots, the file graph, worker agents, typed-scope normalisation, the producer build, the HAR-86 snapshot fix. Three diagrams. |
| [user-guide.md](user-guide.md) | Signing in, the prompt-first landing, talking to the agent, steering, stop, `/spawn` and applying a worker's diff, the graph and inspector, receipts, what each GT mode does, slash commands and keys. Ends with the in-progress terminal grammar. |
| [api.md](api.md) | Every REST endpoint and status code, every SSE event type and payload field, the `Session` / `Message` / `TurnReceipt` shapes, and the auth rules. |
| [operations.md](operations.md) | Prerequisites, the OAuth app, every environment variable, `deploy.sh`, Codespaces specifics, image builds, restart/health, the reaper, quotas, the disk floor, logs, verification, recovery, and the SQLite drop-and-recreate. |
| [security.md](security.md) | The threat model as built: what is isolated, what is not, secrets, the egress allow-list, resource caps, authorisation, and the known gaps. |
| [testing-and-ci.md](testing-and-ci.md) | Every test suite and what it covers, how to run them, what skips without Docker or the GT wheel, the CI workflow, the live verification scripts and the QA rounds. |
| [decisions.md](decisions.md) | The decision log, dated, with the commit that carries each decision. |
| [changelog.md](changelog.md) | Every commit on the branch, grouped by day. |
| [known-limitations.md](known-limitations.md) | Everything deferred or not done, with the reason and where it lives. |

## The notes these consolidate

These predate this set and are still the primary evidence for their subjects.
They are linked rather than copied:

| Note | Subject |
|---|---|
| [`cloud/README.md`](../../cloud/README.md) | The product README: quickstart, the API in brief, worker agents, deploy. |
| [`docs/cloud-vm-substrate.md`](../cloud-vm-substrate.md) | Actions vs Codespaces vs a plain VM, and what the Codespaces deployment actually took. |
| [`docs/cloud-sandbox.md`](../cloud-sandbox.md) | Sandbox design, threat model, configuration, operations, evidence, limitations. |
| [`docs/cloud-e2e-run.md`](../cloud-e2e-run.md) | The first live end-to-end run: six cases against a real server and OpenRouter, cited by event id. |
| [`docs/cloud-gt-run.md`](../cloud-gt-run.md) | GT indexing on the Codespaces deployment, and why the producer is built from source. |
| [`docs/har85-literal-search.md`](../har85-literal-search.md) | HAR-85: why `exact_literal_search` abstained on a complete graph. |
| [`docs/cloud-qa-round2-fixes.md`](../cloud-qa-round2-fixes.md) | Round-2 QA: tool frames under GT, stop latency, deploy hygiene. |
| [`docs/cloud-audit-fixes.md`](../cloud-audit-fixes.md) | The audit, gap by gap (G-01 … G-23), with before/after evidence. |
| [`docs/upstream-groundtruth-issue.md`](../upstream-groundtruth-issue.md) | The upstream bug text filed as harneet2512/groundtruth issue #5. |
| [`cloud/producer/README.md`](../../cloud/producer/README.md) | Why the cloud image builds `gt-index` from source, and what the patch does. |

## External references

- Linear **HAR-84** — the cloud coding agent. **HAR-85** — glob scopes made
  concrete before GT's literal search. **HAR-86** — the harness state
  directory excluded from GT's working-tree snapshot.
- GitHub **harneet2512/groundtruth issue #5** — `gt-index` aborts the entire
  resolution graph on one derivation-invalid candidate. **PR #6**
  (`fix/gt-index-skip-invalid-candidates`) — the fix this image ships as the
  `cloud.2` producer variant.
