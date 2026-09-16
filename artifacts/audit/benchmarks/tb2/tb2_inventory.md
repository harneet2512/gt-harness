# Terminal-Bench 2.0 (89 tasks) — GT capability inventory and provider-free test plan

Companion data: `tb2_tasks.json` (89 rows). Corpus: `tb2repo/` (`harbor-framework/terminal-bench-2`
@ `69671fba`, the exact commit the frozen GT-off baseline ran) and `tb2harbor/` (the
`harbor dataset download terminal-bench@2.0 --export` output). Only `task.toml` differs (schema
1.0 → 1.1 metadata); `instruction.md`, `tests/test.sh`, `tests/test_outputs.py` and
`environment/Dockerfile` are byte-identical for all 89. Verdict vocabulary and audit layers follow
`D:\gt-context-plan\docs\AUDIT-ABILITY-SPEC.md` §5A and §4. Per its §4 TB2 boundary, **no
in-container Mini-SWE proof is inherited here** and the two host blockers stay open until their own
offline reproductions pass. Inspected identities: `D:\gt-harness` @ `a4431c1a`
(`eval/gt_central_agent.py`, `gt_engine/central_runtime.py`, `tests/test_gt_central_agent.py`),
`D:\gt-context-plan` @ `5fa957be`, wheel `site-packages/groundtruth/runtime/patterns.py`.

## (a) Task-shape breakdown

TB2 is not a repository benchmark. It is a container benchmark.

| property | /89 |
|---|---|
| workspace is a git repo | **7** (`crack-7z-hash`, `fix-code-vulnerability`, `fix-git`, `fix-ocaml-gc`, `make-doom-for-mips`, `make-mips-interpreter`, `sanitize-git-repo`) |
| any test-config marker a discovery probe would find | **4** |
| grading tests visible to the agent (deliberately `COPY`d into `/app`) | **5** |
| workspace ships its own runnable suite | **3** |
| agent cannot run the grading tests at all | **81** |
| zero payload files beyond the Dockerfile (built by `RUN`/clone/heredoc) | 26 |
| ≤ 3 payload files | 67 |
| `allow_internet` / GPUs / workdir `/app` | 89 / 0 / 86 |

Verification is uniform and post-hoc: Harbor mounts `tests/` at `/tests` *after* the agent phase, runs
`bash /tests/test.sh`, which installs pytest (82 via `uvx`, 7 via `pip`) and runs
`pytest /tests/test_outputs.py`, writing `/logs/verifier/reward.txt` (0|1) and a CTRF json. No task
deviates. Hidden suites are small: median 3 tests, 22 tasks have exactly 1. Assertion styles:
file-exists 75, spawns-a-subprocess 46, timing-sensitive 27, numeric-tolerance 11, network-service 9.

Languages skew exotic: `config/terminal_bench_2_language_contract.json` (`gt.tb2.language-contract.v1`,
14 witnesses, 22 suffixes) names coq, stan, R, sparql, turtle, latex, vim, nginx, gcode, cobol, scheme,
red, povray. Categories: software-engineering 26, sysadmin 9, scientific-computing 8, security 8,
data-science 8, tail. Difficulty: 55 medium / 30 hard / 4 easy.

Frozen GT-off baseline (`D:\gt_runs\miniswe_tb2_gtoff_20260731`; mini-swe-agent 2.2.8,
`eval.miniswe_agent:MiniSweAgent`, deepseek-v4-flash, multipliers 1.0): **66/89**, 4 `AgentTimeoutError`,
10 at the 100-call step limit, 238.5 M prompt + 4.0 M completion tokens, 15.9 h wall clock. Per-task
agent timeouts 600–12000 s (49 tasks at 900 s).

## (b) Capability × equivalence class, with verdicts

