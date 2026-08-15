# ARB V2 Setup Audit

Benchmark setup only: this artifact does not alter the GT runtime, invoke a
model, or launch a paid run.

## Pinned source and releases

- Official repository: `eyuansu62/agent-retrieval-bench`
- Pinned checkout: `07014c986f3deadb1548c62b32c0ffbe6a81465d`
- Package version: `0.2.1`
- Dataset repository: `eyuansu71/agent_retrieval_bench`
- Python requirement: `>=3.10`

Primary V2 releases:

| Release | Rows | Workflow |
| --- | ---: | --- |
| `v2_code2test` | 106 | implementation signal -> related tests |
| `v2_comment2context` | 80 | review comment + given file -> additional context |
| `v2_trace2code` | 101 | failure trace -> root-cause source |
| `v2_edit2ripple` | 58 | anchored edit -> affected files |
| `v2_abstention` | 82 | natural/counterfactual no-gold |

The positive workflows contain 345 rows. The abstention release contains 82
no-gold rows (50 natural and 32 counterfactual). The two selective input
releases are `v2_selective_retrieval_balanced` and
`v2_selective_retrieval_natural`; they reuse the five primary corpora.

## Official download and extraction commands

From `D:\gt-harness`:

```powershell
$env:PYTHONPATH = "artifacts/final_execution/arb-upstream/src"
\.venv\Scripts\python.exe -m agent_retrieval_bench.cli releases --json
\.venv\Scripts\python.exe -m agent_retrieval_bench.cli download-benchmark `
  --all `
  --local-dir artifacts/final_execution/arb-upstream/data `
  --force
```

The official extractor invokes `zstd -dc` and `tar -xf -`. Windows setup must
provide both `zstd` and `tar` on `PATH`. If `zstd` is absent, download and
checksum verification can succeed while extraction fails. A benchmark is not
ready until `benchmark/`, `corpus/`, `eval/`, and `reports/` exist for every
release.

Download selective inputs explicitly:

```powershell
\.venv\Scripts\python.exe -m agent_retrieval_bench.cli download-benchmark `
  --version v2_selective_retrieval_balanced `
  --local-dir artifacts/final_execution/arb-upstream/data `
  --force
\.venv\Scripts\python.exe -m agent_retrieval_bench.cli download-benchmark `
  --version v2_selective_retrieval_natural `
  --local-dir artifacts/final_execution/arb-upstream/data `
  --force
\.venv\Scripts\python.exe -m agent_retrieval_bench.cli merge-corpus-manifests `
  --local-dir artifacts/final_execution/arb-upstream/data
```

The selective merge uses the five primary releases and, in this checkout,
produced 271 unique `(repo, base_commit)` snapshots from 365 manifest rows
(94 duplicates).

## Verified local download state

All seven release archives were downloaded and checksum-verified, then
extracted. The verified SHA-256 values are:

```text
v2_code2test                    387cdfb25835b176ba27707d43dc31cae6b31514231923af237ad04deef19c0b
v2_comment2context              be68161844d63c64e687e0c6da1d52434160fafcaf8932a5767fbe64b643b3f5
v2_trace2code                   19b252e8cfff42107fedc74005dbb6972f2970af33651ce0c1571546819e41c4
v2_edit2ripple                  a174196d69b531d176a65c76fea928b3f1c893710baa4efccc48e901ff404b2c
v2_abstention                   bb358d6b65b7ef6aa36b417240cab4f2d51a8d69e699e573cf324ab339a5c1d8
v2_selective_retrieval_balanced 470876de20453e22fa905e132dfdd1d99799bb4bda0e1acd3ea32f17267956c9
v2_selective_retrieval_natural  1be829d7fc03631765bff389970afabd4f74be54d9c0d7eaab00bb9c467fc0cc
```

## Gold-isolated inputs

ARB sample rows include `gold`, `gold_blocks`, `gold_spans`, fix commits,
negative files, and audit metadata. Those fields must never enter the GT
adapter process. Generate inputs with:

```powershell
\.venv\Scripts\python.exe scripts/prepare_arb_redacted_inputs.py `
  --data-dir artifacts/final_execution/arb-upstream/data `
  --out-dir artifacts/final_execution/arb-redacted-inputs
```

This generated 427 rows. The redacted schema is exactly:

```text
sample_id
repository
base_commit
task_type
instruction
active_paths
```

Projection rules:

- `code2test`: PR title/body/change summary; `changed_file` is the only active
  path.
- `comment2context`: PR title/review comment/diff hunk; the reviewed/given
  file is the only active path.
