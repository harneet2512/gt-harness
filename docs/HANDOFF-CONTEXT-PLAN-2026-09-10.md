# GT context + plan implementation handoff — 2026-09-10

## Release state and objective

**NOT BENCHMARK READY.** Continue the existing implementation, not a redesign.
The objective is better correctness and lower harness overhead without removing
capabilities, weakening evidence gates, or repeatedly rebuilding graphs.
Deterministic context/plan delivery is being verified; it is not proof of
universal task success or 4/4 repeated success. No measured six-repository
speedup has yet been established.

The authoritative checklist is `docs/plans/context-plan-implementation-status.md`:
51 checked and 17 open items at handoff. This is an item count, not an effort
percentage. Its historical evidence paragraphs are cumulative; use exact commit
and artifact identities, not the word “passed” from an older paragraph.

### Update — 2026-09-10, later the same day, at `fad29bcf`

62 checked and 6 open. The item count moved by eleven, and one of those moves
was BACKWARDS: all-consumer graph parity was ticked on fixture-scale evidence
and then reopened, because the first real repository it was tried on disproved
the claim. Read the next paragraph before anything else in this document.

**THE PRODUCER IS NOT DETERMINISTIC, AND THAT IS NOW THE DOMINANT BLOCKER.**
Three full rebuilds of a byte-identical `click` checkout (105 parsed files)
produce two different edge sets — 50,954 and 50,957 edges, different content —
and the batch amend wobbles between exactly the same two. `-workers 1` is
equally nondeterministic and lands on the identical pair of digests, so this is
not resolver concurrency. What moves is receiver selection for same-named
methods: `AliasedGroup.get_command` calls `Context.fail` in one build and
`ParamType.fail` in another; `_AtomicFile.close` calls `Context.close` or
`LazyFile.close`; `runner.invoke` binds to `Context.invoke` or
`CliRunner.invoke` across five assertions; 359 of 43,680 `vta_flow_edge_fact`
nodes differ at an identical row count.

Three consequences, in order of how much they cost:

1. It sets a CEILING on every parity claim in this project. No comparison
   between two graphs can be tighter than the producer's agreement with itself,
   so amend-versus-rebuild parity is not establishable at real scale by digest
   equality until the tie-break is deterministic. This is producer work and
   needs re-certification.
2. A consumer asking who calls `fail` gets a different answer depending on
   which build of the same code it reads. That is a correctness property of
   every graph-backed answer, not only of the amend.
3. It explains why the fixture-scale suites pass and why they were not
   sufficient evidence. A six-file synthetic tree has no same-named methods for
   the tie-break to act on. An eight-file fixture built specifically to have
   that SHAPE is still deterministic — measured, not assumed. The defect needs
   repository scale.

Evidence and witnesses: `determinism-click.txt`, `arm-diff-click.txt`,
`study-click.json` under `D:/gt-context-proof`, and
`tests/test_producer_determinism.py`, which pairs a passing fixture guard with a
real-repository test that XFAILs today and turns into an XPASS when the producer
is repaired. A static scan of the vendored producer found 38 range-then-break
sites, nearly all ranging over slices already sorted after map extraction; one
is not (`internal/resolver/promote.go:567` over `map[fnlKey]int64`). That site
is a confirmed member of the same class and is NOT attributed to the measured
symptom, which lies in CALLS targets and VTA facts.

**TWO PRODUCER-PINNING FINDINGS, one closed and one blocking.**

CLOSED. `tests/test_gt_engine.py` stripped every `GT_*` variable before each
test, including `GT_INDEX_BINARY` -- which is the producer executable's
LOCATION, not GT behaviour. The canonical workflow installs the vendored
producer at `/opt/groundtruth/gt-index/gt-index`, checks its sha256 against the
pin, and passes it to the suite ONLY through that variable; that directory is
not on PATH. So `find_binary` fell past the override to `ensure_binary()`, which
on a networked runner DOWNLOADS gt-index v1.1.0. That file's graph tests ran
against a downloaded producer while the workflow certified one they never used.
Offline the same path merely fails, which is the only reason it surfaced.
Fixed at `54d9ea5a`, guarded at `fd038e6c` by a test that compares the resolved
producer's BYTES against the vendored artifact and names a resolution that
reached the download cache. Canonical CI **34473945205** passed at `fd038e6c`
with that guard active, which is the proof it now resolves the pinned binary.
Side effect worth knowing: the installed suite went from 89 skips to 15. About
seventy-four graph tests had been skipping themselves because the fixture ate
the binary path, and a skip is not a failure, which is why it survived every
verification run.

BLOCKING. The certified binary's declared source cannot be reproduced:

    build-info git_commit          efa70e52...   built 2026-09-10T04:16:51Z
    vendor/.../SOURCE-COMMIT       193b9d93...   a different commit
    build-info source_fingerprint  f7fca174...
    vendored tree, producer recipe 4f612d4c...
    git archive efa70e52 gt-index  367a641a...

