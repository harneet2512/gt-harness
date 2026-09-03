# GT harness pipeline flow

Where a benchmark run starts, every layer it passes through, what each layer is
capable of, and — separately — what is actually *proven* about each one today.

Status vocabulary, used deliberately:

| Mark | Meaning |
|---|---|
| **VERIFIED** | I ran it this session and saw the result |
| **CODE-READ** | I read the implementation; behaviour asserted, not observed |
| **BROKEN** | Reproduced failing |
| **UNPROVEN** | Plausible from code, never observed end to end |

Nothing below is marked VERIFIED on the strength of a passing unit test alone.
A test proves the code does what the test says; it does not prove the layer
does its job in a real run.

---

## Layer 0 — Dispatch admission (before a single dollar is spent)

**Starts at:** `.github/workflows/deepswe_gt_harness_product_p0731.yaml` (paid
smoke20), with `deepswe_gt_harness_product.yml` as the provider-free
acceptance gate that must pass first.

**Capabilities**

- Rebuilds the producer *in-workflow* from the pinned Groundtruth source and
  asserts the vendored binary equals what it just built. The vendored binary is
  never trusted on sight.
  Recipe: `golang:1.22.5-bookworm@sha256:a07daa84…`, static
  (`-linkmode external -extldflags=-static`), `-trimpath -mod=readonly`,
  `-buildvcs=false`, `BUILD_TIME` hardcoded to `2026-09-02T06:51:07Z` so the
  digest reproduces.
- Verifies lineage and review provenance against a **separate review-inbox
  branch** (`gt-review-inbox`), pinned by `review_inbox_commit`. A review packet
  must exist there as a file, appear exactly once in `inbox/INDEX.json`
  `live_packets`, belong to exactly one ticket, and carry a digest over its own
  body.
- Pins that must move together: `producer_sha256`, `source_commit`,
  `source_tree`, `build_info_sha256`, `builder_image_digest`, plus two pins
  *outside* the bundle — `eval/miniswe_agent.py::_GT_BINARY_SHA256` (a runtime
  guard) and the workflow's `sha256sum --check`.

**Status**

| Item | Mark | Evidence |
|---|---|---|
| CI rebuild + digest assert | **VERIFIED** | Run 33778444842 failed the assert on a wrong pin; 33779096992 passed it after re-pinning to CI's own artifact `b4bfba37…` |
| Static linking | **VERIFIED** | CI `file` output: "statically linked"; ELF has no `PT_INTERP`, no `PT_DYNAMIC` |
| Lineage/provenance verification | **VERIFIED (as blocking)** | Run 33779096992: `exact_source_review_packet_missing` — it genuinely cannot be satisfied by editing the bundle |
| Review packet for `cffca1fd2` | **BROKEN / open** | Must be authored on `gt-review-inbox`; does not exist yet |

> This layer is the reason no money has been spent on a bad build. It is
> working *as designed* even though it is currently red.

---

## Layer 1 — Task materialisation

**Starts at:** Pier + Harbor (`datacurve-pier==0.3.1`, harbor 0.20.0) launching
the task's own container from `deepswe-bench/tasks/<task>/environment/Dockerfile`.

**Capabilities**

- Clones the upstream repo and rewrites its default branch to the task's
  `BASE_SHA`, then gc's future history so the reference solution cannot leak.
- Mounts the agent (`mini-swe-agent 2.4.6`) and the producer binary at
  `/opt/groundtruth/gt-index/gt-index`.
- Stages language servers into `/installed-agent/lsp-bin` and prefixes `PATH`.

**Status**

| Item | Mark | Evidence |
|---|---|---|
| Task repo at base commit | **VERIFIED** | I cloned 19 of them at their own `BASE_SHA` and indexed them |
| LSP binaries staged | **CODE-READ** | 15 server references in the gate workflow; never observed resolving a symbol in a real run |

---

## Layer 2 — The producer (`gt-index`, Go)

**Starts at:** `gt_engine/indexer.py::ensure_index` → the binary with
`-root -output -max-files -workers -closure=true`.

**Three passes**

1. **Discover** — walk, language detect, `-max-files 200000`.
2. **Parse** — tree-sitter per language, `_INDEX_WORKERS` parallel. Parse
   failures are *counted*, not hidden (arktype: 384/458 parsed, 74 failures
   reported).
