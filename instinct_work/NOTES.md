# HAR-5 clean pinned-baseline receipt

This receipt replaces the earlier HAR-5 record for the overhaul replacement
lineage. It describes the source and results below, including the failing Python
baseline. It does not claim that the central runtime exists or that the suite is
green.

## Source identity and safety boundary

- Captured on 2026-08-29 in `D:\gt-harness-clean-lineage`.
- Remote: `https://github.com/harneet2512/gt-harness.git`.
- Branch: `codex/gt-harness-overhaul-clean`.
- Remote `main`, local `origin/main`, starting `HEAD`, and pinned handoff:
  `81d9a00613e65f761de6c0efa142503a371d42d3`.
- `git merge-base --is-ancestor 81d9a00613e65f761de6c0efa142503a371d42d3 HEAD`:
  exit 0.
- Initial `git status --short`: exit 0, zero lines.
- `core.autocrlf=true` in this checkout.
- Python 3.12.0; Go 1.26.7 windows/amd64.
- No provider, benchmark, GCP, schema migration, release, or vendor mutation was
  performed.

The invalid-lineage PR #19 worktree was not modified. Its uncommitted HAR-31
delta was exported outside this repository as a 24,631-byte binary patch with
SHA-256
`c2f6b41160de7d245e9f13b8fc350888316833ee91b7e524bc2e3ee7d89a01a4`.
`git apply --check --whitespace=nowarn` succeeded at exact source commit
`b0c3b56b5832eb57db6ca4bf3da2fe0ec6b64458` in a separate clean probe clone.

## Central-source inventory at the pinned handoff

| Path | Present | Bytes | Working-tree SHA-256 |
| --- | ---: | ---: | --- |
| `gt_engine/indexer.py` | yes | 8,013 | `70dd25d0daea8e9c1d273adb58cadfe4b1ff07252e5c24d24e253b5744ac474d` |
| `gt_engine/graph_lease.py` | no | not applicable | not applicable |
| `gt_engine/persistent_execution_state.py` | no | not applicable | not applicable |
| `gt_engine/repository_intelligence.py` | no | not applicable | not applicable |
| `gt_engine/hybrid_retrieval.py` | no | not applicable | not applicable |
| `gt_engine/hybrid_repository.py` | no | not applicable | not applicable |

This is a named safe stop for direct replay of the old HAR-11 commit. The old
receipt's central-runtime claims are not valid for this source.

## Python baseline

Command from repository root:

```text
python -m pytest -q
```

Result: pytest test-failure exit status 1. The run started at approximately
2026-08-29T16:44:01Z and its stdout was last written at
2026-08-29T16:49:04.9083860Z (approximately 304 seconds). The terminal
transport did not return the wrapper's final millisecond metadata after its
initial yield, so this receipt intentionally does not claim a more precise
duration.

- 606 collected, established separately by
  `python -m pytest --collect-only -q` (exit 0).
- 602 passed, 3 skipped, 1 failed.
- Failure:
  `tests/test_gt_finalstand.py::test_finalstand_is_machine_valid`.
- Reported cause: generated finalstand inventory drift involving
  `gt_finalstand/role_inventory_source.json`,
  `gt_finalstand/role_audit.csv`,
  `gt_finalstand/language_operation_certification.csv`, and
  `gt_engine/generated_typed_capabilities.py`.
- stdout: 1,878 bytes; SHA-256
  `d1dcb21c223e64bd2289ba61a2f943824925127df6ae594f6311ef07563274f8`.
- stderr: 0 bytes; SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- Collection stdout SHA-256:
  `2540c2f1bb2d353d0445cbbb5394e12b2a3681125bfadc843fa82a24d2c3adba`.

The failure is evidence, not a waived gate. HAR-5 does not repair generated
inventory drift.

## Vendored Go baseline

Command from `vendor/gt-index-src`:

```text
go test ./...
```

Result: exit 0. Eight packages reported `[no test files]`. The run lasted
23,671.193 ms, from 2026-08-29T16:49:27.3252552Z through
2026-08-29T16:49:50.9964483Z.

- stdout: 645 bytes; SHA-256
  `7e1f6e0980912e7170426746a2eff370254047faae6ebb4f1659d7a32851f1bf`.
- stderr: 0 bytes; SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

## Integrity checks before writing this receipt

- `git diff --check`: exit 0.
- `git diff --name-only -- vendor/`: exit 0, zero lines.
- `git status --short`: exit 0, zero lines.

After this receipt is written, the intended tracked delta is exactly this file.
The pre-commit verification must repeat the whitespace, vendor-diff, source
identity, and status checks and record their actual results in Linear.