Measured with the producer's own recipe
(`scripts/swebench/build_gt_index_linux.sh:73`) run from `$REPO_DIR/gt-index`.
The producer worktree sits clean at `efa70e52` and neither its working copy nor
a `git archive` of that commit reproduces `f7fca174`. WHY is not established and
is not guessed at. Both comparisons are exactly what the PAID smoke20 workflow
performs, and they live only there -- the free canonical workflow does not check
the binding at all -- so a paid dispatch would fail its producer-source gate
before doing any work. `tests/test_vendored_producer_binding.py` carries both as
xfail witnesses with the measured digests. Nothing was repinned: repinning a
certified binary on an unestablished cause is what that item exists to prevent.

**Six-repository performance study: harness built, one repository measured.**
`scripts/graph_transition_study.py` (tested by
`tests/test_graph_transition_study.py`, 18 cases).

READ THE CORPUS BEFORE THE NUMBERS. The first corpus used (`D:/test-repos`:
click, terraform, cpython, sentry, grafana, kubernetes) is far larger than
DeepSWE's actual task repositories, which are library-scale across five
languages. Its rows measure the producer's SCALING, not the benchmark's
workload, and are retained as `study-large-repos.json`: terraform (5,184 files),
cpython (5,639) and sentry (20,094) all EXCEEDED a 900s build budget, peaking at
1.6-2.1 GB when killed, while the harness's own budget is 600s.

THE BENCHMARK-SCALE RESULT is different and is the one that answers the item.
Over locally staged SWE-bench checkouts, five alternating repetitions each,
parse cache enabled in both arms (`study-benchmark-scale.json`):

    repository      parsed  parent   baseline  candidate   x     peak MB
    kedro-4580        328    6.3s      5.35s     5.23s    1.02  188 -> 198
    keras-20396       694  126.4s    118.42s   119.06s    0.99  1769 -> 1722
    dynaconf-1238     609    4.2s      3.25s     3.30s    0.98  164 -> 166

All build well inside 600s, the slowest at 126s, consistent with this
codebase's own ~115s for arktype. The amend is BREAK-EVEN, 0.98x to 1.02x,
three times over, with neutral memory. It is not failing structurally -- on
keras it retains 10,055 of 10,698 parser nodes -- it is that both arms still run
one full resolver pass and resolution is what costs.

Semantic parity is TRUE on kedro, keras, dynaconf and haystack -- one digest
across every run of both arms -- and FALSE on conan-io__conan-17132, which gives
three distinct edge digests across five builds of a byte-identical tree (34,457,
34,459 and 34,460 edges). So the producer nondeterminism is repository-dependent
but NOT confined to oversized repositories: it reaches the benchmark's own size
and style class, roughly one repository in five here. The candidate arm wobbles
across the same three states rather than a different set, so the amend inherits
the defect rather than adding to it.

An earlier version of this study left GT_PARSE_CACHE_ROOT unset in both arms, a
real instrument defect that was found by reading the amend result line it was
already recording, fixed, and did not change any verdict. On `click` alone the
amend measured 0.76x and +32% memory; that figure does NOT generalise and
should not be quoted for the benchmark.

conan, haystack and matplotlib remain. That is the smallest of the six and
the amend's advantage should grow with size, but "should" is what the study
exists to replace. The remaining five are the point of the exercise. The
harness does NOT measure the blocked-versus-background split, checks or
snapshots; those belong to the coordinator and the run loop and need their own
measurement against the live path.

**Persistent full-suite failures are environmental, verified at a clean HEAD**
with no uncommitted work: two L6 wake tests resolve the producer by a route the
`gt-linux-suite:3.12` image cannot reach, and two product-acceptance tests
report `source_closure_differs_from_head`. Latest full installed run: 2,198
tests, 4 failed, 89 skipped (`full-suite-76.xml`).

## Worktrees, branches, and authority

| Purpose | Worktree | Branch | Implementation HEAD at handoff |
| --- | --- | --- | --- |
| Harness | `D:/gt-context-plan` | `codex/context-plan-integrity` | `7e9911baf9bcd6f0771c2365b6d5e5c6edc56f67` |
| Producer | `D:/gt-context-producer` | `codex/context-plan-producer` | `efa70e52d4e85bff2364b0038ceca2306de218b0` |
| Review evidence | `D:/gt-context-review` | `codex/context-plan-review` | `86293f7122c9c4975ddf9280b8fffab7b4737717` |

Harness GitHub repository: `harneet2512/gt-harness`.
Producer push remote: `upstream`, repository `harneet2512/groundtruth`.
**Do not print producer origin or `git remote -v`: that origin contains a token.**

Approved harness base: `ce309e90613de3aad6ccfa9a8251b009cdef5bea`.
Approved producer base: `193b9d93b0650721be6120ae519ab3a1d8e8137f`.
Both ancestry checks passed earlier; repeat before delivering a successor.

**Do not edit `D:/gt-harness`: it is an unrelated dirty worktree.** It owns the
shared Git administration/hooks but is not the implementation working directory.
Producer and review worktrees were clean when this handoff was prepared.

