# GT two-task diagnostic: 31270761663

Date: 2026-08-08  
Commit under test: `48b879166377c236c77f4f64ee85b069e41ae668`  
Tasks: `cobol-modernization`, `write-compressor`  
Agent: `eval.gt_central_agent:MiniSweCentralAgent`  
Mode: `ACTIVE` integration, `SHADOW` preflight, all 17 features enabled

## Decision

This run is rejected as treatment evidence. Both verifiers returned reward 1,
but `write-compressor` failed the required repository-intelligence gate. A
solve with a dead or stale graph cannot prove that GT supplied deterministic
repository intelligence. No 89-task run is authorized from this evidence.

## What the run actually measured

| task | reward | total tokens | API calls | GT guidance events | guidance chars | frontier deliveries | graph status | final nodes/edges |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| cobol-modernization | 1 | 4,197,876 | 77 | 1 | 157 | 0 | passed | 10 / 6 |
| write-compressor | 1 | 749,930 | 20 | 1 | 78 | 0 | failed (`mirror_incomplete`) | 0 / 0 |

`guidance_events` is the direct feature-guidance stream, not the count of all
GT work. Private engine state, existing actuation, represented Mini-SWE
history, and frontier accounting are separate receipt classes. Therefore “one
guidance event” is a warning that requires inspection, but not proof that only
one GT operation happened. In COBOL, the zero frontier count was a correct
deduplication result for candidates already present in durable model/tool
messages. In write-compressor, zero frontier delivery was not acceptable because
the graph substrate became invalid.

## Root cause

`gt_engine.central_runtime.WorkspaceSensor.scan()` captured changed source with
one hard-coded command:

```text
python3 -c '... read paths and emit JSON/base64 ...'
```

The write-compressor trajectory contains the task-image result:

```text
bash: line 143: python3: command not found
```

The model later authored/changed C source. The sensor still obtained manifest
metadata and SHA-256 hashes, but it did not obtain source contents. The
repository session therefore could not apply the transition, recorded
`mirror_incomplete`, invalidated the current graph, and suppressed graph-backed
frontier facts. This is a host/task-image capability mismatch, not a model
acknowledgement problem and not evidence that the 17 feature producers failed.

## Repair

The sensor keeps the Python JSON/base64 path as the fast path. If it returns a
nonzero status, malformed JSON, an invalid base64 value, or only a partial set
of paths, it now runs a shell-native bounded fallback:

```text
for p in <validated paths>; do
  printf '%s\t' "$p"; base64 "$p" | tr -d '\n'; printf '\n'
done
```

The fallback is decoded only for exact paths selected from the validated
workspace manifest. It does not infer symbols, rewrite commands, or weaken the
existing hash/metadata authority. If both capture methods fail, behavior
remains fail-open for Mini-SWE execution and fail-closed for the intelligence
gate.

## Proof

Added RED-first regression test:

`tests/test_gt_central_runtime.py::test_sensor_captures_source_when_task_image_has_no_python`

The test simulates a task image returning exit 127 for `python3`, then verifies
that the fallback captures exact C source bytes and that both capture commands
were attempted. It failed before the patch and passes after it.

Provider-free checks after the patch:

```text
python -m pytest tests/test_gt_central_runtime.py tests/test_gt_repository_intelligence.py tests/test_gt_central_agent.py -q
........................................................................ [100%]
python -m scripts.central_feature_census
ALL_17_PRODUCERS_PROVEN
ALL_17_CONSUMERS_PROVEN
ALL_EFFECTS_TIMING_VALID
ALL_PAYLOADS_GROUNDED
ALL_17_CONSUMER_PATHS_PROVEN
ALL_17_TRIGGERS_PROVEN
ALL_17_PAYLOADS_CONCRETE
ALL_17_CONSUMERS_APPLIED
ALL_VISIBLE_PAYLOADS_IN_FIRST_ELIGIBLE_REQUEST
NO_ACTIONS_BLOCKED
ALL_EFFECTS_CONTEXT_ACCOUNTED
REPOSITORY_SUBSTRATE_PROVEN
CONTEXT_FRONTIER_PROVEN
```

The census was run with the pinned local `GT_INDEX_BINARY`; without that
explicit binary, the fail-closed parser gate correctly rejects the environment
instead of claiming COBOL/Scheme support from the Python registry alone.

## Terminal-Bench language coverage implication

The official Terminal-Bench 2 repository visibly includes C, C++, Rust/C
polyglot, JavaScript, COBOL, Scheme, OCaml, Python/data-science, shell/system,
and mixed build/runtime tasks. Its GitHub language breakdown is repository
composition rather than a task-source histogram: Shell 40.2%, Python 22.6%,
C++ 17.1%, C 6.4%, Scheme 5.4%, JavaScript 2.8%, Other 5.5%.

Our registry currently has structural support for Python, JavaScript,
TypeScript, Go, Rust, Ruby, Java, Kotlin, C#, PHP, Swift, Scala, C/C++, Lua,
Elixir, OCaml, shell, CSS, CUE, Elm, Groovy, HCL, HTML, protobuf, SQL, and
Svelte, plus certified COBOL and Scheme. Racket, Objective-C, Erlang, Haskell,
Clojure, Dart, Zig, Perl, F#, Visual Basic, R, Verilog, Red, and POV-Ray remain
explicit fail-closed unsupported languages. The four code-like TB2 suffixes
are now recognized as validation-relevant, so their presence cannot silently
disappear from source coverage; they still require certified parsers before
graph-backed intelligence can be claimed. That is the correct behavior until a certified parser
and graph gate exists; silently dropping them would make GT appear healthy while
providing no repository intelligence.

Source: [Terminal-Bench 2 task list and benchmark scope](https://github.com/harbor-framework/terminal-bench-2)
and [GitHub language breakdown](https://github.com/harbor-framework/terminal-bench-2#readme).

## Next gate

1. Run the full provider-free suite and `scripts/central_pre_smoke_gate.py` on
   the pushed repair commit.
2. Re-run the two-task matched smoke only after explicit authorization.
3. Require both tasks to finish with current graph substrate, nonzero/explicitly
   accounted frontier decisions, first-eligible timing, and outcome-preserving
   efficiency before interpreting guidance counts.
4. Keep the 89-task run blocked until repeated matched outcome-first trials pass.