**Scope correction, and it is load-bearing.** The TB2 host arm does **not** run the engine machinery an
inventory of this kind usually assumes. In `eval/gt_central_agent.py`: `baseline`, `persistent_plan`,
`discover_command`, `rev-parse` and `lsp` each have **zero** occurrences. There is no LSP and no git
read anywhere on this path; `.git` appears only as a `find -prune` token. So `discover_command`'s
missing `tests/test.sh` probe, `BaselineResult.status`, the 20–120 s baseline budget and
`gt_session._mandatory_capability_rows` (including `dense_retrieval`'s unconditional `required=True`)
govern the **in-container** arms (`eval/miniswe_agent.py`, `eval/tb_agent.py`, which set
`GT_STATE_DIR=/logs/agent/gt-state`) — not TB2. Per spec §4 they are not inherited here.

What TB2 actually runs is `CentralFeatureRuntime` (`gt_engine/central_runtime.py:2037`), *"host-side
trigger router for the complete 17-feature inventory"*, which *"deliberately does not scrape task source
… observes only action metadata, command text, return status, and the non-Git workspace transition
already collected by WorkspaceSensor."* Triggers are regexes (`_SEARCH` over `rg|grep|find|ack|ag`,
`_DEFINITION`, `_CALLSITE`, `_EDIT`, `_SIGNATURE`, `_FAILURE`, `_PRECEDENT`). Anchors come from the
agent's own search output (`_search_anchors` :2201, `_search_observation` :2263); graph evidence is
*injected when available* via `register_structural_evidence` :2748. Delivery requires
`feature_payload_valid` (:576 — correct boundary, non-empty `revision`, `fresh`, feature-specific keys)
and model-visibility additionally requires `feature_payload_grounded` (:644 — concrete anchors, callers,
symbols, commands, blockers; *"generic booleans and scope reminders are never grounded"*).

Consequence: graph-derived abilities on TB2 are **degraded, not absent**. `CENTRAL_FEATURES` covers 17
of the 21 engine identities. **`cochange_prior` is absent from the host route entirely**;
`persistent_plan` / `plan_gate` are replaced by `persistent_execution_state` (35 occurrences).

Class groups: **A+B** = 8 tasks with real source and a runnable suite; **C** = 6 cloned repos with git
history; **D/E/F** = 75 tasks with 0–3 seed files, no repo. Evidence column cites the host route only.

