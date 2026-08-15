# nano-harness — How It Works

One loop, three tools, two providers. ~850 physical lines total. This doc traces
exactly what happens when you run `nano run "fix the bug"`, so you can see where
efficiency lives and where it leaks.

## The pipeline, step by step

1. **CLI** (`cli.py`) — parses args, forces UTF-8 stdout, routes the model name
   to a provider: `--base-url` set → `OpenAIProvider` against any
   OpenAI-compatible endpoint (ASU gateway, vLLM, ollama); name starts with
   `claude` → `AnthropicProvider`; anything else → `OpenAIProvider`.
2. **Agent loop** (`agent.py`) — the core. Repeats: send conversation → get
   text + tool calls → execute tools → append results → repeat. Stops on
   `end_turn`, iteration cap (30), or context cap (200k tokens/step).
3. **Tools** (`tools.py`) — `bash` (persistent shell: bash everywhere, incl.
   Windows via Git bash; cmd.exe fallback only), `read_file` (line-numbered),
   `edit_file` (exact unique string replacement). Malformed calls come back as
   errors the model can fix, never crashes.
4. **Guards** — four things keep a run from dying or lying:
   - *Truncation*: conversation over ~120k chars → oldest tool outputs replaced
     with `[truncated]` placeholders (structure kept).
   - *Retry*: 429/5xx/connection errors retried 3× with backoff.
   - *Continuation*: output cut off mid-response → "continue" nudge, not false
     success.
   - *Verify pass*: the first "done" gets challenged once — re-read the task,
     run the tests, then finish.
5. **Result** — `AgentResult` with stop reason, iteration count, token totals,
   and a full transcript (every message, tool call, and tool result).

## GroundTruth engine lifecycle

When `gt_root` is set, GroundTruth participates at the decision boundaries of
the loop itself: task start, every completed tool observation, request
construction, the next model response, and the penultimate submit decision.
It is therefore the deterministic evidence engine for the GT arm, not a
post-run tracer. The language model still supplies reasoning; GT supplies
repository facts, deterministic checks, routing, and completion evidence.

### Central Mini-SWE action boundary

The active Terminal-Bench arm is two-sided and in-process:

```text
model.query
  -> typed ProposedAction (newline-aware; heredoc/code bodies opaque)
  -> deterministic preflight (default PASS; paid mode SHADOW)
  -> environment.exec
  -> source/workspace revisions + attributed validation status
  -> postflight effects and one-shot semantic decision frame
  -> exact provider-prepared request receipt
  -> next model.query
```

GT does not predict an action before model selection. Validation PASS/FAIL is
recorded only when the terminal foreground validator owns the shell return
code. The active loop is two-sided: typed preflight (SHADOW by default), host
execution, postflight observation, progress/completion control, then the next
model request. Deterministic provider-view compaction is bounded: it removes
no assistant turn or distinct reasoning. Oversized tool observations are
bounded even when recent, exact duplicate results are represented append-only,
and only old tool bodies may become hash/return-code receipts. No generic
state frame is inserted. The exact provider-prepared request is budgeted before
dispatch; an over-budget request is not sent. The audit history is never
mutated. The host switch is `integration_mode=off|audit|active`.

Task paths are normalized once into typed roles. Only high-confidence `OUTPUT`
resources affect task-deliverable progress; `INPUT` resources never do.
Output-existence probes can advance controller progress but cannot cover a task
obligation or issue an auto-submit certificate. `BUDGET_RISK` persists through
mere observation novelty and clears only after authored source or confirmed
task-output change.

The two audit streams answer different questions:

- `gt_ledger.jsonl`: which exact evidence bytes were sealed and delivered.
- `gt_attribution.jsonl`: which of the 17 direct mechanisms had an opportunity,
  fired, stayed dark, was suppressed, reached the provider request, and was
  linked to the next response.

Request exposure is found by structurally reading message block lists.
Trajectories are not used as the delivery witness, and response linkage is not
overclaimed as semantic consumption or benchmark causality. The next response's
tool-call IDs and names are linked to the exposed delivery IDs without retaining
raw model text or tool arguments. A paired GT-off run is the comparison needed
to attribute a behavior or reward delta to the GT arm.

## The main loop

