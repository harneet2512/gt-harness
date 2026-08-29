# Baseline and safety receipt

This receipt records the preimplementation state for HAR-5. It does not claim that the baseline is passing. Failures and specification conflicts are retained as observed.

## Verdict

- Receipt status: `COMPLETE_WITH_NAMED_SAFE_STOPS`
- Source HEAD: `7c8e77af02d1cc752ed9c0e389422b617aa98013`
- Branch: `codex/gt-harness-overhaul`
- Worktree: `D:\gt-harness-overhaul`
- Initial tracked and untracked status: clean
- Product or vendor code changed by HAR-5: no
- Python suite: failed during collection with two release-manifest errors
- Vendored Go suite: passed
- Named safe stop `EXPECTED_ANCESTRY_MISMATCH`: the issue's historical ancestry premise is inverted in this checkout
- Named safe stop `RELEASE_MANIFEST_WORKTREE_HASH_MISMATCH`: protected LF Git blobs are checked out as CRLF while `core.autocrlf=true`

## Current-checkout facts

The checked-out source, not the historical issue text, is authoritative:

- `eb8714e8b739e37f39e2a6a3e95fe41c7a1db739` is **not** an ancestor of HEAD (`git merge-base --is-ancestor` exit 1).
- `2bf3f4954b123c222b7f6c2b98761654ef2ef007` **is** an ancestor of HEAD (exit 0).
- All five central files exist in the working tree. No commit is reapplied.
- The original `D:\gt-harness` worktree had 139 untracked entries and no tracked changes when counted. It was not cleaned or modified.

## Toolchain

| Component | Executable or package | Version/state |
| --- | --- | --- |
| Python | `C:\Users\Lenovo\AppData\Local\Programs\Python\Python312\python.exe` | 3.12.0 |
| SQLite | Python `sqlite3` module | 3.42.0 |
| Go | `C:\Program Files\Go\bin\go.exe` | go1.26.7 windows/amd64 |
| sqlite-vec | Python distribution | 0.1.9 |
| igraph | Python distribution | absent |

## Central-file inventory

These are working-tree SHA-256 values at the recorded HEAD:

| Path | SHA-256 |
| --- | --- |
| `gt_engine/persistent_execution_state.py` | `aab38acef15672496c791e1b6b72dbe52c43d171d1460116609e1ccc1b35500a` |
| `gt_engine/repository_intelligence.py` | `4a5726759a3d8dc0fdc111187d2906d275ad4c4ad3eff0df287bd9065e8db231` |
| `gt_engine/hybrid_retrieval.py` | `6f82e8a60fe55bb361351cbeaf6a5b89eea6fb9e550bef2c0f4f581ac7e7fb96` |
| `gt_engine/hybrid_repository.py` | `666c364a10e3f684b1b2182a5f9ebf3b5a450b6488dcf2765a4ba997d8caee5e` |
| `eval/gt_central_agent.py` | `740ecd3c3c7bea2312f0c6a2484735d749e1d4ac59257d71bf5ec21e018a4e8c` |

## Command receipts

