# GroundTruth Graph Build Audit

Subject: `79321e0da09174805a0909f69dc695dd129a5ebf`

Verdict: **PASS** on the frozen ten-repository Linux matrix.

Builder identity: `gt-index-source-b8c66604d39fe1bd-repository-identity-v4`.
Source identity: `b8c66604d39fe1bda0a40c03ff4d12730560c26f81136f8a7e1fb42f30bcd859`
over 83 files. Every repository was checked out at its frozen commit, built,
persisted, reopened, identity-checked, and queried through `RepositoryGraphService`.

| Repository | State | Discovered | Indexed | Failed | Symbols | Nodes | Edges | Coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| itsdangerous | READY | 50 | 29 | 0 | 146 | 173 | 449 | 1.000 |
| Django | READY_WITH_DECLARED_LIMITATIONS | 7,085 | 3,500 | 0 | 45,224 | 48,040 | 142,622 | 1.000 |
| Pydantic | READY_WITH_DECLARED_LIMITATIONS | 816 | 729 | 0 | 16,569 | 17,284 | 38,345 | 1.000 |
| Express | READY_WITH_DECLARED_LIMITATIONS | 213 | 163 | 0 | 1,298 | 1,457 | 1,848 | 1.000 |
| Redux | READY_WITH_DECLARED_LIMITATIONS | 477 | 334 | 0 | 677 | 1,008 | 1,105 | 1.000 |
| pnpm | READY_WITH_DECLARED_LIMITATIONS | 5,839 | 4,246 | 0 | 34,848 | 39,069 | 94,920 | 1.000 |
| gorilla/mux | READY | 27 | 23 | 0 | 271 | 293 | 880 | 1.000 |
| Testify | READY_WITH_DECLARED_LIMITATIONS | 91 | 73 | 0 | 1,156 | 1,229 | 3,317 | 1.000 |
| ripgrep | READY_WITH_DECLARED_LIMITATIONS | 237 | 161 | 0 | 3,917 | 4,077 | 10,056 | 1.000 |
| Gson | READY_WITH_DECLARED_LIMITATIONS | 313 | 292 | 0 | 4,144 | 4,433 | 12,459 | 1.000 |

Coverage is indexed divided by attempted supported files, not all repository
files. Every skip and parser limitation remains in the receipt. No repository
had a failed attempted file. `READY_WITH_DECLARED_LIMITATIONS` is intentional
and queryable; `DEGRADED`, `FAILED`, `STALE`, `ABSENT`, and `BUILDING` are not.

Receipt: `audit/receipts/codespaces-79321e0/real-repository-matrix.json`.