```mermaid
flowchart TD
    START([nano run 'task']) --> ROUTE{model name /<br/>--base-url?}
    ROUTE -->|base-url set| OAI[OpenAIProvider<br/>any OpenAI-compatible endpoint]
    ROUTE -->|claude*| ANT[AnthropicProvider<br/>+ prompt caching]
    ROUTE -->|other| OAI
    OAI --> LOOP
    ANT --> LOOP

    LOOP[iteration += 1] --> ITCAP{iteration ><br/>max_iterations?}
    ITCAP -->|yes| RMAX([return: max_iterations])
    ITCAP -->|no| TRUNC{conversation ><br/>120k chars?}
    TRUNC -->|yes| DROP[replace oldest tool outputs<br/>with truncated placeholder]
    TRUNC -->|no| STEP
    DROP --> STEP[provider.step<br/>retries 429/5xx 3x]
    STEP --> CTX{context this step<br/>>= 200k tokens?}
    CTX -->|yes| RTOK([return: max_tokens])
    CTX -->|no| CUT{output cut off<br/>mid-response?}
    CUT -->|yes| NUDGE[inject 'continue' nudge] --> LOOP
    CUT -->|no| DONE{model says done /<br/>no tool calls?}
    DONE -->|no| EXEC[execute each tool call<br/>errors become tool_result errors]
    EXEC --> APPEND[append tool results] --> LOOP
    DONE -->|yes| VERIFY{first 'done' and<br/>tools were used?}
    VERIFY -->|yes| CHALLENGE[inject verify nudge:<br/>re-read task, run tests, confirm] --> LOOP
    VERIFY -->|no| REND([return: end_turn + summary])
```

## One iteration, on the wire

```mermaid
sequenceDiagram
    participant A as Agent loop
    participant P as Provider
    participant G as Model API<br/>(gateway / Anthropic / OpenAI)
    participant T as Tools

    A->>P: step(messages, tools, system)
    P->>G: POST /chat/completions (retry 3x on 429/5xx)
    G-->>P: text + tool_calls + usage
    P-->>A: StepResult (normalized)
    A->>A: log assistant text + tool calls to transcript
    loop each tool call
        A->>T: dispatch(name, arguments)
        alt ok
            T-->>A: output (truncated to 16k chars)
        else bad call / tool error
            T-->>A: ToolError -> tool_result(is_error=true)
        end
    end
    A->>A: append tool results as next user message
    Note over A: repeat until end_turn / caps
```

## The persistent shell

```mermaid
flowchart LR
    RUN[bash tool call] --> ALIVE{shell process<br/>alive?}
    ALIVE -->|no| SPAWN[spawn bash<br/>Git bash on Windows,<br/>cmd.exe only as fallback]
    ALIVE -->|yes| SEND
    SPAWN --> SEND[write command + sentinel echo]
    SEND --> READ[reader thread drains stdout<br/>into a queue]
    READ --> WAIT{sentinel seen<br/>before timeout?}
    WAIT -->|yes| OUT[return output<br/>cwd + env persist for next call]
    WAIT -->|no| KILL[kill + respawn shell<br/>error tells model:<br/>state was reset]
```

Key property: `cd` and `export` survive between calls — the model works in a
real session, not one-shot commands. On timeout the whole shell dies and the
model is told its state is gone.

`read_file` and `edit_file` resolve relative paths against the persistent
shell's live cwd, including Git Bash's Windows path form. A preceding `cd`
therefore applies consistently to all three tools.

## Where the efficiency lives (and leaks)

| Mechanism | Status | Effect |
|---|---|---|
| Prompt caching (`cache_control`) | Anthropic direct only — **inactive through the gateway** (all head-to-head runs show `cache_read=0`) | biggest cost leak on long tasks |
| Iteration cap (30) | active | load-bearing for weak models (haiku hit it on all 3 tasks) |
| Tool output truncation (16k chars) | active | keeps one noisy command from flooding context |
| Conversation truncation (120k chars) | active | keeps long runs inside the window |
| Verify pass (1 extra step) | active | buys correctness for ~1 iteration of cost |
| Loop/thrash detection | **none** | haiku burned 30 iterations on 1-line fixes; nothing stops identical repeated calls |