3. **Resolve** — a ladder of strategies, each with its own confidence and
   evidence type:

| Rung | Mechanism | Claim strength |
|---|---|---|
| 1.75 | `same_file` / `inherited` (self/this/Self + inheritance) | receiver proven |
| 1.93/1.94a | `import` / `import_type` | declared type |
| 1.94 | `impl_method` (1–3 implementors) | implementation set |
| 1.95/1.96 | `type_flow` (assignment + construction tracking) | declared type |
| 1.97 | `return_type` (return-shape bridging) | declared type |
| 1.98 | `unique_method` (name unique to one class) | **name only**, capped CANDIDATE 0.6 |
| 2 | `name_match` / `verified_unique` | global name, fallback |
| — | `vta` (variable type analysis) | flow-proven, candidate-only |

Then **publish**, atomically: nodes, edges, `closure`, `properties`,
`assertions`, `content_passages`, `cochanges`, `file_hashes`, plus resolution-v2
(`resolution_callsites`, `resolution_candidates`, `resolution_symbols`,
derivation facts).

**The property that dominates everything else:** publication is a single
transaction. One rejected candidate discards the *entire* index, and the run
then proceeds with **no graph at all**. For a graph-only product that is the
worst possible failure mode, and until this session it was silent.

**Status**

| Item | Mark | Evidence |
|---|---|---|
| Parses and resolves real repos | **VERIFIED** | 18/19 smoke20 repos, e.g. arktype 458 files → 159,548 nodes / 188,264 edges / 16,431 callsites |
| Atomic publication | **VERIFIED (the hard way)** | 4 distinct defects each destroyed a whole graph |
| Closure | **VERIFIED** | arktype depth 1: 1402, depth 2: 1150, depth 3: 1050 |
| `MaxDepth = 3`, `MinEdgeConfidence = 0.7` | **VERIFIED** | `internal/closure/closure.go:52,60` — the requested depth-6 change is **not done** |
| boa | **BROKEN** | Publication never terminates; WAL past 11.5 GB; *not* the closure (reproduced with `-closure=false`) |

**Defects found and fixed this session** (all fixture-first, RED replayed in a
clean checkout by the pre-push hook):

1. `validateCandidateDerivation` rejected an empty `Mechanism` that
   `resolutionDerivation` already maps to `global_name`/`partial` — two
   validators contradicting each other.
2. `unique_method` missing from the derivation vocabulary entirely.
3. Partial-VTA callsites claimed `vta` over merged hierarchy candidates that
   carry no flow proof.
4. Candidates sharing one `TargetStableID` collided on the candidate edge id
   (**pre-existing** — the shipped producer `0aadb1b9` fails aiomonitor
   identically, so that task has never had a graph).

---

## Layer 3 — Index lifecycle in the harness

**Starts at:** `gt_engine/indexer.py::ensure_index`.

**Capabilities**

- `_build_index_with_attempts` — 3 bounded retries, attempt trail sealed into
  `index-failure-resource.json` with cgroup memory evidence, exit code, and a
  4 KiB scrubbed stderr tail.
- **`BenchmarkGraphRequired`** — under `identity_scope == "benchmark_bound"`,
  an indexable repo that produced no graph *raises* rather than degrading.
  Re-raised **by name** at both real call sites before any broad handler
  (`scripts/miniswe_gt_run.py:397`, `gt_engine/__init__.py:37`).
- `start_lsp_promotion` → sealed as `gt.lsp_promotion.v1` with four states.
- Dense retrieval: `Snowflake/snowflake-arctic-embed-m@7802add0`, ONNX,
  `gt.snowflake_onnx_asset.v1`.

**Status**

| Item | Mark | Evidence |
|---|---|---|
| Fail-fast prevents a graphless paid run | **VERIFIED** | Gate 33730572741 aborted at **zero provider cost** with the real abort captured |
| Captured stderr is diagnostic | **VERIFIED** | It named `candidate derivation requires stable target identities and mechanism`, which reproduced exactly |
| Retries | **VERIFIED** | `build_attempts: 3` recorded, all `nonzero_exit` |
| LSP promotion actually promotes edges | **UNPROVEN** | Wired and sealed; never observed changing a graph |
| ONNX actually retrieves | **UNPROVEN** | Asset pinned and real; retrieval quality unmeasured |

---

## Layer 4 — The agent loop and decision boundaries

