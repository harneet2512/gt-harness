# HAR-85 — `exact_literal_search` abstained on a complete graph

Date: 2026-09-04. Branch `cloud/internal-harness`. Codespace
`gt-cloud-agent-wvrqp4rqpjp42gvp7`, stack from `cloud/docker-compose.yml`,
model `nvidia/nemotron-3-super-120b-a12b:free`, `gt_mode=advisory`,
producer `0aadb1b9…+cloud.1`.

## 1. The prime suspect is wrong

`docs/cloud-gt-run.md` §3 flagged a producer warning during indexing:

```
[WARN] nodes_fts clear/insert failed (database disk image is malformed) —
DROP+recreate to self-heal the FTS5 index
```

and the working theory was that the self-heal left the FTS5 tables empty, so
literal search had no coverage. **Both halves of that theory are false.**

Fresh click session `2e25f7e3208e`, graph at
`.gt_state/f3a2a5b531a128c8/graph.db` (170 MB), read-only sqlite3 3.46.1:

| table | rows |
|---|---|
| `nodes` | 62,839 |
| `nodes_fts` | **62,839** |
| `edges` | 79,216 |
| `content_passages` / `content_passages_fts` | *do not exist in this schema* |

```
select count(*) from nodes_fts where nodes_fts match 'Command'  ->  89
pragma integrity_check                                          ->  ok
```

The self-heal worked: the FTS index is fully populated, matches the literal,
and the database is not corrupt.

More decisively, **`exact_literal_search` never opens the graph at all.** The
producer is `groundtruth/runtime/deterministic_queries.py::_literal_search`
(vendored wheel `vendor/groundtruth_mcp-1.0.0-py3-none-any.whl`, dispatched
from `gt_engine/miniswe_typed_actions.py:677-723` via
`_deterministic_query_api()` at `gt_engine/miniswe_typed_actions.py:550`). It
is a pure filesystem scan over an explicitly declared scope; `graph_db` is
carried on the context but unused by this query kind. An empty FTS table could
not have produced this symptom.

## 2. Root cause: a glob scope is stat'ed as a literal path

The planner asked for `paths: ["src/click/**"]`. The producer treats every
entry of `paths` as a concrete filesystem path:

* `deterministic_queries.py:135-147` — `_safe_scope()` joins the string to the
  repository root and resolves it. No globbing, no `fnmatch`, no `Path.glob`.
* `deterministic_queries.py:150-152` — `_iter_scope()`:

  ```python
  if not scope.exists():
      omissions.add(f"missing_scope:{_rel(root, scope)}")
      continue
  ```

* `deterministic_queries.py:266-270` — the produced artifact:

  ```python
  exact = not omissions and bool(scopes)
  ... EvidenceSemantics.EXACT if exact else EvidenceSemantics.INCOMPLETE,
      Coverage.COMPLETE if exact else Coverage.PARTIAL,
  ```

* `observation_compiler.py:396-403` — the interception decision then stamps
  `SEMANTICS_NOT_EXACT`, `COVERAGE_NOT_COMPLETE`, `EVIDENCE_HAS_OMISSIONS`, and
  `gt_engine/miniswe_typed_actions.py:711` turns `INCOMPLETE` into
  `returncode 2`.

`<workspace>/src/click/**` is not a file or a directory, so `scopes` is
non-empty but the walk yields nothing, `omissions == ["missing_scope:src/click/**"]`,
and the artifact is honestly `incomplete` with `matches: []`. **The abstention
was correct. The scope simply never existed.**

### Data evidence

Run inside the container against the live workspace of session `2e25f7e3208e`
(click @ `36baa15ff831b939a22bc527cd76ce653ef6f66d`, session idle, snapshot
complete, 215 files, 18 of them under `src/click/`), calling the real
`gt_engine.miniswe_typed_actions.execute_typed_action_fail_open`:

```
=== paths ['src/click/**']      rc 2  semantics incomplete  coverage partial
    omissions    ['missing_scope:src/click/**']
    reason_codes ['SEMANTICS_NOT_EXACT','COVERAGE_NOT_COMPLETE','EVIDENCE_HAS_OMISSIONS']
=== paths ['src/click']         rc 0  semantics exact  coverage complete  omissions []
=== paths ['src/click/core.py'] rc 0  semantics exact  coverage complete  omissions []
=== paths ['.']                 rc 2  omissions ['snapshot_scope_content_mismatch','query_result_byte_limit']
```

One character of difference between an abstention and an exact answer.

Nothing else is broken: with a real scope the same session's transcript
(`.gt_state/transcript.json`, messages 6 and 8) shows
`semantics: exact`, `coverage: complete`, `omissions: []`,
`reason_codes: ['EXACT_COMPLETE_EQUIVALENCE']` and populated `matches`.

### Secondary observation (not fixed)

`_snapshot_authority()` (`gt_engine/miniswe_typed_actions.py:225-270`) excludes
`.git` but **not** `.gt_state`, which GT itself appends to during a turn
(`events.jsonl`, `provider_requests/`). Probing the workspace *while* a turn was
running reproducibly added `repository_revision_mismatch` and
`working_tree_sha256_mismatch` to the omissions of every typed action, because
the manifest is captured twice (once in `build_action_request`, once in
`execute_typed_action`) and the tree moved in between. The live turns observed
here did not hit the race, and it is not the HAR-85 symptom, so it is recorded
rather than fixed — it touches the benchmark path.

## 3. Fix