## GroundTruth repository-intelligence boundary

The active Mini-SWE treatment adds a host-owned deterministic intelligence
path around the stock model loop:

```text
task container --source-only bounded mirror--> RepositorySession
  -> certified graph.db + manifest at source revision S
  -> task-linked structural retrieval
  -> ContextFrontierCompiler(history, S)
  -> exact provider request N
  -> typed ProposedAction
  -> SHADOW preflight
  -> original environment.exec
  -> postflight diff/validation/features
  -> incremental graph refresh at source revision S+1
  -> next frontier/request
```

The graph is valid only when the shipped binary, schema, FTS tables, source
coverage, node/edge counts, graph hash, and validation-relevant source revision
are certified. Supported index suffixes come from the same language registry
used by workspace capture and source revision. A language can be authored
source without being structurally supported; that state is an explicit
unsupported/incomplete-coverage failure, never a license to manufacture regex
symbols. COBOL and Scheme are certified parser-backed extensions in the
vendored source, and every workflow builds that source before the graph
fixture; Racket and other unshipped grammars remain fail-closed.

The mirror transfers authored source and bounded project metadata only;
checkpoints, datasets, binaries, build outputs, caches, and task deliverables
never enter the host index. The repository frontier is selective retrieval,
not a task-start dump. It
compares certified graph facts with the exact provider view and emits the
smallest new decision frame: no more than three facts, 1,200 characters per
call, or 6,000 characters per task. Facts require a concrete path, positive
line, symbol, current graph/source revisions, semantic certainty, and retrieval
relevance. Definitions precede callers/references; already represented,
duplicate, stale, low-precision, unhealthy, or over-budget facts receive an
explicit non-delivery disposition. Complete facts are omitted rather than
truncated.

Semantic certainty and task retrieval relevance are independent. A certified
graph node can still be irrelevant to the task, and generic symbols are not
made visible by graph confidence alone. Semantic claim IDs deduplicate the same
fact across revisions while versioned fact IDs retain the exact source/graph
evidence for replay.

