# final_hardening — complete handoff

**Written for a session that has none of this context.** Last updated 2026-09-04 ~17:00 ET.

---

## 1. What was delivered

A 15-row GT-vs-GitNexus delta table, turned into 11 plan items, all landed,
wired end to end, CI green on every platform, producer re-pinned with verified
lineage. One paid smoke dispatched (pre-existing failure, not a regression).

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
| **Harness landing** | `har81/canonical-task-identity` | `8ef705f0` (CI GREEN run `33860505208`) |
| **Producer landing** | `final_hardening/producer` | `5dc180a5e` (CI GREEN run `33889762269`, all 9/9 jobs) |
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

## 6. CI infrastructure

- **Harness:** push trigger on `har81/canonical-task-identity` → auto CI
- **Producer:** push trigger on `final_hardening/**` → auto CI + build workflow
- **Full Go test suite** in CI (`go test -tags sqlite_fts5 ./...`)
- **Ruff lint:** 210 mechanical fixes, explicit rule set, all clean
- **Python suite:** 0 failed on all 6 platform legs

## 7. Producer re-pin

- Binary SHA: `071fd6cb941b12adf762694c4bef1fb7f126841e4e587d56fd3b90b02002ca32`
- Source: `9d0d8079f8d3db7b1bf5208c92918e6daf9d62a6`
- Build: `2026-09-04T08:38:26Z`, go1.22.5, sqlite_fts5
- Lineage: 68-commit ancestry, 567 changed files, attestation verified
- Harness CI: GREEN (run `33860505208`)
- Workflow: verify-vendored (not rebuild — Go CGO static builds not reproducible)

## 8. Why the smoke failed

**PRE-EXISTING — NOT a final_hardening regression.**

Error: `GT_PROVIDER_REQUEST_TOO_LARGE` on task `arktype-json-schema-refs-dependencies`

The OLD producer also failed on the same task:
- Run `33730572741` (Sep 3, `cffca1fd2`, `main`): FAILED
- Run `33708231670`: FAILED

The smoke workflow tests ONE task. That task exceeds the provider's request
limit. `provider_calls: 0` — failed before any LLM call. The readiness check
passed — the producer is verified; the failure is at the agent/provider level.

Options: (a) cap evidence context size, (b) different task, (c) dispatch full
19-task sweep where most tasks won't hit the limit.

## 9. What remains

### Blocking (owner decision)
1. **Smoke resolution** — choose approach for `GT_PROVIDER_REQUEST_TOO_LARGE`

### Non-blocking
2. **`SYMBOL_LABELS` extension** — new labels unreachable until extended
3. **50 xfailed tests** — each with named reason; XPASS-fail when fixed
4. **Oracle corpus** — vendor or env-gate
5. **lua/sql/svelte** — three languages index no symbols (pre-existing)

## 10. Constraints

- **No GT-off** ever
- **Dispatch:** 1 of 4 spent (on pre-existing failure)
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
gh run view 33860505208 --repo harneet2512/gt-harness

# Producer CI (all 9 green)
gh run view 33889762269 --repo harneet2512/groundtruth

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
