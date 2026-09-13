# swelive-bench — SWE-bench-Live Lite adapter for the GT harness

Task adapter that binds two small wall-clock SWE-bench-Live Lite instances to
the product path:

```
GitHub Actions -> task.toml contract -> datacurve-pier (agent inside task docker
image, then [verifier] section inside the image -> reward) -> gt-harness -> receipts
```

Layout mirrors the DeepSWE task package contract (`pier run -p swelive-bench/tasks`
discovers each subdirectory via `task.toml` + `environment/` + `instruction.md` +
`tests/test.sh`).

## Tasks

| ordinal | instance_id | repo | base_commit | F2P / P2P | test_cmds | image |
|---|---|---|---|---|---|---|
| 1 | `cyclotruc__gitingest-94` | cyclotruc/gitingest PR 94 | `2125765025c65fdd2aec89856bdc095dfb0fc826` | 1 / 30 | `pytest -rA` | `starryzhang/sweb.eval.x86_64.cyclotruc_1776_gitingest-94` |
| 2 | `dynaconf__dynaconf-1241` | dynaconf/dynaconf PR 1241 | `105e6312f8ce3414ba0bebf88ad6e35b3953df38` | 1 / 27 | `pytest -m "not integration" -v -l --tb=short --maxfail=1 -rA tests/` | `starryzhang/sweb.eval.x86_64.dynaconf_1776_dynaconf-1241` |

### Image verification (DockerHub registry API, no pull)

All four candidates have pullable images (tags `latest` and `0430`, identical
digests; `GET https://hub.docker.com/v2/repositories/starryzhang/sweb.eval.x86_64.<name>/tags` → 200):

| instance | digest | compressed size |
|---|---|---|
| cyclotruc_1776_gitingest-94 | `sha256:0698979e10aee6ee1b46607d7c86e857218d03df3e62524abb00ea8bffeba394` | 454,302,608 B (~433 MiB) |
| cyclotruc_1776_gitingest-115 | `sha256:f2c460c296a20a51aa8685eb1277aace8280f5400984fecc82897ca0bf94b794` | 454,472,912 B |
| dynaconf_1776_dynaconf-1241 | `sha256:c5930010f69611e20ec838903de6d76a8bd61284ffdfb6237fbbc36d881bfed8` | 623,555,318 B (~595 MiB) |
| amoffat_1776_sh-744 | `sha256:0ab5f01f2c45fe84813deec1dd1478917ba63b9a2a7443dc43a065bd186e14c3` | 591,256,222 B |

`task.toml [environment].docker_image` uses the digest-pinned pullable ref
`name@sha256:<digest>` (reproducible; still a normal pullable ref). The tag refs
+ digests are also in `manifest.json` (`container_image`/`container_digest`,
composed downstream as `image@digest` — see `gt_harness/product.py:875`) and in
per-task `tasks/<id>/image.lock.json`.

## task.toml contract (pier 0.3.1, schema_version "1.3")

Faithful to the DeepSWE task.toml at
`datacurve-ai/deep-swe@435ee89e/tasks/aiomonitor-task-snapshots-diff/task.toml`:

- `schema_version = "1.3"`, `artifacts = ["/logs/artifacts/model.patch"]`,
  `[task]` package block, `[metadata]` (task_id/language/repository_url/
  base_commit_hash + SWE-bench-Live provenance fields).
- `[environment]`: `docker_image` = digest-pinned sweb.eval ref, `os="linux"`,
  `cpus=2`, `memory_mb=8192`, `storage_mb=20480`, `workdir="/testbed"`.