This architecture follows three established results: interface design changes
software-agent behavior ([SWE-agent](https://arxiv.org/abs/2405.15793));
localization/repair/validation decomposition can outperform gratuitous agent
complexity ([Agentless](https://arxiv.org/abs/2407.01489)); and iterative
repository retrieval is stronger than indiscriminate repository context
([RepoCoder](https://arxiv.org/abs/2303.12570)). The strict context budget also
reflects evidence that relevant information can become harder to use inside
long prompts ([Lost in the Middle](https://arxiv.org/abs/2307.03172)). These
papers motivate the interface; they do not prove this implementation improves
the current benchmark.

Operational failure and experimental validity are deliberately separate. A
graph failure records a degraded fallback and does not block the model from
executing commands; the merged treatment still fails promotion. Healthy empty
retrieval and facts already represented in Mini-SWE history are accounted
abstentions, not graph failures. This distinction prevents both failure modes:
destroying a baseline solve with a controller bug, and forcing generic context
merely to make visibility nonzero.

Provider-view control begins at each typed tool observation. Operation-specific
bounds reduce pathological read/search replay while preserving every distinct
assistant content and reasoning field. Large successful reads retain head,
three evenly spaced interior windows, and tail. Soft checkpoint compaction is considered
at 120,000 provider characters toward an 80,000-character target only when the
exact projection saves at least 20,000 characters and 10% of the view; smaller
changes are deferred to preserve a stable provider-cache prefix. Hard prompt
headroom remains a separate fail-before-query invariant.

`require_graph_ready=true` therefore means analytically fail closed and
operationally fail open. The receipt contains substrate reasons, fallback state,
retrieval disposition, provider exposure, and request hashes. The later merge
audit remains authoritative for intelligence validity, payload correctness,
timing, outcome preservation, and efficiency.

The deterministic implementation boundary was certified on exact commit
`e6ce41f` by provider-free workflow `31244088870`. Its checked-out Linux
indexer produced certified COBOL, Python, and Scheme nodes and directed call
edges; 311 workflow-scope tests, structural readiness, and static checks
passed. This certification does not establish a live outcome or efficiency
gain; those remain matched-smoke acceptance gates.

### Action-result and progress identity

The shell proposal adapter does not give redirection syntax to the executable
classifier as argv. It emits semantic argv plus typed redirections, preserving
the distinction between descriptor duplication, file input, and file output.
Validation authority, requested timeout, typed reads, and workspace mutation
therefore derive from the same parse.

Postflight assigns each command an executable-aware result kind. An attempt is
identified by operation, executable, targets, source revision, and declared
check; its observation additionally includes result kind and output hash. This
prevents different fallback tools from collapsing into one failed action while
also detecting a genuine repeated observation. Observation novelty does not
equal task progress. The controller clears semantic budget risk only for an
attributed validation pass or confirmed task-output change. Repeated
same-state controller updates are accounted privately and do not add another
model call or provider frame.

Repository facts cross the provider boundary only when grounded in the current
decision context. A path-only need may select a file location. A definition,
caller, reference, test, or named symbol requires the exact symbol or relation
target to be represented in Mini-SWE history; graph confidence alone is not
decision relevance.

Efficiency measurement includes response batching and uses actual model-query
invocations as the denominator. Promotion fails if common-solved tokens, API
calls, assistant steps, model actions, normalized cost, or controller-inclusive
effective actions violate their configured aggregate boundary. Consequently a
token reduction cannot mask more reasoning turns or more host work.

## Final regression-repair contract (2026-08-09)

GT source identity is semantic. SourceRevisionReceipt hashes canonical source path plus full-content SHA-256 only; raw workspace metadata remains a separate audit revision. Missing source digests invalidate graph refresh and completion certification without blocking Mini-SWE. Internal revision hashes are never model-visible.

Repository facts have persistent provenance (TASK_START, MODEL_AUTHORED, OBSERVED_EXTERNAL, or UNKNOWN) and exactly one eligible provider call. Task-start facts cannot spill, and new claims on model-authored paths remain controller-only. Genuine new cross-file consequences may remain eligible. newfile_precedent can use only a non-empty compatible task-start source and receipts precedent_origin=task_start_repository.

ProviderEvidenceLedger is the authoritative provider-context accounting surface. It joins graph_frontier, feature_fact, state_frame, progress_frame, and preflight_return events to evidence action, eligible/prepared/dispatched calls, exact provider message indices, request hash, characters, disposition, reasons, and revision. A represented fact with zero newly inserted characters is correct GT operation; never force provider text merely to avoid a zero-visible count.

Provider request lifecycle is explicit: provider_requests_prepared, model_query_invocations, provider_responses_received, and provider_requests_not_sent. api_calls equals actual model_query_invocations. An unsent prepared request confirms no delivery and contributes no visible context.

Deterministic compaction restores only a current fact whose last concrete provider representation it removed. It does not inject generic controller state, repeat adjacent frames, delete unique assistant reasoning, or truncate a fact. StallAggregateFact is a separately gated controller fact, not an eighteenth feature: deterministic, declarative, <=320 characters, at most twice per task, first-eligible, source-bound, and non-predictive.

Replay v2 is exact and content-addressed under gt_replay/ (manifest.json, calls.jsonl, blobs/<sha256>.json.gz). The verifier fails closed on corruption. Workspace source capture caches its working backend; a missing task-image python3 is not retried on every edit. Local graph resolution prefers the checked-out pinned gt-index binary over obsolete machine-global builds.

Efficiency gates aggregate provider/model resources only across common uncensored solves. Tokens, actual model calls, model-selected actions, assistant responses, cost, and wall time are primary. Effective actions and host/controller/sensor executions are reported separately. Cheap failed tasks cannot improve the aggregate.

Provider-free implementation evidence is recorded in details_done/GT_FINAL_REGRESSION_REPAIR_AND_89_GATE_20260809.md. The archived ten-task replay passed; this is not live outcome proof. The next permitted paid step is the exact ten-task certified_full/integrated GT-on smoke after the exact pushed commit prints SMOKE_APPROVED. Preflight remains SHADOW. The 89-task run remains blocked until that smoke has no uncensored outcome regression/censor, valid graph substrate, complete provider-evidence accounting, zero invalid/late/predictive/duplicate delivery, and an aggregate common-solved provider/model efficiency win.