- `trace2code`: command/failure excerpt/run strategy/source type; no path is
  invented from gold data.
- `edit2ripple`: intent/anchor diff; the anchor file is the only active path.
- `abstention`: query text only; no active path.

The projector writes no gold sidecar. After predictions are finalized, the
evaluator joins prediction `sample_id` values with the original ARB release.
The focused tests are `tests/test_prepare_arb_inputs.py` and
`tests/test_arb_adapter.py` (6 passed).

## Runtime workspace requirement and blocker

`scripts/arb_adapter.py` calls the active GT graph path and accepts one
`--repo-root`. It must run against a lossless checkout of the exact
`(repository, base_commit)` pair, grouped one snapshot at a time:

```powershell
\.venv\Scripts\python.exe scripts/arb_adapter.py `
  --samples artifacts/final_execution/arb-redacted-inputs/v2_trace2code.redacted.jsonl `
  --repo-root <lossless-checkout-at-base-commit> `
  --state-dir artifacts/final_execution/arb-state/<repo>--<base_commit> `
  --output artifacts/final_execution/arb-predictions/<repo>--<base_commit>.jsonl
```

ARB `.chunks.jsonl` files are candidate/evaluation data and may truncate large
files; they are not a lossless production graph workspace. Do not index them
as if they were source checkouts. The setup blocker before full GT retrieval
evaluation is a cache/runner that materializes each exact base commit (for
example, bare Git cache plus temporary worktree), verifies `HEAD == base_commit`,
and invokes the adapter only for that snapshot's rows.

## Official baseline commands

All baselines use `--candidate-filter all_files` and `--no-keep-list`.

```powershell
$d = "artifacts/final_execution/arb-upstream/data"
$env:PYTHONPATH = "artifacts/final_execution/arb-upstream/src"
\.venv\Scripts\python.exe -m agent_retrieval_bench.cli eval-baseline `
  --derived "$d/benchmark/v2_edit2ripple" `
  --corpus "$d/corpus/v2_edit2ripple" `
  --ranker lexical --candidate-filter all_files --no-keep-list `
  --out "$d/eval/v2_edit2ripple/lexical_summary.json" `
  --details "$d/eval/v2_edit2ripple/lexical_details.jsonl"

\.venv\Scripts\python.exe -m agent_retrieval_bench.cli eval-baseline `
  --derived "$d/benchmark/v2_edit2ripple" `
  --corpus "$d/corpus/v2_edit2ripple" `
  --ranker bm25 --candidate-filter all_files --no-keep-list `
  --out "$d/eval/v2_edit2ripple/bm25_summary.json" `
  --details "$d/eval/v2_edit2ripple/bm25_details.jsonl"

\.venv\Scripts\python.exe -m agent_retrieval_bench.cli eval-repomap `
  --derived "$d/benchmark/v2_edit2ripple" `
  --corpus "$d/corpus/v2_edit2ripple" `
  --candidate-filter all_files --no-keep-list `
  --out "$d/eval/v2_edit2ripple/repomap_summary.json" `
  --details "$d/eval/v2_edit2ripple/repomap_details.jsonl"
```

Replace `v2_edit2ripple` with each positive release. For abstention:

```powershell
\.venv\Scripts\python.exe -m agent_retrieval_bench.cli eval-selective-baseline `
  --derived "$d/benchmark/v2_selective_retrieval_natural" `
  --corpus "$d/corpus/v2_selective_mixed" `
  --ranker lexical --candidate-filter all_files --no-keep-list `
  --out "$d/eval/v2_selective/lexical_summary.json" `
  --details "$d/eval/v2_selective/lexical_details.jsonl"
```

Official metrics are MRR, Recall@K, Precision/F0.5, token-budget BCY, and
selective success@20. ARB proves retrieval behavior only; it does not prove
model reasoning, timing, trajectory utility, or task solve rate.

## Current status

| Item | Status |
| --- | --- |
| Pinned ARB source | complete (`07014c9`) |
| Five primary V2 archives | downloaded and checksum-verified |
| Two selective archives | downloaded and checksum-verified |
| All seven releases extracted | complete |
| Selective merged corpus manifest | complete (271 unique snapshots) |
| Gold-free input projector | complete (427 rows) |
| Adapter focused tests | pass (6 tests) |
| Lossless base-commit worktree cache | not built |
| Full GT retrieval evaluation | not run |
| Paid model calls | none |

The next setup task is the lossless checkout/cache runner. Do not run GT
retrieval against the truncated chunk corpus.
