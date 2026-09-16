# Benchmark corpus fixtures

Recorded facts about the two Python-reachable benchmark cohorts, compact
enough to replay offline in `tests/test_deepswe_corpus.py` and
`tests/test_swelive_corpus.py`. Nothing here needs a container, a network
call, a provider or a benchmark dispatch.

Generated **2026-09-15** from the per-benchmark analyses under the audit
scratchpad; the full inventories and the complete task tables are copied to
`artifacts/audit/benchmarks/<bench>/`.

## Sources

| File | Source of truth |
|---|---|
| `deepswe_tasks.json`, `deepswe_test_configs.json` | `datacurve-ai/deep-swe` @ `435ee89ec2f2e2289f33b0da4f992f0b7b7266b9` (DeepSWE v1.1, 113 tasks); repository config bytes and root trees fetched from each task's `repo` at its `base_commit` |
| `swelive_tasks.json` | SWE-bench-Live **Lite** (300 instances, `log_parser=pytest`); repository config facts read from each `(repo, base_commit)` on GitHub; verifier commands from the dataset's own `test_cmds`; the ten agent commands under `origin: run_34996816912` are recovered from that recorded GT run's journal |
| `model_command_labels.json` | labels over the already-committed `tests/fixtures/command_corpus/model_test_commands.json` (1,282 commands recorded from GT-on DeepSWE trajectories) |

## How the expectations were derived

Expectations are **not** produced by calling the code under test.

* **Discovery triple** - re-derived from the CONFIDENCE LAW written in
  `groundtruth.runtime.verification_plan.discover_test_command`'s own
  docstring (config-probe precedence, then the language-profile extension
  fallback), applied to the materialisation spec each fixture carries. Each
  row keeps the `rule` string naming the clause that decided it.
* **Command extent** (`suite` / `scoped` / `unknown`) - read off the command
  text with a pytest option table taken from `pytest --help`, and with the
  documented `test_command_shape` contract ("at most one test-runner
  invocation surrounded by benign segments") for the plain/compound split.
* **Recorded cross-checks** - `swelive_tasks.json` also carries
  `recorded_discovery`, the result of calling the real
  `discover_test_command` against roots rebuilt from the repositories' real
  config blobs, and `deepswe_tasks.json` carries `analysis_discovery` from
  the same pass. The tests assert the independent expectation; the recording
  is there so a compaction that lost a decisive fact shows up.

## Materialisation

The discovery tests rebuild a repository root in `tmp_path`:

* `deepswe_test_configs.json` - `files` are the **real bytes** of every root
  config file whose CONTENT decides a probe (`pyproject.toml`, `setup.cfg`,
  `Makefile`, ...); `present` are the root manifests whose PRESENCE decides
  one; `package_json` is the real `scripts.test` entry; `dirs` are the root
  directory names; `has_python_sources` comes from GitHub's language census.
* `swelive_tasks.json` - `materialisation` records which root manifests
  exist, which pytest config section the repository carries, whether the
  Makefile has a column-0 `test:` target, and the test-root directory names.
  Bodies are the minimum that carries the recorded fact.

## Known UNPROVEN rows

Four DeepSWE rows (`arcane-drift-detection-baselines`,
`claude-code-by-agents-recursive-delegation`,
`optique-conditional-option-dependencies`, `quill-shared-toolbar-focus`)
declare a JS workspace with no usable root test script, so their real
discovery answer is decided by a **sub-package** manifest below the recorded
root tree. They carry `expected_discovery.basis == "UNPROVEN"`, are excluded
from the discovery corpus, and `test_t01_unproven_rows_are_named_and_bounded`
pins the set so it cannot grow silently.

## Sizes

All four files are well under the 1.5 MB per-fixture budget
(`deepswe_test_configs.json` is the largest at ~430 KB).