Use `apply_patch`, preserve unrelated changes, and read the engineering-loop
skill before implementation. No subagents are authorized. No paid provider or
benchmark dispatch is authorized. No GCP actions, auth changes, credential
cleanup, secret exposure, or provider routing changes are needed.

The harness pre-commit hook performs a read-only authority check, **not pytest**.
Its post-commit hook auto-pushes this branch. Do not assume committing ran tests.

## Immediate state: resume here

### Update — 2026-09-10, evening, at `3ec3981b`. READ THIS FIRST.

Tracker: **63 checked, 5 open**. NOT benchmark ready, and do not claim it: parity,
the study's run-loop half, and the release artifact binding are all open.

**Closed since the last update:** the canonical acceptance + installed full-flow
rehearsal item. Both halves are green at the SAME commit `48ca7ee2` — canonical CI
run **34505286172**, rehearsal run **34505263336**, `VERIFIED_SYNTHETIC_REPAIR`,
`runtime_receipt_errors == ["synthetic_transport_not_paid_evidence"]` exactly, and
the expected-error list was never edited. Three consecutive rehearsal passes.

#### THE BIG ONE: the producer determinism defect has a root cause and a verified fix

This blocked all-consumer parity and capped every graph comparison in the project.
It is a single site.

`vendor/gt-index-src/internal/resolver/resolver.go:1546`:

    for id, m := range nodeMeta[0] {                  // map[int64]NodeMeta
        if m.ParentID != 0 && (m.Label == "Method" || m.Label == "Function") {
            ...
            methodsByClass[m.ParentID][m.Name] = id   // LAST WRITE WINS
        }
    }

`nodeMeta[0]` is a Go map, so the loop order is randomized. A class can hold TWO
members under one name (`@overload`, a conditional redefinition, a decorator
pair), and last-write-wins makes WHICH definition the index keeps run-dependent.
Every `impl_method` resolution reads that index.

**Fix (eight lines):** keep the lowest `(start_line, id)` instead of the last
writer. Patched source: `D:/gt-context-proof/gt-index-src-rootfix/`, single file
also at `D:/gt-context-proof/resolver-rootfix.go`. Applied as the ONLY change
against the vendored tree, built with the declared tags
(`netgo,osusergo,sqlite_fts5`, go1.24):

    click   105 files    8 runs, 8 distinct digests  ->  1 digest   DETERMINISTIC
    conan   997 files    5 runs, 5 distinct digests  ->  1 digest   DETERMINISTIC
    kedro   328 files    unchanged at 5,982 edges (no same-name collision)

conan is the one that matters: benchmark-scale, and its wobble was the evidence
that the defect reaches the benchmark's own size class.

**NOT SHIPPED.** This is producer source behind a certified binary. Landing it
needs a producer rebuild and re-certification — the same gate the release artifact
binding item is blocked on, so those two items are now ONE action.

**How to reproduce and re-verify without the certified binary** (this is the part
that took longest to find, so do not rediscover it). Build the producer from the
vendored source in `golang:1.24` with `PATH=/usr/local/go/bin:$PATH` (the image
does not put `go` on PATH for `bash -lc`), copying `/src` to a writable dir first,
then:

    python /proof/determinism_ab.py <repo> <runs> <binary>...   # digests per binary
    python /proof/edge_diff.py      <repo> <binary> <runs>      # which edges move
    python /proof/idcheck.py        <repo> <binary> <runs>      # are node ids stable
    python /proof/method_diff.py    <repo> <binary> 2           # flips by resolution_method

Full write-up with the ruled-out hypotheses:
`D:/gt-context-proof/determinism-root-cause.md`.

**Three hypotheses that are DEAD — do not spend time on them again:**

* NOT concurrency. `-workers 1` still differs run to run.
* NOT node-id assignment. At `-workers 1` all 1,086 click definitions keep
  byte-identical ids across runs (0 of 1,086 differ).
* NOT the id tie-breaks. `pickBestNameMatchTarget` (~954), `pickBestLocalTarget`
  (~902) and `pickBestImportCandidate` (~866) all end in a node-id comparison and
  all three were rewritten to `(file, start_line, id)` and rebuilt — still
  nondeterministic, because the wrong entry is in the index before any picker runs.

Also corrected: `promote.go:567` is a real order-dependent site of the same class
but is NOT this defect — it emits CO_SERIALIZES, which the core layer never
produces.