Neither producer option applies: `gt-index` is not in this code path, and the
FTS self-heal is healthy. The fix is cloud-side and does not touch
`gt_engine/` or the benchmark path.

**`cloud/server/typed_scopes.py` (new)** reduces a glob-style scope to the
concrete directory it selects, before the request reaches the producer:
`src/click/**` → `src/click`. It is deliberately conservative:

* only a scope that actually contains `*`, `?` or `[` is rewritten, so a plain
  typo (`src/click/coree.py`) still abstains instead of silently widening to
  its parent;
* a scope containing `..` is never reduced, and neither is an absolute one;
* the rewrite keeps the longest leading run of literal segments, so the
  searched scope is always a **superset** of the requested one — evidence stays
  complete and never overclaims. The producer echoes what it really searched in
  `answer["scope"]`, so the planner sees the widening;
* if the reduced prefix does not exist inside the repository root, the original
  string is passed through untouched and the producer abstains exactly as before.

**`cloud/server/runner.py:593-604`** builds the typed Mini-SWE model through
`build_scope_normalizing_model()`, a `GroundTruthLitellmModel` subclass whose
`_parse_actions()` normalizes literal-search scopes. This is the first point
where raw model arguments enter the harness, and it is cloud-only.

## 4. Tests

`tests/test_cloud_typed_scopes.py` — 14 tests. 11 pin the normalizer
(recursive glob, extension glob, interior glob, bare `**`, concrete paths
untouched, typo not widened, missing prefix left alone, `..` and absolute
escapes left alone, non-string pass-through, arguments copied not mutated,
non-literal-search actions untouched). 3 drive the **real** typed-action code
path through `execute_typed_action_fail_open` on a temporary repository:
the glob abstains with `missing_scope:` and `matches: []`; the normalized glob
returns `exact` / `complete` / `omissions: []` with the right matches; a
genuinely missing scope is *not* rescued.

The three code-path tests `importorskip` the vendored deterministic-query
module, so they run in the cloud image and skip on a checkout without the wheel.

```
in container (cloud-server:latest):  14 passed
local checkout (no wheel):           11 passed, 3 skipped
```

## 5. Cloud verification

Deployed to the codespace (`gh codespace cp`, `docker compose build server`,
`up -d`). The producer stage was cached; only the Python layers rebuilt.

Deterministic proof against the deployed image — the real cloud model class,
fed a synthetic `groundtruth` tool call with `paths: ["src/click/**"]`:

```
model class: ScopeNormalizingGroundTruthModel -> GroundTruthLitellmModel
normalized action: {"kind":"exact_literal_search",
                    "arguments":{"literal":"class Command","paths":["src/click"]}}
returncode: 0
semantics: exact  coverage: complete  omissions: []
reason_codes: ['EXACT_COMPLETE_EQUIVALENCE']
scope: ['src/click']
matches: 2
    src/click/core.py 959  class Command:
    src/click/core.py 2119 class CommandCollection(Group):
```

### Before / after transcript

Before — `docs/cloud-gt-run.md` §4, session on click, two GT actions:

```
exact_literal_search "class Command" scoped to src/click/**
returncode 2 · "typed evidence incomplete" · matches: []
semantics: "incomplete"
reason codes: SEMANTICS_NOT_EXACT / COVERAGE_NOT_COMPLETE / EVIDENCE_HAS_OMISSIONS
```

After — session `62f5f89249fb`, same message
("Which module defines the Command class and what calls its invoke method?
Answer briefly."), `.gt_state/transcript.json`:

Session `62f5f89249fb` on click, second turn. The raw provider response
(`.gt_state/73d60fa7af437c87/provider_responses/915ae2f9….json`) shows the
planner still emitting a glob:

```
RAW MODEL TOOL CALL: groundtruth
{"kind":"exact_literal_search",
 "arguments":{"literal":"class Command","paths":["src/click/**"]}}
```

The action that reached the producer (`.gt_state/transcript.json`,
`messages[35].extra.actions[0].gt_action`) is the normalized one:

```json
{"kind": "exact_literal_search",
 "arguments": {"literal": "class Command", "paths": ["src/click"]}}
```

and the compiled observation (`messages[36]`) is no longer an abstention:

```
<returncode>0</returncode>
semantics: exact      coverage: complete      omissions: []
reason_codes: ['EXACT_COMPLETE_EQUIVALENCE']
scope: ['src/click']   files_observed: 18     matches: 2
    src/click/core.py:959   class Command:
    src/click/core.py:2119  class CommandCollection(Group):
```

The agent's answer: "The `Command` class is defined in **src/click/core.py**
(line 959). Its `invoke` method is called by `Command.main` →
`self.invoke(ctx)`, `Group.invoke` → `super().invoke(ctx)`, and `Group.invoke`
→ `sub_ctx.command.invoke(sub_ctx)`." — correct, and this time it is grounded
in typed evidence rather than plain file reads.

The first turn of the same session (the verbatim message from
`docs/cloud-gt-run.md` §4) happened to use only Bash: this free model chooses
the typed tool nondeterministically. That turn is not evidence either way; the
second turn, which pinned the glob, is.


Sessions `2e25f7e3208e` and `62f5f89249fb` were closed afterwards.

## 6. Follow-up

A typed action still produced no frame on the session's event stream, because
it never reaches `env.execute` and therefore never reaches the emitting
environment proxy. `gt_action` events, and the `gt_actions` /
`gt_exact_matches` receipt counters, are
[`har84-gt-action-events.md`](har84-gt-action-events.md).
