# Testing and CI

Everything below is as committed at `e12f5b65`. Test-function counts are
`def test_` counts in each file, so parameterised cases collect higher than the
number shown; the totals quoted from commit messages are the collected figures.

- [The rule these suites follow](#the-rule-these-suites-follow)
- [Server suites](#server-suites)
- [UI suites](#ui-suites)
- [Running them](#running-them)
- [What skips, and why](#what-skips-and-why)
- [CI](#ci)
- [Live verification](#live-verification)
- [The QA rounds](#the-qa-rounds)

---

## The rule these suites follow

Each test module declares a **fake boundary** in its docstring, and most declare
*none*. The endpoint suites fake exactly one thing — the model provider — and
run everything else for real: a real uvicorn server on a loopback port, real
JWT auth, a real SQLite database, the real event bus and SSE encoder, the real
agent turn loop, real `bash` subprocesses on the real filesystem, and real git
against a real seed repository that is really cloned and really diffed.

The two seams that are not behaviour are scheduling: a `_GitCloneRedirector`
rewrites the *remote* in the `git clone` argv to a local seed repository (the
HTTP body still carries a real `https://github.com/...` URL, so route validation
is exercised), and a thin wrapper around `SessionManager._create_blocking` lets a
test hold the workspace worker at its entry point so pre-clone assertions are not
racy. Both wrappers run the real code they wrap.

---

## Server suites

| File | Tests | Fake boundary | Covers |
|---|---|---|---|
| [`tests/test_cloud_chat.py`](../../tests/test_cloud_chat.py) | 47 | The LLM only (`ScriptedModel`) | The whole chat API against a live server: auth, `/health`'s build stamp, create → clone → idle, a turn streamed end to end, cross-turn memory, mid-turn steering, stop, step limit, the wall-clock budget, `/diff` and `/diff?through_event=`, snapshot truncation and the slow-snapshot cutout, `/graph`, close, the idle reaper (expired vs user vs never-reaped-while-running vs TTL 0 vs activity vs stop-counts-as-activity vs at-startup), `Last-Event-ID` replay and rejection, environment failure ending only the turn, a persisted GT error, listing order, every validation code, every offered `gt_mode`, ref and model validation, the model preflight, blank messages, `turn_started.content`, the disk floor, the workspace quota (including a `dd`-style filler), interrupted-turn recovery on the wire, and creation concurrency. |
| [`tests/test_cloud_agents.py`](../../tests/test_cloud_agents.py) | 20 | The LLM only (reuses the same harness) | Worker agents with `SANDBOX_MODE=local`: workers are children that run and report; a worker runs its task unprompted; the report survives a reload; frames are mirrored with `agent_id`; apply merges files; a conflicting apply names the paths and changes nothing; apply with nothing to apply, and apply against a non-idle parent or a foreign worker; parent close cascades; a worker closes on its own; spawns over the creation cap and over the worker cap create nothing; a worker cannot spawn workers; body validation; `first_message` starting the first turn; and the four `/spawn`-in-chat cases. |
| [`tests/test_cloud_conversational_agent.py`](../../tests/test_cloud_conversational_agent.py) | 35 | The LLM and the shell (`FakeModel`, `FakeEnv`) | The turn loop itself: transcript continuity, the text-only-reply rule, question detection, provider-error envelopes never becoming replies, format-error retries and the consecutive limit, steering drain order, stop, `fail_turn`, the wall-clock watchdog, per-turn step budget shifting, the `is_reply` assistant frame, `_last_thought` fallback, and the context truncator. |
| [`tests/test_cloud_sandbox.py`](../../tests/test_cloud_sandbox.py) | 56 | Unit half: none (the argv *is* the boundary). Integration half: none, real Docker | The `docker run` argv (user, caps, limits, mounts, network, `--init`, restart policy), the exec argv and its in-container `timeout`, the env allow-list and credential scrub, `is_exec_failure`, recreate-and-retry, the OOM mapping, `prepare_workspace` permission bits, reaping, and the proxy's allow-list — imported from the file that ships in the proxy image, so the two copies are asserted identical. The integration half builds both images, starts a real sandbox on a temporary workspace and proves the bind mount, the uid, the timeout and the egress policy. |
| [`tests/test_cloud_workspace.py`](../../tests/test_cloud_workspace.py) | 17 | None — a real git repository | Clone (including the full-SHA fetch path and the abbreviated-SHA fallback), the sanitised clone error text, `compute_diff` with untracked files and the `.gt_state` exclusion, `split_patch_by_file`, `cap_diff`, `list_tree`, `apply_patch` (clean, conflicting, and the index restored afterwards), the `_WRITES` twin, and the disk helpers. |
| [`tests/test_cloud_store.py`](../../tests/test_cloud_store.py) | 23 | None — a real SQLite database | Schema version, the transition table, unknown-field rejection, message and turn round-trips, totals, `touch`, `idle_sessions_before`, children, diff snapshots and event append/replay. |
| [`tests/test_cloud_codegraph.py`](../../tests/test_cloud_codegraph.py) | 18 | None for imports; the GT graph db is a real SQLite file written with the indexer's schema | Python (absolute, relative, `src/` layout), JS/TS specifier resolution, Go module paths, Rust `mod`/`use`, the binary and size guards, node fields, the `MAX_NODES` cap, the GT edge-kind mapping, path normalisation, and fail-open on a broken database. |
| [`tests/test_cloud_auth.py`](../../tests/test_cloud_auth.py) | 16 | None — real JWT encode/decode | The OAuth state, token TTL, bearer parsing, cookie vs header, expiry and tampering, and the per-request allow-list. |
| [`tests/test_cloud_environment.py`](../../tests/test_cloud_environment.py) | 6 | None — real `bash -c` subprocesses | Credential scrubbing, `bash` resolution, the timeout kill, and the interrupt path — the same one `request_stop()` drives in production, so a pass here means a live Stop really does kill the command in flight. |
| [`tests/test_cloud_typed_scopes.py`](../../tests/test_cloud_typed_scopes.py) | 14 | None | HAR-85: the pure normaliser (glob reduced, plain path untouched, non-existent prefix untouched, absolute and `..` refused, dedupe) and the real typed-action code path. |
| [`tests/test_cloud_compose.py`](../../tests/test_cloud_compose.py) | 7 | None — the compose file *is* the artefact | Restart policies, healthchecks, and `ui` depending on `server` being **healthy**. |
| [`tests/test_gt_snapshot_state_dir.py`](../../tests/test_gt_snapshot_state_dir.py) | 6 | None — a real git repository | HAR-86: writes under the state dir leave `_snapshot_authority` unchanged, a real edit still changes it, an untracked file outside the state dir is still hashed, an explicitly configured state dir is the one excluded, `.git` is always excluded, a state dir outside the repository is a no-op, and `GT_STATE_DIR` is honoured. |

Reported at `9c394863`: **306 passed / 4 skipped**.

[`tests/test_cloud_gt_events.py`](../../tests/test_cloud_gt_events.py) landed
with the typed-action package in `9c0212d5`: **24 tests** over the payload
builder against real `gt.compiled_observation.v1` shapes (exact answer,
abstention, enum-valued semantics, mapping-valued `coverage`, argument
truncation, the omission cap, unparseable output, every `match_count` shape, and
producer scope never echoing the request back), the HAR-85 scope-normalised case,
and ordering plus tallies through the real turn loop with a `FakeGtRuntime`
standing in for GT's `execute_actions` replacement. `test_cloud_chat.py` and
`test_cloud_agents.py` gained the HTTP-level and mirroring cases. The only stub
is `execute_typed_action_fail_open`, which ships in the server image's vendored
`groundtruth` wheel. Totals after it: **333 passed / 4 skipped** (306 before).

---

## UI suites

Vitest, `environment: "node"`, `include: ["src/**/*.test.ts"]`. The config is
deliberately separate from `vite.config.ts` — the production image builds the
bundle with `npm run build`, and nothing in that path should have to resolve
vitest. Only the **pure data layer** is tested; component tests would need
jsdom, and the bugs these layers produce do not.

| File | `it()` blocks | Covers |
|---|---|---|
| `src/__tests__/chatState.test.ts` | 30 | The thread reducer: event folding, turn grouping, message linking, orphan steering, and keeping a worker's mirrored frames out of the primary turn. |
| `src/__tests__/contract.test.ts` | 34 | The API contract as the UI understands it: `EVENT_TYPES` (including `gt_action` and the four worker frames), `GT_MODES` and their help text, `lifecycleToSessionStatus`, `streamUrl` flooring, the wall-second bounds, `CAP_REASONS`, `agentIdOf`. |
| `src/__tests__/workers.test.ts` | 29 | Worker cards folded out of the parent's stream: spawn, mirrored activity, reports, apply and its conflicts, closure, hue assignment by spawn order, `workerNo`, and the nested `/resume` rows. |
| `src/__tests__/terminal.test.ts` | 16 | The terminal grammar's pure parts: the GroundTruth line (`gt.ts`, both the `gt_action` frame and the typed-action `tool_call` fallback), the status verbs (`verbFor`), and the theme helpers. |
| `src/__tests__/prompt.test.ts` | 24 | `launch.ts` (creation stages, `combinePrompt`, `createAndStart`, file counting), `prefs.ts` normalisation, `slash.ts` parsing and suggestions, and `parseSpawn`'s refusals. |
| `src/__tests__/graph.test.ts` | 19 | Particle field construction, relation folding, cluster hue, radius, the `MAX_PARTICLES` cap, neighbour lookup. |
| `src/__tests__/sync.test.ts` | 20 | `sessionSync` — snapshot ordering (the round-1 P0 that wedged the header on *Working*) — and `streamSync` ingest, dedupe and terminal detection. |
| `src/__tests__/trail.test.ts` | 18 | Step kinds, the `WRITES` twin, file matching, attention decay, call counting. |

Reported at `e12f5b65`: **214 Vitest tests** collected, with `tsc --noEmit` and
`vite build` clean.

## Running them

```bash
# server, everything
python -m pytest tests/test_cloud_*.py -v --tb=short

# one suite
python -m pytest tests/test_cloud_chat.py -q
python -m pytest tests/test_gt_snapshot_state_dir.py -q

# lint (CI runs exactly this)
ruff check cloud/

# UI
cd cloud/ui
npm ci
npx tsc --noEmit
npm test
npm run build
```

Install first: `pip install -e ".[cloud,miniswe]"` plus `pytest` and
`pytest-asyncio`. Python 3.12 is required.

---

## What skips, and why

| Skipped when | Which |
|---|---|
| No Docker daemon | The integration half of `tests/test_cloud_sandbox.py` (`needs_docker`), which builds both images, starts a real sandbox and drives the egress policy. The first such run pays for the sandbox image build (~2 min: Debian plus build-essential plus node); later runs hit the layer cache. |
| No outbound network from the host | The egress-policy assertions inside that half explicitly `pytest.skip` when `github.com` is unreachable, rather than failing a policy test on a connectivity problem. |
| Not POSIX | `prepare_workspace` permission-bit assertions. This is why the `a+rwX` bug (`80be612b`) was caught on the Linux CI runner and not on Windows. |
| The GroundTruth wheel is absent | GT-dependent tests around `gt_engine`. The cloud suites themselves do **not** require it: `typed_scopes` defers its GT import, `gt_events` reimplements `is_typed_action` for the same reason, and `_prepare_gt` / `_install_gt` degrade rather than raise. The HAR-86 commit records one pre-existing failure in the neighbouring typed-action tests that needs the wheel — identical on the untouched tree. |

`SANDBOX_MODE` is `local` in the chat and agent suites, so no container is
needed to exercise sessions, turns, workers, apply or the reaper.

---

## CI

[`.github/workflows/cloud_harness_ci.yml`](../../.github/workflows/cloud_harness_ci.yml),
on push to `main` and `cloud/internal-harness`, and on PRs to `main`. Three
independent jobs:

| Job | Steps |
|---|---|
| `test` | Python **3.12 only** (matrix of one), `pip install -e ".[cloud,miniswe]"` plus pytest and pytest-asyncio, then `python -m pytest tests/test_cloud_*.py -v --tb=short`. |
| `lint` | `ruff check cloud/`. |
| `ui` | Node 20, `npm ci`, `npx tsc --noEmit`, `npm test`, `npm run build`. |

The 3.11 leg was removed in `f6c393d0`: the package requires >=3.12, so 3.11
failed at `pip install` on every push and `fail-fast` cancelled the 3.12 leg
before any test ran — the branch never had a green run until then. `fail-fast`
is now `false` as well.

CI runs the cloud suites only. `SANDBOX_MODE` is unset there, so it is `local`,
and the docker-dependent sandbox tests skip on the runner.

---

## Live verification

Tests are not the only evidence on this branch. Three artefacts run against real
deployments:

| Artefact | What it does |
|---|---|
| [`cloud/sandbox/verify.sh`](../../cloud/sandbox/verify.sh) | End-to-end against a running compose deployment: mints a JWT inside the server container, creates a session on `pallets/click@main`, dumps lifecycle events and a `docker inspect`, runs one turn that writes a file and curls an allowed and a denied host, then checks the diff, the host bind mount, the proxy log, and that close removed both the container and the workspace. `off` and `advisory` both pass. See [operations.md](operations.md#verifying-a-deployment). |
| [`docs/cloud-e2e-run.md`](../cloud-e2e-run.md) | The first live run: six cases (memory across turns, mid-turn steering, stop, validation, close, GT advisory) against a real server and OpenRouter, cited by SSE event id, including a stop attempt that raced and is recorded as such rather than re-run until green. |
| [`docs/cloud-gt-run.md`](../cloud-gt-run.md) | GT indexing on the Codespaces deployment: the certified producer's failure on `pallets/click`, the patched producer's `-build-info`, and the resulting 62 839-node / 79 216-edge index with a real turn on top of it. |

Two more record what the audits found and what changed:
[`docs/cloud-audit-fixes.md`](../cloud-audit-fixes.md) (G-01 … G-23, with
before/after evidence per gap) and
[`docs/cloud-qa-round2-fixes.md`](../cloud-qa-round2-fixes.md).

---

## The QA rounds

| Round | Method | Outcome |
|---|---|---|
| **Browser QA** (`807f6510`) | Driving the real UI against a live `pallets/click` session | A stale refetch wedging the header on *Working* (two `/sessions` responses in flight, the older applied last) — fixed by applying snapshots in issue order; `close()` had no control; labels saturated the graph; the fixed-width inspector crushed the layout. |
| **Round 2** (`8ce95004`, `fa6a4a23`) | The live codespace deployment: a real GT-advisory session, a real editing turn, a mid-turn message, stop, reload | Tool frames were invisible under GT (0/0 persisted) — fixed by moving emission into `_EmittingEnvironment`; stop took 18.5 s — now 0.16 s; deploy hygiene, `BUILD_SHA` and `deploy.sh`; reload re-relaxed the graph layout (fixed, measured at 0.00 px drift); `python -c` added to the write regex. |
| **The audit** (`15f845a0`, `91f6f779`) | A structured server/ops review, 23 numbered gaps | P0: no restart policy, `gt_mode: engine` broken, a forking workload bricking a session. P1: any exception killing the session, provider errors laundered into replies, SHA refs failing, no disk quota, restart-interrupted turns never ending on the wire, two tabs disagreeing, no per-user authorisation, an invalid model buying a full session. P2: G-12 … G-23. Deferred: G-14, G-18. |