The local build reports `analysis_state=failed` and is core-only (3,302 edges
against the certified binary's 50,954). That gap is the missing resolution-evidence
layer, NOT source divergence: the 13 core edge types match count for count.

#### The rehearsal now runs on a hosted runner. Stop using Docker Desktop for it.

`.github/workflows/installed_rehearsal.yml`, free (synthetic transport, no
provider), triggered on push to this branch and by dispatch. Docker Desktop serves
the Windows drive over 9p and TWO consecutive local runs deadlocked in
`p9_client_rpc` (state D, uninterruptible) right after
`mkdir -p /installed-agent/bundle`. Staging onto a VM-local docker volume cleared
that and then failed on a dense-model directory Docker had auto-created empty.

Three constraints it took three failed runs to find, in the order you will meet
them:

1. `inputs.scenario` is the empty string on a push trigger, not the choice default.
2. `_docker_compose_paths` is a **property** on Pier's DockerEnvironment. Overriding
   it as a method kills every trial in setup with
   `TypeError: 'method' object is not iterable`.
3. Pier generates the squid config itself with `acl Safe_ports port 80 443` and
   `http_access deny !Safe_ports`. The transport is reachable on 80 or 443 and
   NOWHERE else, whatever the allowlist says about the hostname. Port 8080 returns
   a squid error page with zero requests reaching the server. The workflow lowers
   `net.ipv4.ip_unprivileged_port_start` and serves on 80. Pinned by
   `tests/test_rehearsal_transport_port.py`, which parses the ACL out of Pier's own
   `squid_bootstrap_command()`.

`eval/pier_filtered_docker.py` adds one compose override giving the PROXY container
`host.docker.internal:host-gateway` — the agent is on an `internal` network and
never reaches the host directly, so the name must resolve in the proxy.

#### Open threads, mid-flight

* **`study-matplotlib-x10.json`** was running at handoff (10+10 repetitions,
  ~1.8h). It decides whether the "matplotlib's amend and rebuild produce DISJOINT
  edge sets" finding survives. That finding rests on 5+5 samples; if the baseline's
  true range is wider than five draws showed, it is sampling and must be RETRACTED
  from the tracker, not softened. Note the determinism fix above may make the whole
  question moot — re-run the study with a fixed producer before trusting either way.
* **LSP binding preservation** (the open half of the history/cochange item) is
  part-traced. Established: `certify_lsp_candidate` refuses a base whose manifest
  carries `derivation`, so enrichment is strictly ONE level; the amend accepts a
  derived parent as certifiable; and the child manifest built at `indexer.py:1347`
  is assembled fresh and carries NO `derivation` key. The unfinished question is
  whether a promoted graph actually becomes the parent of the next amend — if it
  does, the one-level invariant is silently defeated, because the child no longer
  declares the derivation and a second promotion would not trip
  `lsp_nested_derivation_forbidden`. Finish that before writing anything down.
* **The close-time coordinator drain** (`5f4d69c6`) is correct against rehearsal
  06's shape by unit witness and has NOT fired in any live run. The passing
  rehearsal took rehearsal 04's shape — publications at journal rows 3 and 152, the
  ordinary snapshot path — so it is unexercised, not validated.

#### What is left, split by whether it is building or verifying

| Item | Build or verify |
|---|---|
| All-consumer parity at real scale | VERIFY. The harness is built (8 mutations x 8 consumers, zero skips). Blocked only by the producer defect above. |
| Release artifact binding | BUILD, in the producer. Same re-certification gate as the determinism fix. |
| Six-repository study, run-loop half | BUILD. Blocked-vs-background time, checks and snapshots are coordinator/run-loop properties; `graph_transition_study.py` measures the PRODUCER and says so. That instrument does not exist. This is the largest genuine build item left. |
| History/cochange reuse | BUILD, in the producer. History, cochange, derived layers and FTS all recompute every build. |
| LSP binding preservation | UNKNOWN until the trace above finishes. |
| Final review | VERIFY. Last by construction; needs the source to freeze. |

### Superseded — the numbered list below is the state at handoff, not now

Items 1–3 are CLOSED. CI has been green twice since, most recently
**34465862429** at source `13c4e3b5`. Interruption rehearsal 06's expected-error
accounting was fixed and committed at `eb9965ed`; the two uncommitted
source-binding changes named in item 3 were committed at `c0455f36` and no
longer exist as uncommitted work — do not go looking for them. Item 4 stands.

Resume instead at, in this order:

1. **The producer determinism defect described above.** It blocks all-consumer
   parity and caps every graph comparison in the project. Everything else in
   this list is smaller.
2. **Finish the six-repository study.** One of six measured. Launch:
   `scripts/graph_transition_study.py --repos-root <corpus> --workspace <tmp>
   --binary <gt-index> --out <report.json> --repetitions 5 <repos...>` inside
   `gt-linux-suite:3.12`. The corpus used is `D:/test-repos` (click, terraform,
   cpython, sentry, grafana, kubernetes). It writes its report after EVERY
   repository, so a slow one cannot lose the ones already done. Expect hours,
   and do not run anything else CPU-heavy alongside it or the timings are junk.
3. **The installed full-flow rehearsal** half of the canonical acceptance item.
4. **Release artifact binding**, which needs the source to stop changing, and
   the final review. Neither can start while 1 and 2 are moving.

### Original handoff list, retained for its detail

1. Check provider-free CI **34443015691**, source **7e9911ba**. It was in progress
   at handoff preparation. Prior CI **34442196007**, source **f5b9c94f**, PASSED.
2. Fix real forced-interruption rehearsal **06**, now **FAILED** solely on
   `unexpected_runtime_receipt_errors`. Retry/no-submit, exact collected patch,
   reward and process cleanup checks no longer report failures. Do not merely
   replace the expected error list: trace failure-receipt accounting and worker
   finalization ordering. The actual errors are recorded below.
   Output: `D:/gt-context-proof/rehearsal-interruption-06/rehearsal.json`.
   Log: `D:/gt-context-proof/rehearsal-interruption-06-runner.log`.
   Exact source: 7e9911ba; installed harness wheel 59; actual producer 06.
3. There are **two uncommitted source-binding changes** owned by this session:
   `gt_engine/persistent_plan/baseline.py` and
   `tests/test_persistent_plan_baseline.py`. Do not discard or silently commit.
   Details and verification are below.
4. Finish those checks and inspect the full diff. Then commit a coherent verified
   increment, update the tracker, build the next exact artifact and rerun any
   invalidated release/rehearsal checks. Do not mark all remaining items done.

No background agent will continue after this session ends. Docker/CI processes
may continue, so query their actual state rather than relying on this snapshot.

## What is implemented

### Evidence, plans, and verification

- Lexical passing-command matches no longer grant arbitrary requirement proof.
  Bound checks distinguish `CHECK_PASSED` from semantic proof; missing mappings
  remain visible and cannot silently imply completion.
- Exact semantic assertions reject contradictory pass/fail or pass/unknown
  markers. Check admission binds command, test identities, source/configuration,
  environment, output and complete execution evidence.
- CheckSpec uses admissible explicit argv, not replayed shell commands. Shared
  checks are deduplicated; equivalent agent executions discharge queued work.
  Verification drains at bounded VERIFY/submission boundaries, not every edit.
- Requirement/source/example conservation, stable row IDs, full command
  rendering, explicit pending designs and interactions, and `gt-plan`
  show/revise/defer/bind-check requests are implemented.
- Original plan/context/baseline checkpoints and check definitions recover from
  validated persisted state. Historical passing evidence does not regain current
  proof authority. A corrupt original checkpoint cannot trigger a post-edit
  baseline recapture disguised as the original baseline.
- Initial baseline capture now occurs before the one initial index build, so
  baseline-written source cannot leave the initial graph silently stale.
- Baseline output is parsed completely, source mutations are not destructively
  restored, disappearing test IDs remain incomplete, and environment mismatches
  remain unknown. Baseline subprocesses use the existing descendant containment.
- Commit **24d259b5** additionally rejects missing/false output completeness
  receipts. This adds no test execution or graph build.
- The 300-step workflow limit is removed (`max_iterations=0`); wall-time and
  submission reserve remain enforced. Installed 301-query/deadline proof exists.
- Gate reasons distinguish completion evidence, mapping gaps, failed checks,
  baseline uncertainty and budget/stall escape. Acceptance is not correctness.
- Current committed/uncommitted patch state and bounded finalization reminders
  are implemented. The production harness does not auto-commit agent changes.

### Graph amendments

- Producer efa70e52 implements complete parser-result caching and one staged
  batch amendment over a certified immutable parent.
- Unchanged parser nodes, exact parser properties, target-bound assertions,
  containment and taxonomy edges are retained. Changed/deleted structures are
  replaced; parser-local cached references are remapped safely.
- The existing complete resolver and derived analysis run **once per batch**.
  Do not introduce an unproven narrow resolver or legacy per-file fallback.
- The harness uses the existing one-active/one-pending coordinator and external
  task cache. Capability is conservatively named `batch_parser_node_reuse_v1`.
- Source-suite transition tests cover unchanged/edit/add/delete/rename/import/
  inheritance/ambiguity/new target. Installed binary proof verifies retained IDs,
  immutable parent bytes, clean foreign keys and structural parity on its fixture.
- History/cochange, derived layers and FTS still recompute. Full all-consumer
  equivalence and the fixed-workload performance comparison are **not complete**.

### Audit and actual installed integration

- Persistent-plan rendering and plan-gate directives have CAS-backed exact-byte
  provider exposure audits, supporting monolithic and message-CAS requests.
  Immediate response identity is checked. Exposure is not semantic use/proof.
- Missing/tampered/late/wrong-size renderings and mismatched responses cannot
  become witnessed. Gate receipt faults preserve the directive and record the
  declared degradation stage.
- Lineage manifest self-seals are recomputed and validated. Missing or mismatched
  seals fail; a prior provenance verifier incorrectly omitted this check.
- **f5b9c94f** repairs the synthetic fixture: bootstrap calls do not consume
  action ordinals; its plan and execution share a source-bound test-file command;
  the canned agent commits its repair; task-owned `verifier.collect` writes
  `git diff --binary gt-rehearsal-base HEAD` to `artifacts/model.patch`.
  The verifier does not substitute `agent/gt-worktree.patch`.
- **21d0836b** fixes audit call reconciliation: agent turns plus separately
  reported catalog/planning calls must equal provider requests. Malformed,
  negative and boolean counters remain errors, as do arithmetic mismatches.
- **7e9911ba** fixes interruption transport retries: ordinals count returned
  actions, not HTTP requests. Retried calls remain parked at the blocked action.
  Interruption request census excludes bootstraps and permits unanswered retries;
  six-action, no-submit, terminal, receipt and process cleanup checks remain.

## Current uncommitted baseline source-binding increment

RED witness: `D:/gt-context-proof/baseline-source-red.xml` shows a test suite
mutating `data.txt` was still labeled a captured baseline.

The working change adds `source_revision` and `after_source_revision` to
BaselineResult and serialization. It uses the existing workspace snapshotter
before/after the baseline command, rejects incomplete snapshots, subtracts the
initial snapshot time from the execution allowance, and labels differing
revisions `source_changed_during_baseline`. Mutated files remain untouched.
This performs no graph rebuild and no extra test command, but adds two source
captures per baseline run. Measure their cost before claiming speed neutrality.

Wheel 60 was built successfully:
`D:/gt-context-proof/wheels-60/nano_harness-0.0.1-py3-none-any.whl`
SHA256: `287d47b984dc9711740d46f08b3083c8b96dbc125bbab8402556173e02d224f9`.

The three-suite installed test **FAILED: 14 failed, 22 passed**. Result path:
`D:/gt-context-proof/baseline-source-60.xml`.
Failures generally return `spawn_failed`, detail `ValidationError`, before
normal baseline execution. **This candidate must not be committed as working.**
First reproduce the underlying exception without the advisory catch. A concrete
lead (not yet confirmed): subtracting snapshot elapsed time changes an integer
timeout into a fractional float; the installed environment's Pydantic timeout
field may require an integer. Fix without exceeding the remaining budget, then
rerun all 36 tests and real source-mutation behavior. Ruff passed on both files.
Review compatibility carefully: the original checkpoint decoder requires exact
dataclass fields. Older checkpoints lacking these new fields will conservatively
reject; decide whether an explicit versioned, non-authoritative migration is
needed. Never backfill historical source identity with the current workspace.
Installed dependency/config/test identity conservation remains incomplete.

## Evidence index and limits

All listed local evidence is under `D:/gt-context-proof`; it is not necessarily
available to a remote session. Do not claim remote availability unless uploaded.

| Evidence | Observed result | Scope |
| --- | --- | --- |
| `producer-repin-44.xml` | 200 passed, one explicit source-only deselection, no skips | Installed graph/recovery/baseline/check tests |
| `structural-binary-06.xml` | 1 passed | Actual static CLI structural amendment fixture |
| `plan-exposure-installed-49.xml` | 153 passed, no skips | Native provider exposure and negative bindings |
| `gate-exposure-installed-50.xml` | 179 passed, no skips | Gate exposure and related boundaries |
| `rehearsal-collector-56.xml` | 63 passed + four subtests, no skips | Fixture collector/check-source binding |
| `plan-off-guard-56.xml` | 17 passed | Positive mandatory AND guard; OR/negation rejected |
| `bootstrap-audit-57.xml` | 97 passed + four subtests | Audit accounting + rehearsal tests |
| `baseline-capture-58.xml` | 36 passed | Baseline capture completeness and recovery regressions |
| `rehearsal-retry-59.xml` | 98 passed + four subtests | Retry regression + full native audit tests |
| Rehearsal 04 | Reward 1, exact official patch, bound evidence and graph refresh true | Actual installed synthetic repair |
| `rehearsal04-audit57.json` | GREEN-delivered, zero integrity issues | Re-audit of actual preserved rehearsal 04 journal |
| Rehearsal 05 | FAILED; patch gradable, process tree cleaned | Retry advanced to submit; fixed in 7e9911ba, successor required |

Rehearsal 04's original receipt remains FAILED because its original audit had
the bootstrap accounting bug. Do not rewrite it. The separate newer audit is
the evidence that the preserved journal passes the corrected auditor.
Rehearsal 06 FAILED only `unexpected_runtime_receipt_errors`. It recorded ten
transport requests, an exact gradable collected patch, four checked process
identities, no surviving descendants and no new processes. Actual runtime errors:

```text
synthetic_transport_not_paid_evidence
product_not_completed
product_provider_call_conservation_failed
product_effective_model_report_mismatch
product_event_journal_digest_mismatch
product_event_journal_conservation_failed
provider_response_count_exceeds_attempts
treatment_receipt_missing
```

Inspect `scripts/miniswe_supervisor.py`, `gt_harness/runtime_receipts.py`, final
worker/report/journal writes and the interruption oracle. Determine which errors
are truthful expected incomplete evidence and which reveal a race or incorrect
counter. Do not whitelist errors merely to turn this rehearsal green.

Canonical CI:

- 34442196007 at f5b9c94f: **PASSED**.
- 34443015691 at 7e9911ba: **in progress at preparation; query now**.
- 34441061328 at b2a53930: failed static guard test; fixed in f5b9c94f.
- 34440017657 at 509a2a20: failed new degradation-stage registry; fixed in b2a53930.
- 34439493122 at 0d1806bc and 34438826174 at 440c337b: PASSED historical checkpoints.

Canonical CI has known environment-specific skips (real retrieval graph fixtures,
sqlite_vec, and complementary graph-available/unavailable scenarios). Inventory
and exercise required complements; do not call the whole CI zero-skip proof.

## Remaining work and how to close it

These are the tracker’s 17 open items, with concrete completion criteria.

1. **Protocol coverage:** enumerate supported parsers from the actual producer;
   run positive and skip/no-test/contradiction/timeout/late-failure negatives.
   Unsupported output must abstain, never infer proof from a passing command.
2. **No unbound advancement:** exercise those observations through the real
   adapter, cursor and submit gate, not just a parser helper. Assert row IDs and
   proof/check states before and after each observation.
3. **Restart/interruption integrity:** finish rehearsal 06, then test valid,
   truncated, tampered and externally anchored journal tails. Restore definitions
   and original context without restoring stale passing authority or rerunning
   the planning call. Reject replayed already-admitted requests.
4. **Anchor/source validation:** inspect `persistent_plan/anchors.py`, bootstrap
   validation and rendering. Require anchors to belong to their recorded graph
   and source revision. Distinguish proposed symbols/tests from existing ones;
   historical anchors must be labeled historical, not silently current.
5. **Counts/exposure:** expose exact row IDs with separate existing/proposed,
   executed, passed, proven, unmapped and model-exposed counts. Keep compatibility
   aliases explicitly labeled. Test that retrieval availability is not exposure.
6. **Full CLI matrix:** extend `test_plan_check_specs.py`,
   `test_plan_check_recovery.py`, `test_original_plan_recovery.py` and native cursor
   tests for stale/multiple/replayed requests, invalid specs and deferral. Run
   actual installed `gt-plan` requests through their journal owner.
7. **Eligible history reuse:** inspect producer cochange/history owners. A HEAD-only
   key is unsafe (shallow state, replace refs, grafts, configuration/environment
   can change history). Reuse only with a sufficient verified identity; preserve
   embedding cache and LSP ownership. Do not shortcut complete derived analysis.
8. **All-consumer graph parity:** expand the existing transition corpus to every
   consumer (resolution, calls, assertions, properties, covering tests, retrieval,
   LSP, derived layers). Compare amended and fresh graphs on identical snapshots,
   parent immutability and failure fallback; assert one resolver pass per batch.
9. **Baseline identities:** finish the current source-binding increment; bind
   test/configuration/environment/dependency identities and preserve skipped and
   missing-test distinctions. A changed test definition must not masquerade as
   the original passing test; unknown dependency identity remains explicit.
10. **Integrated mutation lifecycle:** installed Linux sequences combining
    background writers, typed edits, auto-checks, incomplete captures and carried
    snapshots. Verify descendant cleanup and epoch invalidation together.
11. **Official versus recovery patches:** record both artifact identities
    separately; test committed-only, uncommitted-only, mixed and interrupted
    states. Only the task-owned collected patch is graded. Actual rehearsal now
    exercises that collector, but the complete separation matrix is still open.
12. **21-capability census:** rerun `issue_feature_matrix.py` /
    `verify_feature_matrix.py` using their actual CLI help/workflow. Audit positive
    and negative bindings for every current feature; preserve historical 19-feature
    evidence. A synthetic feature witness does not prove causal task benefit.
13. **Exact release artifact binding:** after source stabilizes, build the final
    harness wheel, verify producer/review lineage and source-to-wheel matching,
    and bind only real hashes. Rebuild after material code edits.
14. **Required installed tests:** run from installed wheels with the actual static
    producer, then audit real journals. Provide source/Git-only complementary
    tests explicitly; resolve all unexplained skips.
15. **Release/full flow:** canonical provider-free CI and normal repair plus forced
    interruption on the final artifact must pass. Synthetic transport remains
    `paid_smoke_eligible=false`; never suppress that exclusion.
16. **Performance:** stage the fixed six repository transitions and run five
    alternating baseline/candidate repetitions with equal budgets/environment.
    Record blocked/background graph time, parsed files, resolver passes, checks,
    snapshot time, peak memory and semantic parity. Do not substitute toy-fixture
    timings or extrapolate median-turn savings as measured end-to-end speedup.
17. **Final review/handoff:** inspect all changed consumers and serialized formats,
    rerun invalidated checks, commit/push scoped changes, confirm clean intended
    worktrees and exact-SHA passing CI. Only then update readiness. Paid benchmark
    efficacy still requires separate authority and actual task outcomes.

## How to verify and run the actual artifacts

Use PowerShell from `D:/gt-context-plan`. Do not use the shared Windows venv for
new harness pytest: its installed GT is old and the provenance guard rejects it.
Do not bypass that guard or upgrade the shared venv. It is usable for wheel
building and Ruff only.

```powershell
git status --short
git branch --show-current
git merge-base --is-ancestor ce309e90613de3aad6ccfa9a8251b009cdef5bea HEAD
gh run view 34443015691 --repo harneet2512/gt-harness --json status,conclusion,headSha
gh run view 34443015691 --repo harneet2512/gt-harness --log-failed |
  Select-String -Pattern 'FAILED |AssertionError|E   ' | Select-Object -Last 35
```

Installed Linux check example (wheel 60 is the uncommitted baseline candidate):

```powershell
docker run --rm --network none `
  --mount type=bind,source=D:/gt-context-proof/wheels-60,target=/wheels,readonly `
  --mount type=bind,source=D:/gt-context-proof/producer-wheels-06,target=/producer,readonly `
  --mount type=bind,source=D:/gt-context-plan/tests,target=/tests,readonly `
  --mount type=bind,source=D:/gt-context-proof,target=/proof `
  gt-linux-suite:3.12 sh -c 'python -m pip install --no-deps /producer/groundtruth_mcp-1.0.0-py3-none-any.whl /wheels/nano_harness-0.0.1-py3-none-any.whl && cd /tmp && python -m pytest /tests/test_persistent_plan_baseline.py /tests/test_gt_baseline_preservation.py /tests/test_original_plan_recovery.py -q -p no:cacheprovider --tb=short --junitxml=/proof/baseline-source-60.xml'
```

For graph/native tests additionally mount
`D:/gt-context-proof/producer-build-06/gt-index-linux-amd64` read-only at `/binary`
and set `GT_INDEX_BINARY=/binary/gt-index-linux`.
Source/Git AST or release-issuer tests need a separate real Linux checkout,
not `site-packages` as a pretend repository root.

Real journal audit (correct CLI flag is `--json`, not `--json-out`):

```text
python -m scripts.gt_audit /proof/rehearsal-repair-04/trials --json /proof/rehearsal04-audit57.json
```

Run this inside an installed Linux environment with `/proof` mounted. Keep old
receipts intact and write successor audits to distinct names.

Provider-free CI dispatch:

```powershell
gh workflow run "GT Harness: canonical provider-free product acceptance" --repo harneet2512/gt-harness --ref codex/context-plan-integrity
```

### Installed full-flow launcher

Existing local helper: `D:/gt-context-proof/run_rehearsal_01.py`.
Arguments: `--name`, `--wheel`, `--source`,
`--scenario repair|forced-interruption`. It invokes the repository's real
`scripts.gt_installed_rehearsal` through installed Harbor/Pier and the real
supervisor, indexer, model files and network-filtered task container. The only
model endpoint is the local synthetic transport; no paid call is made.

Archive the **exact committed source** into a new proof directory before each
new attempt. Match it to the actual wheel, and choose unused output/container
names. Example shape, replacing SOURCE_DIR, SHA and NAME before execution:

```powershell
docker run --rm --name NAME --pid=host --cgroupns=host -p 80:80 `
  --mount type=bind,source=/sys/fs/cgroup,target=/sys/fs/cgroup,readonly `
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock `
  --mount type=bind,source=D:/gt-context-proof,target=/run/desktop/mnt/host/d/gt-context-proof `
  --mount type=bind,source=SOURCE_DIR,target=/repo,readonly `
  --mount type=bind,source=D:/gt-dense-model-snowflake,target=/dense,readonly `
  --mount type=volume,source=gt-context-rehearsal-assets-01,target=/assets,readonly `
  gt-harness-live-smoke:runner-nftfix-dns4 `
  python /run/desktop/mnt/host/d/gt-context-proof/run_rehearsal_01.py `
  --name NAME --wheel wheels-59 --source SHA --scenario forced-interruption
```

All three host resource settings are required: host PID namespace, host cgroup
namespace, and the read-only host `/sys/fs/cgroup` mount. Without them prior
attempts failed before any model call. No privileged container is required.
Port 80 is used by the local transport; do not launch two rehearsals on it.

Reuse the verified Linux named asset volume. It contains 10,762 LSP/runtime
files, manifest SHA256
`1245a4b6d36130483260e8cc3ceb7201af9034c047ab5ec87bee58f4bf4e08cf`.
Repeated Windows-bind metadata scans were very slow; this volume avoids them.
Do not restage/download everything or touch global Docker/GCP state.

## Producer artifact identities

Pinned producer source tree: `65964ea2b5c6490ec8e119327e47ca44cb7a43ca`.
CI 34436549266 and static build 34436550584 PASSED for efa70e52.

- Wheel: `producer-wheels-06/groundtruth_mcp-1.0.0-py3-none-any.whl`
  SHA256 `cb73ff2ed55c11d7babbdc32ef55c72268e8617913cadfa94b323ea852ca0ce9`.
- Binary: `producer-build-06/gt-index-linux-amd64/gt-index-linux`
  SHA256 `9e2973ea1060fc2c9236b0e81ea39ebb957a0013903d45517e01d4b0b25ef91c`.
- Build info: sibling `gt-index-linux.build-info.json`
  SHA256 `39d00944df6a6ad7213140d9494d606a5f082d15989f3a85d9ce57e25faa44cd`.
- Review packet: `inbox/HAR-83/har83-context-plan-producer-efa70e52.json`
  SHA256 `13a9333a50e57ceff136107a37d84ef5a067feb9be2d853b6d16c5e90c81bba4`.

Use the existing provenance verifier and actual manifest; never declare a new
capability or change a pin before its producer source/build/review evidence exists.