**Starts at:** `gt_engine/__init__.py::create_bridge` → `GTBridge.enrich`
(`gt_engine/bridge.py:3082`).

**Seven boundaries** (`gt_engine/graph_lease.py`): `repository_start`,
`identity_ambiguity`, `pre_edit`, `post_edit_graph_delta`,
`failure_observation`, `verification_selection`, `pre_submit`.

**Capabilities**

- One planning call at `repository_start`; thereafter delivery is deterministic
  and driven by localisation and the action taken (view / edit / open).
- Dedup via `dedup_key` and a last-payload hash, so the same fact is not
  re-shipped.
- Budgets: "an over-budget delta is dropped **whole**, never clipped" — a
  truncated fact would be a lie, so it is withheld instead.
- Graph freshness states: `ABSENT`, `CURRENT`, `STALE`, `BUILDING`, `FAILED`.

**Status**

| Item | Mark | Evidence |
|---|---|---|
| Boundary set and dedup | **CODE-READ** | Read the enum and the dedup/budget laws |
| One LLM planning call | **CODE-READ** | Asserted by design; not measured in a run |
| Deliveries actually improve the agent | **UNPROVEN** | This is what the smoke is *for* |

---

## Layer 5 — Attribution

**Starts at:** `gt_engine/attribution.py::feature_for_evidence` — maps a
concrete evidence envelope to one of the 17 census features. Trace is
append-only and hash-chained, and "correct-or-quiet": tracing can never break
the engine path.

**Status: CODE-READ.** The mapping is real and the trace is chained. What the
run journal showed for the failed gate is the point below.

---

## Layer 6 — Receipts and enforcement

**Starts at:** `gt_harness/runtime_receipts.py::issue_runtime_receipts`.

**The enforcement that matters** (`runtime_receipts.py:864`):

```python
if indexed_files > 0 and not utilisation.get("graph_backed_delivery"):
    errors.append("treatment_graph_evidence_absent")
```

Keyed on indexed **files**, not nodes — a broken index that produced zero nodes
must not be excused as "empty repo".

`GRAPH_BACKED_FEATURES` = `caller_contract`, `cochange_prior`, `def_partition`,
`signature_delta`. Localisation is deliberately **excluded**: `trace_frame` maps
to it and is runtime-derived, so counting it would let a graphless run claim
graph backing.

**Status**

| Item | Mark | Evidence |
|---|---|---|
| Enforcement present and correctly keyed | **VERIFIED (by reading + the incident)** | Run 33708231670's journal carried only `new_file_destination`, `context_delta`, `trace_frame`, `missing_role_postcreate:*`, `context_contract` — **zero** graph-backed evidence. GT-on ran without its graph and nothing stopped it. That hole is what this rule closes. |
| Rule has fired in anger | **UNPROVEN** | No paid run has reached it since |

---

## Layer 7 — Grading and attestation

Official verifier result → `miniswe_report.json` → attestation artifact.
Diagnostics and receipts are keyed by the **canonical task id**
(`resolve_run_task_identity`), not `sha256(task_text)[:16]` — the mismatch that
tripped both arms of attestation on run 33708231670.

**Status: VERIFIED as a diagnosis** (root-caused from the artifacts),
**UNPROVEN as a fix** (no paid run has completed since).

---

## Honest whole-system summary

| Layer | Working? |
|---|---|
| 0 Dispatch admission | Working as designed; **red** pending a real review packet |
| 1 Task materialisation | Working |
| 2 Producer | Working on 18/19; **boa broken** |
| 3 Index lifecycle | Working; LSP + ONNX **unproven in a run** |
| 4 Agent loop | Unproven end to end |
| 5 Attribution | Unproven end to end |
| 6 Receipts/enforcement | Correct by construction; never fired in a real run |
| 7 Grading | Diagnosed, unproven |

**The system has never been observed working end to end with a graph.** Every
paid run so far either had no graph (silently) or was stopped before spending
money. Layers 0–3 are now genuinely evidenced. Layers 4–7 are exactly what a
successful smoke would prove, and nothing short of one will.

### Open, ordered

1. Review packet for `cffca1fd2` on `gt-review-inbox` — blocks dispatch.
2. boa's non-terminating publication — blocks the remaining 19, not the gate.
3. `MaxDepth` 3 → 6 — requested, not done, needs the same CI rebuild.
4. LSP promotion and ONNX retrieval — wired but unmeasured.
