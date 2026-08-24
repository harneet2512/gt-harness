# GroundTruth Performance Audit

Subject: `79321e0da09174805a0909f69dc695dd129a5ebf`

Linux 32-GB Codespace, frozen clean repositories. Times are milliseconds;
RSS and graph sizes are peak/process bytes and persisted DB bytes.

| Repository | Cold build | Warm p95 | Query p95 | Peak RSS | Graph size |
| --- | ---: | ---: | ---: | ---: | ---: |
| itsdangerous | 529 | 4.98 | 5.45 | 111,521,792 | 1,110,016 |
| Django | 123,392 | 78.47 | 76.21 | 469,045,248 | 317,870,080 |
| Pydantic | 24,384 | 10.89 | 10.36 | 257,634,304 | 89,010,176 |
| Express | 1,227 | 6.50 | 6.99 | 128,892,928 | 4,427,776 |
| Redux | 2,114 | 8.58 | 9.12 | 128,901,120 | 3,207,168 |
| pnpm | 76,545 | 67.27 | 68.18 | 463,519,744 | 269,991,936 |
| gorilla/mux | 870 | 5.15 | 5.29 | 132,173,824 | 2,617,344 |
| Testify | 2,086 | 5.51 | 5.91 | 133,857,280 | 8,785,920 |
| ripgrep | 3,820 | 7.25 | 8.15 | 144,216,064 | 19,210,240 |
| Gson | 4,076 | 8.97 | 8.82 | 144,216,064 | 23,023,616 |

The large Django and pnpm cold builds are material (123 s and 77 s). Warm and
query p95 remain below 80 ms. Correctness currently wins over file-keyed update
speed: post-edit updates are full atomic rebuilds and are explicitly receipted as
`full_fallback_unproven_incremental_parity`.

Receipt: `audit/receipts/codespaces-79321e0/real-repository-matrix.json`.
