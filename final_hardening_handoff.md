# final_hardening — complete handoff

**Written for a session that has none of this context.** Last updated 2026-09-04 ~19:45 ET.

---

## 1. What was delivered

A 15-row GT-vs-GitNexus delta table, turned into 11 plan items, is implemented
and wired end to end. The corrective pass closed dynamic provider admission,
receipt verification, incremental invalidation, taxonomy reachability, the
omitted named edge interfaces, legacy co-change provenance, and static producer
artifact construction. Final CI/review/smoke evidence is recorded below.

## 2. The 15-row delta table — ALL LANDED

| # | Row | Strategy | SHA |
|---|---|---|---|
| 1 | Content addressing `(file_hash, byte_start, byte_end)` | INVERT | producer + harness |
| 2 | Behavioural contract | COMPOSE | `fac84bcc` |
| 3 | Contract embeddings bound to `stable_id` | COPY | harness |
| 4 | Hybrid retrieval (FTS + LIKE fallback) | PROJECT | `1122c213` + FTS |
| 5 | Fingerprint invalidation | INVERT | harness |
| 6 | Communities (COPY+AMPLIFY) | COPY+AMPLIFY | `43514ced1` + `24d156530` |
| 6a | Co-change extraction | COMPOSE | `ce5e0370` |
| 6b | Co-change engine half | COMPOSE | harness |
| 7 | Test-witnessed processes | COMPOSE | `a2d536bf4` |
| 8-9 | Symbol taxonomy + edge kinds (30 grammars) | COMPOSE/INVERT | producer |
| 10-13 | reason/step/overload/MRO projections | PROJECT | producer |
| 14 | Budgeted abstention (COPY+AMPLIFY) | COPY+AMPLIFY | producer |
| 15 | Two-phase publication | INVERT | producer |

## 3. Branches and heads

| Purpose | Branch | Head |
|---|---|---|
| **Harness landing** | `har81/canonical-task-identity` | `8c19e52b` plus the closeout follow-up carrying this handoff |
| **Producer landing** | `final_hardening/producer` | `af0854537` (exact static build run `33911978151`) |
| **Review inbox** | `gt-review-inbox` | `9043aab7` |
| Producer worktree | `D:/gt-fh-producer` | same as landing |
| Harness worktree | `D:/gt-har81-canonical` | same as landing |

Push commands for the producer (harness auto-pushes):
```bash
cd D:/gt-fh-producer
GCM_INTERACTIVE=never git -c credential.helper= \
  -c credential.helper='!gh auth git-credential' push origin final_hardening/producer
```

## 4. What was wired end to end

- `properties_fts` → `property_rank` MATCH path (36.5× faster, LIKE fallback) ✓
- `analysis_state` → certification manifest → `IndexBuildReceipt` → `BUILT_CORE_ONLY` ✓
- `cochange_rows` → `graph_utilisation` → enforcement gate ✓
- Derived-layer `project_meta` keys → certification manifest ✓
- `RETURNS_TYPE`/`OVERRIDES`/`DECLARED_IMPLEMENTS` → `why_this_edge.ALLOWED_EDGE_KINDS` ✓
- Content addressing → `bridge.py` task-start orientation ✓
- Embedding refresh → `ensure_index_with_receipt` ✓

## 5. Boa settlement

**"Publication never terminates" was false.** Every run was capped at 1500s;
publication takes 2,162s.

- boa publishes: `exit=0`, **36m02s**, 883 files, 1,353,067 nodes, 8.1 GB
- Per-callsite flow-fact max: **8** — no budget above 8 changes boa
- Budget inert on healthy repos: arktype `+0.0000%` on every count at default
- `pass_coverage` removal: **713 MB of 845 MB** redundant JSON, read by nothing
- 19-repo sweep: 19/19 exit 0, boa in **788s** (63% faster with the fix)

## 6. Verification and CI

