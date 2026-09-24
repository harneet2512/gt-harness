# Canonical typed query surface (HAR-90, workstream W4)

All 16 certified kinds dispatch through one path:
`miniswe_runtime` typed branch -> `execute_typed_action_fail_open` -> `build_action_request`
(core `ActionRequest`) -> `execute_typed_action` -> wheel `deterministic_queries.execute_query`.
`why_this_edge` is not advertised. Its private compatibility path reads the edge from the live
graph and answers AUGMENT/incomplete. It never certifies facts the caller supplied.

## Certification (generated, `scripts/generate_gt_finalstand.py --check` clean)

The producer's compatibility authority labels every graph-backed kind `sound_overapprox`. The
harness applies a ceiling that can only lower a label. Every graph-backed kind is certified
`partial`, for these reasons:

- Resolver edges are name-level: a stdlib `subprocess.run` resolves to a same-file `run` method and is marked CERTIFIED.
- Dynamic dispatch is missed.
- Several producers rely on fixed manifests.

A `partial` kind is advertised and answered with its omissions. A producer `EXACT` answer for it is
demoted from REPLACE to AUGMENT, with reason `CERTIFICATION_NOT_EXACT` and omission
`certified_semantics:partial`. Per-language certification is enforced at the gate. A `language` or
`path` argument that names a registered language outside the kind's certified set is refused with
`typed_language_not_certified:<lang>`.

| kind | semantics | languages | stated limit |
|---|---|---|---|
| exact_literal_search | exact | all 30 | explicit scope; 20 matches, 256 B/line |
| syntax | exact | go, js, py, rb, ts | parse-only |
| verification_status | execution_specific | all 30 | bound to command and revision |
| definition, references, callers, symbol_context, processes, patch_impact | partial | go, java, js, py, rust, ts | name-level resolution |
| route_map, api_impact | partial | go, java, js, py, ts | fixed framework manifest; MIDDLEWARE_ON not surfaced |
| taint | partial | go, java, js, py, rust, ts | symbol-level CALLS reachability plus harness-side statement dataflow for Python sources (def-use over resolved callsites; unresolved callees/non-Python/unbound varargs are named omissions); not an over-approximation |
| rename, shape_check, tool_map | partial | go, java, js, py, rust, ts | see CSV basis |
| slice | partial | go, java, js, py, ts (CFG substrate only) | interprocedural hops are name-matched |

## Revision identity (CANON cross-stream contract)

- `RevisionVector.graph` is `configuration["graph_source_revision"]`. The runtime sets it from `EngineState.graph_source_revision` whenever the graph is current.
- Without a bound revision, the value is the sentinel `graph-source-revision-unbound`. It is never read back out of the graph.
- The indexer passes `-source-revision <workspace revision>` on full builds, batch amends and `-file` incrementals, but only when the producer's `-build-info` declares `source_revision_meta_v1`.
- The vendored producer (0becde10) does not declare that capability. Its graphs therefore carry no `source_revision`, and the vendored wheel (5681eeae) still compares against `project_meta.git_commit`, which is the producer's build commit. Until W2's producer and W3's wheel are vendored, every graph kind reports `graph_revision_mismatch`. That report is honest: the revision cannot yet be verified.

## Bounded output

`QUERY_RESULT_MAX_BYTES = 16384` now bounds dict answers too (`gt_engine/typed_output_bounds.py`):

- The bound repeatedly halves the largest list, whether it sits inside the answer or in the evidence `anchors`/`witnesses`.
- It keeps `direct_answer`, `evidence.direct_answer_json` and `honesty.payload` identical.
- It records `query_result_byte_limit` and `query_result_truncated:<path>:<kept>/<total>`.
- It withholds an answer that cannot fit, recording `query_result_unbounded_payload`.
- It is deterministic.

exact_literal_search additionally applies its 20-match and 256-byte line caps.

## Measured wire sizes (envelope format unchanged)

These were measured on the polyglot fixture in `tests/test_typed_graph_real_producer.py`, with a local gt-index build from 0becde10 and the vendored wheel. Every answer is carried 3 times: `direct_answer`, the escaped `evidence.direct_answer_json`, and `honesty.payload`. Hashes and the request echo add about 3.1-3.7 KB fixed overhead. Changing this is a delivery-policy decision for the next phase.

| kind | arguments | output bytes | answer bytes | overhead bytes |
|---|---|---:|---:|---:|
| exact_literal_search | `{"literal":"clean","paths":["app"]}` | 5321 | 638 | 4683 |
| exact_literal_search | `{"literal":"clean","paths":["."]}` | 16164 (bounded) | 2893 | 13271 |
| definition | `{"symbol":"clean"}` | 3905 | 258 | 3647 |
| callers | `{"symbol":"clean"}` | 3704 | 197 | 3507 |
| route_map | `{}` | 6374 | 1021 | 5353 |
| api_impact | `{"route":"/items"}` | 4849 | 558 | 4291 |
| taint | `{"source":"list_items","sink":"execute"}` | 5374 | 684 | 4690 |
| rename | `{"symbol":"clean","new_name":"sanitize"}` | 5710 | 802 | 4908 |
| shape_check | `{"symbol":"Friendly"}` | 4125 | 327 | 3798 |
| tool_map | `{}` | 3156 | 27 | 3129 |
| slice | `{"symbol":"list_items","line":35}` | 4620 | 474 | 4146 |
| slice | `{"symbol":"list_items","line":35,"interprocedural":true}` | 8052 | 1554 | 6498 |

## Known defects left in place (pinned as strict xfails or documented)

- **route_map (wheel):** an API_CALL-only route yields anchor line 0, and the whole answer becomes `producer_not_supported`.
- **shape_check (producer/wheel):** conformance counts callable members only — interface `property_signature` members are unchecked, and each IMPLEMENTS/DECLARED_IMPLEMENTS edge emits its own verdict row (one logical check can appear twice).
- **tool_map (wheel):** undecorated registration sites (`server.add_tool(f)` calls) are not provable from the graph — reported as `registration_sites_untracked`, never guessed.
- **callers/references (wheel):** these silently keep 20 rows per band while still labelling the answer exact.
- **exact_literal_search (wheel):** scope `.` scans `.git/`.
