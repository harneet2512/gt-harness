# GroundTruth Clean-Machine Validation Runbook

## Preconditions

- Use a fresh GitHub Codespace or GitHub-hosted Actions runner.
- Check out the exact harness and GroundTruth commit identities recorded in the execution ledger.
- Place the GroundTruth checkout beside the harness checkout and set `GROUNDTRUTH_ROOT` to that path.
- Do not configure provider credentials; this runbook is provider-free.
- Install Python 3.12 and the Go version selected by the GroundTruth `go.mod`.

## External workflow

The canonical execution is the manually dispatched
`.github/workflows/gt_finalstand_provider_free.yml` workflow. Supply an immutable GroundTruth ref.
The workflow rejects anything except a full lowercase 40-hex commit identity.
The workflow performs every command below and uploads its receipts even on failure; its final
receipt records the actual job status and cannot represent a failed job as successful.

Run the following from the harness checkout:

```bash
python scripts/generate_gt_finalstand.py --check
python scripts/validate_gt_finalstand.py
python -m pytest tests/test_gt_finalstand.py tests/test_phase2_closeout.py -q
python scripts/finalstand_offline.py offline \
  --cases gt_finalstand/offline_cases.json \
  --gt-index-bin "$RUNNER_TEMP/gt-index" \
  --require-terminal \
  --out gt_finalstand/receipts/offline_suite.json
python scripts/finalstand_offline.py runbooks \
  --out gt_finalstand/receipts/runbook_validation.json
python scripts/phase2_experiment.py dry-run \
  --manifest gt_finalstand/phase2_experiment_manifest.json \
  --out gt_finalstand/receipts/experiment_dry_run.json
```

In the GroundTruth checkout, execute the native tests and emit the live registry manifest:

```bash
CGO_ENABLED=1 go test -tags sqlite_fts5 ./gt-index/...
CGO_ENABLED=1 go build -tags sqlite_fts5 -o "$RUNNER_TEMP/gt-index" \
  ./gt-index/cmd/gt-index
CGO_ENABLED=1 go run -tags sqlite_fts5 ./gt-index/cmd/gt-index \
  -language-manifest > language-manifest.json
```

Then bind the live Go output to the 210-row certification matrix:

```bash
python scripts/finalstand_offline.py language \
  --manifest "$GROUNDTRUTH_ROOT/language-manifest.json" \
  --out gt_finalstand/receipts/language_manifest.json
```

Run the forbidden-capability scan. It must exit zero and report no reachable forbidden default or
advertised capability; preserve the JSON receipt:

```bash
python scripts/finalstand_offline.py forbidden \
  --rules gt_finalstand/forbidden_capability_rules.json \
  --groundtruth-root "$GROUNDTRUTH_ROOT" \
  --out gt_finalstand/receipts/forbidden_scan.json
```

## Acceptance receipt

Upload the JSON receipts, exact workflow file, commit SHAs, `go version`, test logs, generated
language manifest, and SHA-256 values as immutable GitHub Actions artifacts. A clean scan preserves
FS-022's `REMOVED` state; any reachable match reopens it. No dry-run receipt can satisfy FS-024.

After upload, bind the artifact returned by GitHub's Actions API into
`receipts/fs023_provenance.json`. Record the numeric artifact ID and the API's `sha256:` digest,
along with the run ID, attempt, URL, repository, workflow ref/SHA, and harness execution commit.
The validator re-fetches both the public run and artifact API records, downloads the artifact,
verifies the API digest, and safely inspects the outer and inner ZIP files. Duplicate names, path
traversal, missing required members, stale receipt bytes, a mismatched workflow receipt, or a
workflow file that differs from the bytes fetched at the recorded GitHub workflow SHA all fail
closed. Merely copying or editing a local workflow receipt cannot close FS-023.

```bash
gh api repos/OWNER/REPOSITORY/actions/runs/RUN_ID
gh api repos/OWNER/REPOSITORY/actions/artifacts/ARTIFACT_ID
```
