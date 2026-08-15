# GroundTruth Phase II Live TODO

This is the terminal operational authority for FS-001 through FS-026. The machine state remains [closeout_status.csv](closeout_status.csv). All rows are terminal under the project owner's one-run-vs-local-baseline override; there is no remaining execution queue.

## Live queue

| TODO | Decision | Status | Terminal criterion | Current evidence and next proof |
|---|---|---|---|---|
| FS-001 | BUILD | COMPLETE | Canonical contracts, strict validation, deterministic bytes, and decision truth table pass. | Observation compiler exists; 14/14 new contract and 120/120 focused core tests passed. |
| FS-002 | BUILD | COMPLETE | One snapshot authority detects every relevant worktree/config/revision change and ignores irrelevant caches. | Content-addressed pre/post action authority is runtime-wired; final harness authority receipt is 129 collected, 128 passed, 1 environment-conditional skip. |
| FS-003 | MODIFY | COMPLETE | Native registry produces exactly 30 collision-free languages and the harness consumes the generated manifest. | GroundTruth manifest/parity and full tagged Go suites passed in Codespaces; generated hash `53afc445...` and 30 sorted identities are the harness authority, and Mini-SWE binds that nonempty hash by default; focused harness receipt 28/28 passed. |
| FS-004 | BUILD | COMPLETE | Seven typed actions coexist with byte-compatible stock Bash and fail open without another model call. | Native tool and tests exist; the final provider-free harness suite collected 592 tests, passed 591, and recorded one declared complementary graph skip. |
| FS-005 | MODIFY | COMPLETE | Exact final provider bytes join one request, response, and immediate next action across retry/restart/error cases. | Final request/response/next-action lineage and exact provider-bound receipts pass the final harness authority suite. |
| FS-006 | BUILD | COMPLETE | Off/shadow/augment/replace/enforce precedence and every capability kill switch pass rollback tests. | Global modes, independent capability controls, GT-off restoration, kill switches, and fail-open paths pass the final harness authority suite. |
| FS-007 | MODIFY | COMPLETE | Ten cold semantic builds match byte-for-byte and source, Windows, and vendored Linux binaries pass one corpus. | Codespace GroundTruth `7fcb0191...`: full `CGO_ENABLED=1 go test -tags sqlite_fts5 ./...` passed; runtime freshness and atomic publication cluster passes. |
| FS-008 | BUILD | COMPLETE | Explicit-scope literal results equal the reference byte scanner; every omission revokes exactness. | Producer exists; 9/9 focused and 132/132 related GroundTruth tests passed. |
| FS-009 | MODIFY | COMPLETE | One ordered transaction captures multi-file add/edit/delete/rename/mode/symlink effects and partial failure. | Canonical pre/post snapshots, ordered multi-file deltas, exact blobs, modes, symlinks, rename candidates, and omission semantics are runtime-wired and tested. |
| FS-010 | MODIFY | COMPLETE | Every changed applicable file has an immediate revision-bound syntax receipt or explicit unsupported record. | Immediate transaction artifacts emit one post-revision syntax row for every changed file: Python exact, deletion not applicable, and every other file explicitly unsupported; 66/66 closure tests passed. |
| FS-011 | MODIFY | COMPLETE | Exact patch reconstructs postimages and callers never claim completeness without a certified closed world. | Untruncated patches plus exact postimage bytes/hashes reconstruct text, binary, create, modify, and delete changes; callers remain `graph_recorded`, never complete; 66/66 passed. |
| FS-012 | MODIFY | COMPLETE | Structured build/test parsing can fail without dropping a raw byte and every result is execution-scoped. | Execution artifacts preserve raw observations and bind command, outcome, revision, transaction, and delivery; parse failure retains raw output. |
| FS-013 | BUILD | COMPLETE | Incremental and clean-full results match across edit/config/delete/rename/crash cases with atomic fallback. | Runtime rejects stale graph evidence, invalidates on edits, atomically publishes refreshes, and fails open; full tagged Go suite passed. |
| FS-014 | MODIFY | COMPLETE | Every definition/reference/caller pair has certified language/config scope or is absent from the typed schema. | All definition/reference/caller pairs are terminally removed, the generated public schema omits them, and runtime certification rejects manual construction; typed contract passed 16/16. |
| FS-015 | BUILD | COMPLETE | All 210 language-operation pairs are terminally certified and the public schema is generated from that matrix. | GroundTruth authority records 35 exact, 30 execution-specific, 145 removed; harness advertises only literal, syntax, and verification. |
| FS-016 | MODIFY | COMPLETE | Localization stays advisory, gives stable anchors and score reasons, and passes tie/stale/no-match fixtures. | Advisory anchors/scores/reasons/ties, dirty files, no-match and unavailable fallback pass; existing stale graphs are gated by `graph_fresh`, fall back lexically, and record `graph_localization_stale`; Codespaces cell 620 passed 27/27 plus lint. |
| FS-017 | MODIFY | COMPLETE | Task-span obligation IDs deliver deltas and invalidate on task/edit/verification changes. | Exact UTF-8 task-span IDs, episode-session wiring, delta-only emission, and revision invalidation pass the 67/67 widened GroundTruth artifact suite. |
| FS-018 | MODIFY | COMPLETE | Recovery reuses only an identical normalized failure identity and never prescribes action for similar-only failures. | Exact normalized action/environment/revision/outcome/diagnostic identity is episode-wired; similar-only failures do not match; 67/67 widened tests pass. |
| FS-019 | BUILD | COMPLETE | On-demand build/config adapters qualify target membership, generated inputs, and dependency edges. | Repository adapters emit deterministic sound-overapprox slices on demand; unresolved targets, membership, generated inputs, and dependency edges are explicit omissions rather than false exactness. |
| FS-020 | MODIFY | COMPLETE | New-file precedent is provenance-rich, path-specific, inspectable, and incapable of replacement/suppression. | Same-action advisory binds revision/transaction, reasons, destination and sibling paths; preserves raw output, stays quiet without precedent, excludes exact renames, runs no hidden command, and only augments; 66/66 passed. |
| FS-021 | BUILD | COMPLETE | Fresh closed-scope blockers alone suppress submit in enforce mode, with provider-absence receipt and rollback. | Exact closed fresh blocker gate, immediate enforce kill switch, zero-provider-byte suppression receipt, and receipt-failure fail-open behavior pass the 67/67 widened suite. |
| FS-022 | REMOVE | REMOVED | Prohibited capabilities have zero public/default visibility and zero execution; comparison controls are isolated. | AST import closure covers 66 files reachable from Mini-SWE and reports zero forbidden findings; typed schema omits comparison controls. |
| FS-023 | BUILD | COMPLETE | The complete offline battery passes in one immutable external workflow with artifacts and hashes. | GitHub Actions [run 30729901088](https://github.com/harneet2512/gt-harness/actions/runs/30729901088) passed every step at harness `e87cada097f55fe5df203c339148c65fff75c36a` and GroundTruth `61cfdbce2c42751c11028e46e863b3231f0bb70e` with zero provider calls. Artifact `8827623572` is API-bound by digest `1de4fa253719edf851484d8ab98b7e9b7077f11552a6f8c18ecf0401c328ac74`; inner bundle SHA-256 is `64f416aee72fdc3ed6828ca0cb68ceda68455b3d363997053280cc71cf92150f`. |
| FS-024 | BUILD | COMPLETE | Execute the owner-approved single matched witness and freeze a bounded descriptive analysis. | Actions run `30731388242`, artifact `8828119172`: the provider trial and verifier passed; offline postprocessing was repaired locally without another provider call. Both arms received reward `1.0`. Calls changed 33 -> 25, actions 33 -> 37, pre-edit exploration 19 -> 25, and raw pre-edit bytes 34,696 -> 43,009. This supports no general efficacy or exploration-reduction claim. |
| FS-025 | KEEP | COMPLETE | Make the conservative release decision from the bounded witness without weakening baseline behavior. | Stock Mini-SWE remains the default. GroundTruth remains explicit opt-in behind existing mode controls and rollback switches. The reward tie avoids a one-task regression finding, but the worse exploration/byte measures do not justify default promotion. |
| FS-026 | BUILD | COMPLETE | Publish a terminal, explicitly bounded attestation for every FS row. | All 26 rows are terminal, with FS-022 removed and every other row complete. The attestation incorporates FS-023's immutable provider-free proof and FS-024's one-task descriptive witness; it does not claim benchmark-wide solve-rate improvement, non-inferiority, or exploration reduction. |

## Current post-audit proof note

The bounded hardening inventory and proof surfaces are recorded in [POST_AUDIT_HARDENING.md](POST_AUDIT_HARDENING.md). The earlier machine-readable [Codespaces receipt](receipts/final_codespace_verification.json) records GroundTruth **9,980 passed, 415 skipped, 6 expected failures**, harness **592 collected, 591 passed, 1 skipped**, offline smoke **10 collected, 9 passed, 1 skipped**, and a passing full tagged Go suite. Immutable Actions run `30729901088` subsequently reran the provider-free closure at committed inputs and closed FS-023. The owner then superseded the six-arm protocol with the completed one-task matched witness described above.

## Closed checkpoint loop

No recurring checkpoint is required. If the repository is reopened, use this audit sequence:

1. Pull the latest external workflow state by immutable run identity.
2. Update only evidence that actually changed.
3. Run `python scripts/generate_gt_finalstand.py --check` and `python scripts/validate_gt_finalstand.py` in the Codespace or GitHub Actions job.
4. Keep every missing proof attached to its FS row.
5. Do not reopen a terminal row without recording a new owner-approved scope.

## Current execution state

- Machine authority: **25 `COMPLETE`, 0 `IN_PROGRESS`, 1 `REMOVED`**.
- Provider-free regression state: immutable GitHub Actions run `30729901088` is green at harness `e87cada097f55fe5df203c339148c65fff75c36a` and GroundTruth `61cfdbce2c42751c11028e46e863b3231f0bb70e`; artifact `8827623572` and both archive hashes are recorded above.
- Open terminal gates: none.
- FS-022 remains `REMOVED` by decision.
- Historical checkpoint records are preserved in [LIVE_TODO_HISTORY.md](LIVE_TODO_HISTORY.md). They are superseded and are not current status authority.