- **Producer local Python:** 4,225 passed, 192 skipped, 6 expected xfails.
- **Producer local Go:** `go test -tags sqlite_fts5 ./...` passed.
- **Corrective focused Go:** specs, taxonomy, parser, resolver, store and cmd passed.
- **Harness focused corrective suite:** 184 passed; co-change/edge follow-up passed.
- **Harness full Windows run:** all non-binary tests passed; seven tests fail closed with
  `GT_INDEX_RESOURCE_GUARD_UNAVAILABLE` because the certified artifact is Linux ELF.
- **Producer:** push triggers multi-platform CI and a separate static Linux build.
- **Harness:** push triggers the canonical provider-free product acceptance workflow.

## 7. Producer re-pin

- Binary SHA: `932b6336adf0a82f7d752cbcc0a508fc357546b37eb857d45da9fc273401928d`
- Source: `af08545371d8ac75fdf7b82c5858a2818202c379`
- Source tree: `b34451a2eebae8061b9b41367de662be54b6e2c8`
- Build-info SHA: `c1729ba72e3f5d2c37f181f270711366471706774fa9d2ffa7a496cd73a093da`
- Build: go1.22.5, `sqlite_fts5`, musl/static; workflow run `33911978151`
- Lineage: complete ancestry from the accepted default through the exact producer head;
  source tree, executable bytes and embedded build identity verified.

## 8. Prior smoke failure and correction

**PRE-EXISTING — NOT a final_hardening regression.**

Error: `GT_PROVIDER_REQUEST_TOO_LARGE` on task `arktype-json-schema-refs-dependencies`

The OLD producer also failed on the same task:
- Run `33730572741` (Sep 3, `cffca1fd2`, `main`): FAILED
- Run `33708231670`: FAILED

The prior smoke used a hard-coded request ceiling and failed before any provider
call. The runtime now fetches the selected model's live context window, reserves
the configured completion budget, measures the exact prepared messages, and
receipts either admission or refusal. The correction must be validated by one
new run of the same arktype task; the other 18 tasks remain halted.

## 9. What remains

1. Exact-head producer CI must finish green.
2. Harness closeout commit must pass provider-free acceptance in CI.
3. Final standards/spec review must be rerun against the closeout SHAs.
4. One paid arktype smoke must be dispatched and analyzed; the other 18 stay halted.

The requested overload-resolution result above the 36% baseline is not a valid
remaining implementation target under the stronger no-promotion invariant. The
measured narrowing removed wrong candidates and preserved the selected-target
rate; exceeding 36% would require promoting ambiguous candidates. That criterion
is internally inconsistent and is not reported as achieved.

## 10. Constraints

- **No GT-off** ever
- **Dispatch:** only one corrective arktype smoke is authorized in this closeout;
  the other 18 tasks remain halted
- **Never bypass hooks**; fixture-first on protected paths
- **Candidate evidence never promoted**
- **REVs answered:** 253-292

## 11. Traps

1. Nothing over ~8 min foreground (600s watchdog)
2. `post-commit` hook auto-pushes
3. Killed runs leave multi-GB WAL
4. Go CGO static builds NOT byte-reproducible
5. Smoke needs `approve_paid_run=true` input
6. `block-no-verify` false-positives on messages quoting the flag

## 12. Verify

```bash
# Harness CI
gh run list --branch har81/canonical-task-identity --repo harneet2512/gt-harness

# Producer CI (all 9 green)
gh run list --branch final_hardening/producer --repo harneet2512/groundtruth

# Release blockers
cd D:/gt-har81-canonical && python -c "
import json, pathlib; import gt_harness.product as P
d = json.load(open('config/deepswe_product_bundle_v1.json'))
print(P._groundtruth_release_blockers(d['groundtruth'], root=pathlib.Path('.')))
"

# Dispatch smoke (needs owner authorization)
gh api repos/harneet2512/gt-harness/actions/workflows/347688665/dispatches \
  -f ref=har81/canonical-task-identity -f "inputs[approve_paid_run]=true"
```