| ability (spec §2) | A+B | C | D/E/F | evidence / limitation |
|---|---|---|---|---|
| Localization `localization`,`GT_LOC_RESLOT` | UNPROVEN | UNPROVEN | PARTIAL | trigger + anchors derive from the agent's own `rg`/`grep` output, so it fires without a graph; `_GROUNDING_REQUIREMENTS["localization"]=("anchors",)` is satisfiable. 1 host-agent hit, 2+3 test hits — no TB2-shaped fixture |
| Caller contracts `caller_contract` | UNPROVEN | UNPROVEN | **NOT APPLICABLE** (D/F) / UNPROVEN (E) | requires `callers_verified` + `callers`; no graph and no callable corpus on a 1-file task. Zero occurrences in agent and its tests |
| Co-change `cochange_prior` | **NOT APPLICABLE** | **NOT APPLICABLE** | **NOT APPLICABLE** | not a member of `CENTRAL_FEATURES`; the host route does not implement it, and no git read exists on this path |
| Search partitioning `def_partition` | UNPROVEN | UNPROVEN | PARTIAL | needs `definitions`+`references` and `definition_anchors`/`reference_anchors`, all obtainable from search output |
| New-file precedent `newfile_precedent`,`GT_CHANGE_SURFACE` | UNPROVEN | UNPROVEN | PARTIAL | uniquely permissive validity (`precedent_verified` **or** `created_files`, :618); but grounding needs `precedent_path`, and 67 tasks have ≤3 files, so sibling authority is thin — the spec's "do not invent registry or sibling authority" case is live here |
| Change consequences `signature_delta`,`GT_PATCH_DELTA` | UNPROVEN | UNPROVEN | PARTIAL | `_semantic_signature_deltas` (:2594) works off `WorkspaceTransition` file content, not a graph; grounding needs `symbol`+before/after signature |
| Edit validation `syntax_result`,`GT_EDIT_CHECK` | UNPROVEN | UNPROVEN | PARTIAL/NOT APPLICABLE | needs a certified post-image parser. Applies to the python/c/js tasks; genuinely N/A for `.gcode`, `.red`, `.scm`, `.v`, `.stan`, `.ttl`, `.conf` and data-only tasks. 2 test hits |
| Covering failures `covering_red` | UNPROVEN | **BROKEN** | **BROKEN** | boundary is `test_result`, needing an `execution_evidence kind=test` row. Verified empirically below: `bash tests/test.sh`, `uvx pytest`, `python script.py` and `./run.sh` produce no such row, so on the **81** tasks (C+D+E+F) where the agent cannot run the grading tests the boundary never occurs while the ability reports enabled |
| Recovery `recovery`,`GT_HYPOTHESIS` | UNPROVEN | **BROKEN** | **BROKEN** | boundary is `test_result` only (:558) — the repeated-action leg the engine has is not wired to a second boundary here, so the same non-test command repeating with no information gain cannot trigger it. Zero occurrences in agent and tests |
| Obligations `obligations` | UNPROVEN | UNPROVEN | UNPROVEN | prompt-only, boundary `task_start`, reachable on all 89. 9 agent hits / 2 test hits, none TB2-shaped. Instructions are short (median ~1 KB) so obligation density will be low |
| Persistent planning `persistent_plan` | **NOT APPLICABLE** | NOT APPLICABLE | NOT APPLICABLE | superseded on this route by `persistent_execution_state`, which emits `BootstrapStatus.NOT_APPLICABLE` with `reason_codes:["not_applicable_no_supported_source"]` (:4162-4173) — correct behaviour, but UNPROVEN as tested |
| Plan gate `plan_gate` | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | same; the host gate is `_preemptive_retrieval_gate_reason` (:1289-1322) with 7 typed reasons |
| Submission evidence `submit_refusal`,`GT_SS_SUBMIT_RED`,`GT_CERT_DELIVERY` | UNPROVEN | **BROKEN** | **BROKEN** | boundaries `("test_result","submit")`; the submit leg survives but the red-evidence leg cannot fire without a parsed test row. 1+2+1 test hits |
| Catalog selection `select_catalog` | PARTIAL | PARTIAL | PARTIAL | the one ability with real host-route offline coverage: 20 agent hits, 13 test hits, incl. `test_deterministic_bootstrap_mode_uses_no_provider_call`, `test_bootstrap_marker_failure_prevents_provider_transport`, `test_bootstrap_empty_choices_retains_received_response_accounting`. Limitation: no TB2 task fixture, and no §4-L4 mutation check |

**Runtime observation — empirically verified against the pinned wheel:**

| command | `TEST_RUNNER_RE` | `compile_execution_evidence` kind |
|---|---|---|
| `bash tests/test.sh` | no | `None` (no journal row) |
| `uvx pytest …` | **no** — `uvx` absent from `_WRAPPER_ALTERNATIVES` (only `npx`/`bunx`) | `None` |
| `uv run pytest` | yes | `test` |
| `pytest` / `python -m pytest` | yes | `test` |
| `make` | no | `build` (`_BUILD_RE` has `make(?:\s|$)`) |
| `make test` / `make check` / `cargo test` | yes | `test` |
| `gcc -o a a.c` | no | `None`, but `COMPILER_CHECK_RE` matches |
| `python script.py`, `./run.sh`, `[ -f x ] \|\| exit 1` | no | `None` |

`None` means **no `execution_evidence` row at all** — honest at the observation layer
(`ValidationObservation()` is falsy, `outcome=""` documented as "unobserved") but it is what starves the
`test_result` boundary. Two footguns: `_non_test_outcome` reads `returncode == 0` as `"pass"`, and a bare
`make` exiting 0 writes a row with `outcome="pass"` on a task where no test ran — affecting
`build-pmars`, `build-pov-ray`, `compile-compcert`, `make-*`.

### The two OPEN host-side modes, and the minimal offline reproduction each demands

`tests/fixtures/capability_matrix.json:502` — `tb2_host_side`, surface *"eval.gt_central_agent exec
transport + host-only probes"*, both modes `covering_tests: []`, `status: "open - separate repo track"`.