- `[agent]`: `network_mode="no-network"` (LLM egress rides pier's authenticated
  squid egress-proxy sidecar — `docker.py:_prepare_egress_proxy_compose`; the
  allowlist comes from the agent's `network_allowlist()`, not from task.toml),
  `timeout_sec=1800` (small-clock per requirement; DeepSWE uses 5400).
- `[verifier]`: `environment_mode="separate"`, `timeout_sec=900`,
  `[[verifier.collect]]` hook that diffs the agent worktree against the base
  commit into `/logs/artifacts/model.patch`.
- `[verifier.environment]`: **no `docker_image`** — so pier builds
  `tests/Dockerfile` (which is itself `FROM` the pinned sweb.eval image and
  COPYs the hidden grading assets into `/tests`). `workdir="/testbed"`.
- `network_mode` per task:
  - `dynaconf__dynaconf-1241`: `"no-network"` (all tests local).
  - `cyclotruc__gitingest-94`: `"public"` — **required**: 11 of 30
    PASS_TO_PASS tests live in `tests/test_clone.py` and clone real GitHub
    repos / probe repo existence over HTTPS; with no network the verifier
    would make even the gold patch unresolvable. The agent phase stays
    `no-network` either way.

## How pier 0.3.1 executes this (verified against the wheel)

Key drift vs local pip `datacurve-pier==0.2.0`: **the `[verifier]` contract
only exists in >=0.3.x**. In 0.2.0 `VerifierConfig` is just
`timeout_sec/env/user`; `[[verifier.collect]]`, `environment_mode`,
`network_mode` are unknown fields (pydantic ignores extras, so the task would
silently degrade: no collect, shared-mode verifier, no tests in image →
`RewardFileNotFoundError`). The workflow pin `0.3.1` is required.

Single-step trial flow (`pier/trial/trial.py`):

1. Agent env starts: `[environment].docker_image` is used as the FROM of a
   generated agent Dockerfile that layers the agent install (`mini-swe-agent`
   via `uv tool install` — needs network **at docker-build time**, not runtime;
   `agents/installed/mini_swe_agent.py:623`). Container = compose `main`
   service; `/logs/{agent,verifier,artifacts}` are host bind mounts.
2. Agent runs inside the container (`docker compose exec main bash -c
   "mini-swe-agent --yolo ..."`, workdir `/testbed`) with `instruction.md`
   text as the task. **task.toml and tests/ are never shown to the agent**:
   the agent sees only the instruction text (host-side read,
   `models/task/task.py:67`) plus the container filesystem; `tests/` is only
   the build context of the separate verifier image and is never uploaded to
   the agent env in separate mode. test_patch/FAIL_TO_PASS/PASS_TO_PASS are
   therefore verifier-only, matching the SWE-bench-Live protocol.
3. After the agent: `pre_artifacts.sh` (absent) → `[[verifier.collect]]` hooks
   run `docker compose exec` in the **agent** container
   (`trial.py:_run_collect_hooks`, best-effort, non-fatal) → produce
   `/logs/artifacts/model.patch` via `git add -N -A && git diff --binary
   <base_commit>` (superset of the official `git diff HEAD`: also captures
   committed work and untracked new files; `add -N` records intent-to-add).
4. `artifacts = ["/logs/artifacts/model.patch"]` (+ the `/logs/artifacts`
   convention dir, auto-inserted by `trial/artifact_handler.py`) are
   downloaded to the host trial dir.
5. Separate verifier env: fresh container built from `tests/Dockerfile`
   (`FROM` the same pinned image → `/testbed` pristine at base commit);
   `/logs/verifier` bind-mounted to the host trial verifier dir; collected
   artifacts are uploaded back to their source paths
   (`/logs/artifacts/model.patch`) via `upload_artifacts`
   (`trial.py:420-426`, `artifact_handler.py:72-110`).
6. pier executes `/tests/test.sh` in the verifier env (`verifier.py:verify`,
   `skip_tests_upload=True` → the script must exist **inside** the image —
   hence `tests/Dockerfile COPY test.sh /tests/test.sh`). Then pier reads
   `/logs/verifier/reward.txt` (float) or `/logs/verifier/reward.json` (flat
   dict). Reward **never** comes from the test script's exit code; the script
   must always write a reward file — the `trap ... EXIT` writes `-1` to
   `reward.txt` if grading never completed (DeepSWE convention).

`test.sh` mirrors the official eval (`microsoft/SWE-bench-Live
evaluation/evaluation.py` + `RepoLaunch launch/core/platforms/linux.py`):

1. `cd /testbed` (+ official nested-`.git` fallback), `git apply --reject
   --whitespace=nowarn /tests/test_patch.diff` (test_patch first, as official).
2. `git apply --reject --whitespace=nowarn /logs/artifacts/model.patch`
   (missing/empty → graded 0, still produces reward).
3. `bash /tests/run_tests.sh > /logs/verifier/testlog.out 2>&1` — verbatim
   `test_cmds` from the dataset row.
4. `python3 /tests/grade.py` — `parse_log_pytest` + `default_pytest_parser`
   (status lines `PASSED|FAILED|SKIPPED|ERROR|XFAIL` from the `-rA` summary;
   XFAIL→pass) then the official `resolved` rule: every FAIL_TO_PASS observed
   passing **and** no FAIL_TO_PASS/PASS_TO_PASS observed failing. Writes
   `reward.json` `{"reward": 1.0|0.0}` + `eval_report.json` diagnostics.

## Files per task

```
tasks/<instance_id>/
  task.toml              schema 1.3 contract (above)
  instruction.md         problem_statement verbatim + "work in /testbed" line
                         (per the official protocol: agent may see ONLY
                         problem_statement + the image; hints_text excluded)
  image.lock.json        sidecar: image ref, tags, digest, size, verification
  environment/Dockerfile validation-only (pier requires a Dockerfile or
                         docker-compose.yaml here; real env uses docker_image)
  tests/
    Dockerfile           FROM pinned image; COPY grading assets -> /tests
    test.sh              verifier entrypoint (applies patches, runs tests, grades)
    run_tests.sh         verbatim test_cmds
    grade.py             pytest log parser + resolved rule -> reward.json
    spec.json            fail_to_pass / pass_to_pass / test_cmds / provenance
    test_patch.diff      the dataset test_patch (verifier-only)
```

## Open risks / assumptions needing a real run (ranked)

1. **In-image layout unverified** (no docker on this host): `/testbed` with a
   `.git` at `base_commit` is assumed from RepoLaunch's container conventions
   (`working_dir=/testbed`, `git init /testbed && fetch --depth 1 <base> &&
   reset --hard`). If the image's repo lives elsewhere or lacks `.git`, the
   collect hook produces no artifact and every trial grades 0. Mitigations in
   place: nested-`.git` fallback in both hook and test.sh; reward trap → `-1`.
2. **Python/pytest PATH**: if the image activates its env via `.bashrc` rather
   than `ENV PATH`, `docker compose exec bash -c` won't see `pytest`. test.sh
   defensively prepends common conda/venv paths; if the env lives somewhere
   exotic the verifier fails with `-1`, not a false 0.
3. **gitingest verifier `public` network**: even with net enabled, the clone
   tests depend on github.com reachability + rate limits from the runner; a
   network flake turns a true solution into a false 0. If flakiness appears,
   consider re-scoping to `dynaconf__dynaconf-1241` + `amoffat__sh-744`
   (178 P2P — heavier but fully local) or `cyclotruc__gitingest-115` (same
   network caveat as -94).
4. **`git diff <base_commit>` in shallow clones**: works iff `base_commit` is
   the checked-out HEAD object (true for `fetch --depth 1 <base>`). If the
   image carries fuller history, still fine; if `base_commit` were somehow not
   present, the hook yields no patch → 0. `git diff HEAD` fallback possible.
5. **`git apply` context drift**: model.patch is `git apply`'d onto a tree
   that already has test_patch applied (official order). Non-overlapping for
   these instances; `--reject` degrades gracefully anyway.
6. **Compose `image:` with `@sha256:`**: `docker_image` is digest-pinned; the
   prebuilt compose path uses `image: ${PREBUILT_IMAGE_NAME}` and the agent
   Dockerfile `FROM ${PREBUILT_IMAGE_NAME}` — both valid, but untested against
   pier's compose generation on a real daemon.
7. **`--maxfail=1` in dynaconf's test_cmds**: pytest stops at the first
   failure; any earlier P2P failure hides the F2P result → unresolved. This is
   the dataset's verbatim command — kept for fidelity; do not "fix".
8. **P2P-missing asymmetry**: the official rule tolerates PASS_TO_PASS tests
   absent from the log (only *observed failures* count). Replicated verbatim;
   stricter graders (e.g. requiring P2P ⊆ passed) would change outcomes.
9. **Windows-host docker-compose `cp`**: artifact upload/download uses
   `docker compose cp`; behavior on Windows hosts + GitHub runners is assumed
   fine (DeepSWE runs there), untested for these images.
10. **`[task].name = "swelive/<instance_id>"`** becomes part of compose
    project/image names (sanitized); collisions with a concurrent DeepSWE
    dataset of the same dir name are not expected but unverified.

## Not in scope (owned by other steps)

- Wiring `swelive-bench/tasks` into a workflow / bundle (analogous to
  `config/deepswe_product_bundle_v1.json` `tasks[]`; `manifest.json` here
  mirrors that entry shape: `ordinal, task_id, language, base_commit,
  task_config_sha256, container_image, container_digest`).
- `task_config_sha256` = sha256 of the exact `task.toml` bytes (LF endings —
  `.gitattributes` pins `eol=lf`; recompute if the file is ever rewritten with
  CRLF).
- Agent/model/pier CLI flags, job config, receipts.