The empty-stream SHA-256 used below is `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

```json
{
  "schema": "gt.har5.baseline_receipt.v1",
  "source_head": "7c8e77af02d1cc752ed9c0e389422b617aa98013",
  "cwd": "D:\\gt-harness-overhaul",
  "commands": [
    {
      "command": "git rev-parse HEAD",
      "started_at": "2026-08-29T09:22:04.6726998Z",
      "ended_at": "2026-08-29T09:22:04.7509640Z",
      "duration_ms": 74.965,
      "exit_status": 0,
      "stdout_sha256": "52d0dc5cdbf9fe078bcc538669b683dd2f5f55e064b60124fb33596006269242",
      "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    },
    {
      "command": "git status --short",
      "started_at": "2026-08-29T09:22:04.7868532Z",
      "ended_at": "2026-08-29T09:22:04.8809743Z",
      "duration_ms": 94.129,
      "exit_status": 0,
      "stdout_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    },
    {
      "command": "git merge-base --is-ancestor eb8714e8b739e37f39e2a6a3e95fe41c7a1db739 HEAD",
      "started_at": "2026-08-29T09:22:04.8809743Z",
      "ended_at": "2026-08-29T09:22:04.9790462Z",
      "duration_ms": 97.379,
      "exit_status": 1,
      "stdout_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    },
    {
      "command": "git merge-base --is-ancestor 2bf3f4954b123c222b7f6c2b98761654ef2ef007 HEAD",
      "started_at": "2026-08-29T09:22:04.9790462Z",
      "ended_at": "2026-08-29T09:22:05.0555484Z",
      "duration_ms": 77.238,
      "exit_status": 0,
      "stdout_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    },
    {
      "command": "python --version",
      "started_at": "2026-08-29T09:22:05.0555484Z",
      "ended_at": "2026-08-29T09:22:05.1006337Z",
      "duration_ms": 43.765,
      "exit_status": 0,
      "stdout_sha256": "a172e122d1bb1308e3143a83779db6e4ef9f56f39986ae71873d002a543c1b46",
      "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    },
    {
      "command": "go version",
      "started_at": "2026-08-29T09:22:05.1006337Z",
      "ended_at": "2026-08-29T09:22:05.1967053Z",
      "duration_ms": 100.152,
      "exit_status": 0,
      "stdout_sha256": "65f9124cab710b3b74a4c40f6d75ddd9bf1429619ac30d37a48ee990d6c1ca56",
      "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    },
    {
      "command": "git diff --check",
      "phase": "pre-receipt",
      "started_at": "2026-08-29T09:22:05.1967053Z",
      "ended_at": "2026-08-29T09:22:05.2746296Z",
      "duration_ms": 74.539,
      "exit_status": 0,
      "stdout_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    },
    {
      "command": "git diff --name-only -- vendor/",
      "phase": "pre-receipt",
      "started_at": "2026-08-29T09:22:05.2746296Z",
      "ended_at": "2026-08-29T09:22:05.3437409Z",
      "duration_ms": 68.483,
      "exit_status": 0,
      "stdout_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    },
    {
      "command": "python -m pytest -q",
      "started_at": "2026-08-29T09:22:18.5922927Z",
      "ended_at": "2026-08-29T09:22:44.4825299Z",
      "duration_ms": 25885.257,
      "exit_status": 2,
      "stdout_sha256": "f8d259ae81a18df5e605acdd993f2f74d4f5d378e13af3f519e40b1688ac0edd",
      "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "result": "collection interrupted by 2 errors"
    },
    {
      "command": "go test ./...",
      "cwd": "D:\\gt-harness-overhaul\\vendor\\gt-index-src",
      "started_at": "2026-08-29T09:22:59.1459858Z",
      "ended_at": "2026-08-29T09:23:22.2806459Z",
      "duration_ms": 23133.141,
      "exit_status": 0,
      "stdout_sha256": "03bcce6b988222283b0bd0f06283a7daf867f78a6465e0ac01ef98e6a5bad568",
      "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "result": "all packages passed or reported no test files"
    },
    {
      "command": "git diff --check",
      "phase": "post-receipt",
      "started_at": "2026-08-29T09:27:02.4529352Z",
      "ended_at": "2026-08-29T09:27:02.5193740Z",
      "exit_status": 0,
      "stdout_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    },
    {
      "command": "git diff --name-only -- vendor/",
      "phase": "post-receipt",
      "started_at": "2026-08-29T09:27:02.5193740Z",
      "ended_at": "2026-08-29T09:27:02.5695690Z",
      "exit_status": 0,
      "stdout_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    }
  ]
}
```

## Preserved Python failure

`python -m pytest -q` stopped while collecting:

- `tests/test_tb2_promotion_gate.py`
- `tests/test_tb2_regression_forensics.py`

Both import `scripts.tb2_promotion_gate`, which invokes `scripts.release_manifest.load_release_manifest()` and raises `ValueError: prediction sha256 mismatch`.

The manifest stores SHA-256 values for LF-normalized Git blobs, while this Windows checkout uses `core.autocrlf=true`. The three referenced JSON working-tree files contain CRLF bytes:

| Protected object | Manifest/Git-blob SHA-256 | Working-tree SHA-256 | Git bytes | Worktree bytes | CRLF count |
| --- | --- | --- | ---: | ---: | ---: |
| prediction | `19fcdcbbaa11475c81f2e948be39d2c1e7d2a1d78d7c5de5c819904fb2a919a7` | `2d0b20a2624e15da920a6f58d2ce68bd1314da287bf519b264355890cc177ef1` | 3053 | 3072 | 19 |
| baseline | `f75ebc8dd1eb25cb31cfa099b196d54346016b9f2de8e6f026e420cc213dd0bf` | `ed79d222ecde36e2d90bf5bf76f96b7458a9a45aa95e4e32969877d882953d4f` | 31524 | 32674 | 1150 |
| treatment | `75513683e02edb0fc676fe388458a754011470f27259c211f97a48db885ab351` | `50cdbe1a94d4b7576a5c8863e82bc415c2528ee2c6ab50578946b15450750924` | 1329 | 1365 | 36 |

For every object, `worktree_bytes - git_bytes == CRLF_count`. HAR-5 records but does not repair this cross-platform validation defect because this issue is receipt-only.

## Diagnostic-command disclosure

Two attempts to build a generic PowerShell command-receipt wrapper failed before the required commands were rerun: the installed Windows PowerShell/.NET surface lacks `Convert.ToHexString`, and an argument-binding attempt passed empty native arguments. A later combined environment probe also contained two malformed `python -c` quoting invocations; direct probes replaced them. None of these failed diagnostics is represented as baseline evidence, and none modified repository files.

## Safety conclusion

The repository baseline is not fully green. The Go source suite is green; the Python suite is blocked during collection by checkout-byte-sensitive release-manifest validation. The ancestry claims in the historical issue are stale. These failures are preserved in the receipt and in Linear. No benchmark, provider call, central-commit reapply, vendor edit, release, or destructive operation occurred.