**`exec_transport_failure` — "container exec fails/timeouts host-side".** The transport is
`self._host_executions.exec(...)` → `HostExecutionRecorder.exec` → harbor `DockerEnvironment.exec` →
`asyncio.create_subprocess_exec("docker","compose",…,"exec",…)`. Every command is a **fresh, stateless
`docker compose exec`** — no tmux, no session, no shell continuity; `self.cwd` is re-sent each time.
Every GT probe (`pwd -P`, `uname`, `find -printf` manifests, `sha256sum`, every test command, every
file read) is one exec, so a transport fault costs the agent's action channel *and* GT's entire
observation channel at once. The defect is at `eval/gt_central_agent.py:8452-8457`:

```python
except Exception as exc:
    result = ExecResult(stdout="", stderr=f"{type(exc).__name__}: {exc}", return_code=-1)
```

A container OOM-kill is then **indistinguishable from a failed command** in the model's context. The
distinction survives only in `HostExecReceipt` (`return_code is None` + `exception_type`), which never
reaches the trajectory. Three compounding findings: `except TimeoutError` at `:3285` is **dead code** —
real Docker raises `RuntimeError("Command timed out after N seconds")`, so the red-test probe reports
`probe_error:RuntimeError` instead of `probe_timeout`; a timeout kills the `docker compose` *client*, not
the in-container process, so a timed-out `make` keeps mutating the workspace and is attributed to a later
action's snapshot; and `stderr` is always `None` here (harbor merges it via
`stderr=asyncio.subprocess.STDOUT`), making every `(stdout or "") + (stderr or "")` a no-op and stream
ordering unrecoverable. Affects all 89, worst where the container is stressed: the 4 GT-off
`AgentTimeoutError` tasks (`gpt2-codegolf`, `caffe-cifar-10`, `largest-eigenval`,
`torch-pipeline-parallelism`), the QEMU/VM tasks (the 2026-07-18 run logged an exit-137 OOM SIGKILL on
one), and the 9 tasks that burned >90 % of their agent wall clock GT-off (30 % solve rate).

*Minimal offline reproduction the spec demands (L1, no fix):* one pytest module, fixture = the existing
`_Environment` stub from `tests/test_gt_central_agent.py:880` subclassed so `exec` fails on the Nth
call, parametrised over `{RuntimeError, TimeoutError, RuntimeError("Command timed out after 30 seconds"),
never-returns, ExecResult(return_code=137), invalid-UTF-8 bytes, 2 MB output}` × `{MODEL_ACTION,
WORKSPACE_MANIFEST, RED_TEST_PROBE}`. Assertions: (1) the receipt distinguishes transport failure from
command failure — assert a `HostExecReceipt` with `return_code is None` and non-empty `exception_type`
exists, and that the value is also recoverable from `central_receipt.json`; (2) the model-facing
observation for a transport fault is not byte-identical to that of a genuine `return_code=-1` command;
(3) the docker-shaped timeout string yields `reason_codes == ["probe_timeout"]`, not
`["probe_error:RuntimeError"]` — RED today; (4) no GT feature receipt is marked DELIVERED from an action
whose transport failed; (5) the loop terminates with a diagnosable `terminal` status, never `Submitted`.

**`no_incontainer_journal` — "receipt surface differs - host-side state only".** The in-container arms
write an append-only hash-chained journal — `ExternalStateStore` (`gt_engine/miniswe_integration.py:73`)
creates `<root>/<task_id>/events.jsonl` with `sequence`/`parent_hash`/`event_hash` off `GENESIS_HASH`,
verified by `verify_event_journal`. **`eval/gt_central_agent.py` has zero references to `events.jsonl`,
`event_journal`, `gt-state` or `EventJournal`.** Its surface is flat host files under `logs_dir`:
`trajectory.json` (:3667), `gt-run-receipt.json` (:3684), `miniswe_trajectory.json` (:11559),
`central_receipt.json` (`"schema": "central-runtime-receipt-v3"`, written :11573 and **rewritten**
:12109), `gt_replay/` (:4013, gated on `enable_replay_capture`, default `False`),
`provider_query_started.json` (:2696). Consequences: no tamper-evidence or tail-truncation anchor;
`gt_engine/uptake_audit.py`'s `delivery_missing_from_event_journal` check is unreachable;
`scripts/extract_replay_fixture.py` **hard-requires exactly one `agent/gt-state/*/events.jsonl`**, so no
replay fixture can be cut from a TB2 run at all. One surface is *richer* host-side:
`HostExecutionRecorder.summary()` with per-category p50/p95 and full `receipts[]`. Affects all 89
unconditionally — a surface difference, not a flaky failure.

*Minimal offline reproduction (L1/L2, no fix):* drive a full scripted run to `Submitted` against the
stub env, then assert on `logs_dir` alone — (1) `central_receipt.json` declares its own integrity class
(e.g. a `journal` field), so "host-side only" is a *stated property* rather than an absence; (2) every
`FeatureReceipt` marked DELIVERED is reconstructible from the receipt without an `events.jsonl`;
(3) the two writes at :11573 and :12109 are consistent — capture the first, assert the second is a
superset and never drops a delivered row (a truncation this surface cannot currently detect);
(4) `extract_replay_fixture.py` invoked on the run directory fails with a *typed* "no journal on the
host arm" reason, not a bare exception. These four are the gate; until they pass, the mode stays open.

**Three genuinely silent branches** (everything else here is typed): `_run_lint` :3438
`except Exception: continue` — no receipt row, so a broken linter is invisible; `_system_information`
:2742 fabricates `ExecResult(stdout="Linux\n\n\n\n", return_code=-1)`; and :3047-3061, where
`enable_repository_intelligence=False` writes receipt `status="disabled"` but returns
`RepositoryEvidence(status="environment_transfer_unavailable")`, which
`classify_repository_applicability` maps to `SUBSTRATE_FAILURE` — **a deliberate off-switch reads as
infrastructure failure**. That three-way classifier (`NOT_APPLICABLE_NO_SUPPORTED_SOURCE` /
`SOURCE_BACKED` / `SUBSTRATE_FAILURE`, `repository_intelligence.py:146-166`) is the TB2 correctness
hinge: a D/F task with one `.py` file is not "no supported source", so a failed graph build lands in
`SUBSTRATE_FAILURE` and the run looks broken rather than inapplicable.

## (c) Equivalence classes

| class | n | GT-off | representative | why it is its own class |
|---|---|---|---|---|
| **A** self-verifiable workspace | 5 | 5/5 | `break-filter-js-from-html` | grading tests `COPY`d into `/app`; the only class where a real edit → re-test loop is possible |
| **B** workspace owns a suite | 3 | 3/3 | `fix-code-vulnerability` | `test.sh` runs the repo's bare `pytest` *plus* `/tests/test_outputs.py` — the "original + additional" protocol GT was designed for |
| **C** cloned repo, no visible suite | 6 | 4/6 | `fix-git` | real `.git` and history present, nothing to run |
| **D** few-file artifact task | 46 | 36/46 | `prove-plus-comm` | 1–3 seed files, produce an artifact at a named path; the bulk of the suite |
| **E** service or VM | 13 | 11/13 | `nginx-request-logging` | success is a listening port / daemon / booted VM, not a file |
| **F** image-only sysadmin or synthesis | 16 | 7/16 | `polyglot-c-py` | zero payload files; workspace is whatever `RUN` built. Worst class GT-off (44 %) |

Class A is the sharpest signal: **100 % GT-off**, and **2/5** in the only historical GT-ON run.

## (d) Provider-free test plan, by audit layer

**Driver (proven; no network, provider or Docker — Docker is unavailable on this host and is not
needed).** `tests/test_gt_central_agent.py` (6,114 lines) already drives `MiniSweCentralAgent` in-process
via two seams: a fake Harbor env implementing one async `exec(...) -> ExecResult` (:880, subclassed ~30×)
and model injection through the public `agent._model_factory = lambda: model` against
`eval/gt_central_agent.py:2344`, consumed at `:3733`. Entry is
`await agent.run(instruction, FakeEnvironment(), AgentContext())`; receipts land in `logs_dir`.
Per spec §4 this is the TB2 path's own proof surface — the engine-side `test_miniswe_smoke.py`,
`run_loop_real_repos.py` and `gt_installed_rehearsal.py` mechanisms exercise the *in-container* arms and
are **not** inherited. `gt_installed_rehearsal.py` requires an image and is disqualified outright.

**Fixture workspaces, one per class**, as `tmp_path` trees copied from `tb2repo/` (no image pull):
A = `break-filter-js-from-html`; B = `fix-code-vulnerability`; C = `fix-git` (`git init`, two commits,
detached HEAD); D = `prove-plus-comm`; E = `nginx-request-logging` (`exec` fakes `curl`/`systemctl`);
F = `polyglot-c-py` (empty `/app`). The `_Environment` subclass replays each tree over `find -printf`,
`cat`, `sha256sum` and `pwd -P`.

| # | test | layer |
|---|---|---|
| 1 | `exec_transport_failure` reproduction, 5 assertions above | **L1** |
| 2 | `no_incontainer_journal` reproduction, 4 assertions above | **L1→L2** |
| 3 | `classify_repository_applicability` returns `NOT_APPLICABLE_NO_SUPPORTED_SOURCE` on F and **not** `SUBSTRATE_FAILURE` on D (one `.py`, no graph) | **L1** |
| 4 | `:3047-3061` status/receipt mismatch: with `enable_repository_intelligence=False`, assert receipt status and returned evidence status agree — RED today | **L1** |
| 5 | `uvx` recognised by `TEST_RUNNER_RE`. Today `search("uvx pytest")` is `False` while `"uv run pytest"` is `True` — RED, one-line fix, covers 82 tasks | **L1** |
| 6 | corpus test over all 89: parse every exported `task.toml`/`instruction.md`/`tests/test.sh`; assert the uniform `/tests/test.sh` → `reward.txt` protocol, that GT's predicted capability set matches `tb2_tasks.json`, and that no task acquires a marker GT would misread. Pure filesystem; catches dataset drift | **L1** |
| 7 | per-class run to `Submitted`: assert `CentralFeatureRuntime` emits a DELIVERED receipt only where `feature_payload_valid` **and** `feature_payload_grounded` hold, and that `covering_red`/`recovery`/`submit_refusal` on D/E/F are explicitly untriggered, not silently absent | **L2** |
| 8 | `_run_lint` :3438 emits a receipt row on a failing linter — RED today | **L2** |
| 9 | file-sync bounds: 24 KB base64 manifest chunks at 5 s each, `tar` 20 s, download 20 s, 50 MB per-file cap. Assert a single chunk timeout yields typed `SourceMirrorManifestWriteFailed` and degrades to baseline, and that `finally` cleanup leaves no controller file visible to the task model | **L2** |
| 10 | language contract: `scripts/verify_tb2_language_contract.py` already does this provider-free over 14 witnesses / 22 suffixes. Keep; add drift checking — it pins dataset commit `2fd12b88` while the baseline ran `69671fba` | **L2** |
| 11 | mutation checks: disconnect `register_structural_evidence`; make the producer return `{}` for an eligible input; strip `revision`; strip `fresh`; drop one `_GROUNDING_REQUIREMENTS` key. Each must turn the matching test in #7 RED | **L4** |

**L3 (installed rehearsal) is not reachable for TB2 on this host** — it needs a real image. Per spec §4
that yields **UNPROVEN, not a skipped pass**; the closest free substitute is a CI tier that uses a cached
GHCR image only to run `bash /tests/test.sh` against a *scripted* solution, proving the verifier contract
with zero provider calls.

**CI matrix over `tb2_cache_images.yml`.** Only two steps in `tb2_miniswe_central.yml` cost money:
`scripts.central_bootstrap_canary` (one forced tool call per dispatch) and `harbor run` (≈100 % of
spend). Reusable verbatim: profile enumeration + `task_set_sha256` validation (L271-316); the 8-task
batching helper (`tb2_cache_images.yml` L63-68) for ~12 jobs instead of 89; GHCR mirror pull + retag
(L514-536); `actions/cache` restore/save with the 1.5 GB guard; the from-source Go build of `gt-index`
exporting `GT_INDEX_BINARY` (L159-172), which for free satisfies the
`skipif(os.name != "posix" or not os.environ.get("GT_INDEX_BINARY"))` gate on ~23 real-producer tests;
the Snowflake ONNX dense proof that *asserts* `network_calls`/`provider_calls` are empty; budget
resolution; `gt_task_visibility` aggregation. Replace `harbor run` with the in-process driver.
`active_release.json` currently has `benchmark_authorized: false` with two blockers, so
`release_workflow_guard` should refuse a paid dispatch today.

## (e) Ranked failure classes that nothing currently tests

Ordered by the spec's §5B priority — run termination, evidence corruption, lost ability, misleading
steering, avoidable work.

1. **Exec-transport failure collapsed into command failure** (`:8452-8457`). Uncovered by construction:
   of 32 fake `exec` methods in the host test file, exactly one raises a transport-shaped error
   (`RuntimeError("configured cwd does not exist")`, :1211, `_resolve_cwd` only); every other `raise` is
   an `AssertionError(command)` scaffolding guard. The provider side is covered densely by contrast.
   A container death can degrade GT to zero evidence while the loop continues and the receipt still
   reports capability rows.
2. **Timeout orphans the in-container process.** `_terminate_process` kills the `docker compose` client;
   the command keeps running and mutating the workspace, and its effects are attributed to a later
   action's snapshot. Evidence corruption, not just a lost command.
3. **`SUBSTRATE_FAILURE` misclassification.** One source file makes a task "not source-less", so a
   failed graph build reads as broken infrastructure rather than an inapplicable task — and
   `enable_repository_intelligence=False` produces the same verdict via a different bug (#4 above).
4. **`test_result` boundary starvation** on 81 tasks — `covering_red`, `recovery` and the red leg of
   `submit_refusal` are enabled, never trigger, and nothing asserts the difference between "never
   triggered" and "triggered and produced nothing".
5. **Bare `make` exiting 0 booked as `outcome="pass"`** — a positive evidence row on a task where no
   test ran. Misleading steering on the build-heavy tasks.
6. **File-sync round-trip cost.** The path manifest streams in 24 KB base64 chunks, one `docker compose
   exec` each at a 5 s timeout, and a single chunk failure collapses the whole GT treatment to baseline
   through the `:3145` catch-all. Nothing measures GT's exec overhead against a per-task budget — and
   9 tasks already used >90 % of their agent wall clock GT-off, solving at only 30 %.
7. **Regression risk is documented, not hypothetical.** The only historical GT-ON TB2 run
   (`docs/benchmarks/2026-07-18-tb2-89.md`, mini-gt-swe @`0903552`, **53/89**) sits **13 below** the
   GT-off baseline: 20 tasks GT-off solved that GT-ON lost, against 7 gained; class A fell 5/5 → 2/5.
   This is **not a controlled A/B** — different dates, harness commits and agent entry points — but it
   is the best evidence available, it points the wrong way, and nothing in CI reproduces it.

**Can GT claim "full capability" on TB2? No — and the spec's vocabulary says why.** Under §5A the honest
per-workflow verdict for TB2 today is **UNPROVEN across the board**, with `cochange_prior`,
`persistent_plan` and `plan_gate` **NOT APPLICABLE** (the host route genuinely does not implement them),
`covering_red`, `recovery` and the red leg of `submit_refusal` **BROKEN** on the 81 tasks whose boundary
cannot occur, and `select_catalog` **PARTIAL** — the single ability with real host-route offline
coverage and still no TB2 fixture and no mutation check. Not one capability meets the WORKING OFFLINE
bar, which requires *"correct production-path behavior demonstrated under the declared conditions, with
effective negative and disconnection tests"* — the matrix already records this as
`covering_tests: []`, and per §4 no in-container proof may be borrowed to close it.

"Working correctly" on a non-repo TB2 task must mean **explicit, not silent** — and the host route is
already mostly built that way: `not_applicable_no_supported_source` is threaded through 12 sites,
`BootstrapStatus.NOT_APPLICABLE` carries reason codes, `_preemptive_retrieval_gate_reason` returns 7
ordered typed reasons, and `_SAFE_PREEMPTIVE_ABSTENTION_REASONS` is commented *"These are deliberate,
deterministic abstentions … they do not mean that repository retrieval failed."* The gap is not
vocabulary; it is that **none of it is exercised on a TB2-shaped fixture**, plus the three silent
branches and the one classifier that turns "inapplicable" into "failed". Until the two reproductions in
(b) pass, a TB2 receipt cannot distinguish "GT had nothing to work with" from "GT was broken".
