# GT generalized regression root cause and repair — 2026-08-10

## Decision

Workflow `31421610097` is rejected release evidence. It solved 15/20 tasks
against 17/20 in the frozen GT-off reference and had two graph-invalid tasks.
The loss is not explained by one cause, and it is not defensible to label all
four solve losses either "temperature variance" or "GT-caused."

Confidence in the diagnosis:

- **High:** GT emitted semantically false `STALLED` state on successful,
  distinct searches/experiments in all four lost tasks.
- **High:** repository archive transfer assumed `/app` although task cwd was
  `/workspace` or `/app/dclm`, invalidating two graph substrates.
- **High:** `/dev/null` diagnostic redirection was misclassified as a
  workspace mutation; replay of the 20 receipts changes 155 actions from
  falsely mutating to read-only.
- **High:** destructive Git maintenance was not classified as mutating;
  `sanitize-git-repo` executed `update-ref -d`, `reflog expire`, and
  `gc --prune=now` through an action typed as read/may-mutate.
- **High:** all four lost trajectories diverged from the frozen reference on
  their first model action, before any GT evidence existed.
- **High:** the frozen reference and central treatment do not isolate GT. The
  reference uses stock Mini-SWE 2.2.8 execution while the treatment uses the
  host-central loop; observed shell error prefixes also differ (`/bin/sh`
  versus Bash). It remains useful as a historical target, not as a clean
  causal control.
- **Moderate:** the false progress frames contributed to later trajectory
  divergence. Exact request capture proves exposure and timing, not internal
  model causality.

## Top-down causal decomposition

| Layer | Evidence | Classification | General repair |
| --- | --- | --- | --- |
| Model sampling | First action differs in all four losses while system/user messages are byte-identical | Pre-GT temperature-1 divergence | Do not attribute this edge to GT; compare GT modes inside one host loop when causal isolation is required |
| Controller state | Distinct successful actions shared one `attempt_id` and produced false `STALLED` frames | Confirmed GT defect | Include normalized command identity in the attempt hash; keep observation novelty and verified task progress separate |
| Action typing | 155 archived read/search actions were marked mutating because of `2>/dev/null` | Confirmed GT defect and host-work inflation | Exclude the null sink from filesystem mutation evidence |
| Irreversible operations | Git history deletion/GC was typed unknown/read | Confirmed GT defect | Classify generic mutating Git subcommands before execution |
| Graph transfer | Archive member paths were always prefixed with `app/` | Confirmed substrate defect | Root archive members and transforms at resolved cwd |
| Graph lookup | Absolute action targets were stripped relative only to `/app` | Confirmed substrate/utility defect | Canonicalize targets relative to resolved cwd; preserve external absolute paths |
| Semantic verification | The four losses failed unseen semantics: alternate ELF input, preserved Git object, distributed row-parallel shape, alternate-video resource scale | Capability boundary | Prefer discovered executable checks, test selection, and exact contracts; do not invent hidden tests or call generic advice verification |
| Provider context | Four losses received progress frames even when feature-guidance count was zero | Audit-accounting defect in the previous report | Count guidance, graph frontier, and controller progress frames as separate provider-visible surfaces |

## The false-stall mechanism

Before this repair, `ProgressObservation.attempt_id` included operation,
executable, targets, source revision, and declared check, but not the command.
The shell parser intentionally does not treat a grep query or an opaque
interpreter program as a file target. Therefore these distinct actions could
collapse to one identity:

```text
grep <secret-pattern-A> ...
grep <secret-pattern-B> ...
grep <secret-pattern-C> ...
```

The archived `sanitize-git-repo` receipt demonstrates the failure directly:
actions 3, 4, and 5 had the same attempt hash, three different output hashes,
and successful return codes. Action 5 nevertheless transitioned to
`STALLED`. A later distinct search produced another `STALLED` transition with
`repeat_count=1`. Similar false frames were delivered in `extract-elf`,
`torch-tensor-parallelism`, and `video-processing`.

The repair hashes the exact selected command into the private attempt identity
and records only that hash. Exact bytes are deliberate: whitespace inside a
quoted or opaque program can be semantic. This does **not** promote a novel output
to verified task progress. The boundaries remain:

1. command identity: is this mechanically the same attempted action?
2. observation identity: did that action return the same typed result/output?
3. observation gain: did it add a localized anchor or attributable diagnostic?
4. task-progress gain: did an attributable validation pass or confirmed task
   output advance completion?

Only task-progress gain can clear `BUDGET_RISK` or support completion. Thus the
fix removes false stall advice without letting timestamp noise, scratch output,
or arbitrary activity masquerade as a solve.

## The action-classifier inversion

The old mutation detector treated every filesystem output redirection as a
workspace mutation, including the special sink `/dev/null`. On the archived
20-task receipts, current-code replay changes the count of mutating proposals
from 445 to 291: 155 false mutations disappear and one missed destructive Git
action becomes mutating.

This matters even in SHADOW mode. A proposal not proven read-only forces an
otherwise unnecessary full workspace manifest/hash/capture boundary after the
action. The 155 corrected actions are therefore 155 projected scan-avoidance
opportunities in this exact sample. This is deterministic host-work reduction,
not a claim about model tokens or solved tasks.

The newly recognized Git subcommands are `filter-branch`, `filter-repo`, `gc`,
`reflog`, and `update-ref`. Recognition changes typing, stale-batch safety, and
receipts. It does not silently block or rewrite the command, and the paid
workflow remains SHADOW.

## Graph correction

The mirror planner returns paths relative to the resolved task cwd. Transfer
now constructs remote archive members under that cwd and strips the same cwd
prefix when extracting into the host mirror. Provider-free tests cover
`/workspace` and nested `/app/dclm`, in addition to the existing `/app` path.
Action-target canonicalization now uses the same resolved cwd, so an absolute
`/app/dclm/ray/process.py` target matches snapshot key `ray/process.py`.

Initially source-less tasks were rechecked before changing this design. They
already retain a live `RepositorySession`: when the model creates indexable
source, the session incrementally refreshes to `source_backed`. The run proves
this for `torch-tensor-parallelism` (18 refreshes) and `video-processing` (37
refreshes). Their task-start applicability remains denominator-excluded by
contract, but their dynamic graph is usable. No redundant bootstrap change was
made.

## Research-derived architecture constraint

The generalized direction is not "send more facts." Research supports a
three-part coding loop:

- SWE-agent shows that the agent-computer interface materially changes coding
  performance; action typing and observation design are part of the algorithm,
  not neutral plumbing.
  <https://proceedings.neurips.cc/paper_files/paper/2024/hash/5a7c947568c1b1328ccc5230172e1e7c-Abstract-Conference.html>
- Agentless separates localization, repair, and patch validation, supporting a
  bounded evidence lifecycle instead of a generic advisory stream.
  <https://arxiv.org/abs/2407.01489>
- SWT-Bench reports that executable tests substantially improve patch
  filtering precision. Verification evidence is stronger than additional
  prose context.
  <https://arxiv.org/abs/2406.12952>
- OpenAI's Codex loop description makes tool results part of the next inference
  and emphasizes stable prefixes and pressure-triggered compaction; arbitrary
  provider-view changes are behavior changes.
  <https://openai.com/index/unrolling-the-codex-agent-loop/>

For GT this implies a model-agnostic hierarchy:

```text
typed proposal
→ deterministic action/safety classification
→ minimal source- and revision-bound evidence
→ execution
→ typed observation and graph refresh
→ executable validation/certificate when mechanically available
→ bounded provider frame only for a new decision-relevant contradiction
```

Graph facts contract the search space. Typed controller state avoids redundant
host work and detects stale/irreversible transitions. Executable validation
selects among candidate states. These mechanisms are complementary; graph
coverage alone cannot verify resource scaling, distributed semantics, binary
format generalization, or preservation of an unreachable Git object.

## Verification completed

- RED-first tests prove distinct same-shape commands have different attempt
  identities while exact repetitions remain identical.
- End-to-end agent test proves three distinct successful opaque experiments do
  not emit `STALLED`; the existing exact-repeat test still emits one bounded
  first-eligible stall frame.
- `BUDGET_RISK` monotonicity and validation-only recovery tests remain green.
- Source archive tests prove resolved-cwd member addressing for `/workspace`
  and `/app/dclm` and preserve source-only extraction.
- Target canonicalization tests cover `/workspace`, nested `/app`, relative,
  and external paths.
- Preflight tests prove `/dev/null` searches are read-only and destructive Git
  maintenance is mutating.
- 132 focused central-agent/preflight/progress tests passed.
- 25 repository-intelligence/mirror tests passed.
- `python -m scripts.central_feature_census` passed all required all-17,
  timing, grounding, context-accounting, graph, and baseline-shield markers.
- `python scripts/central_readiness_audit.py` printed `READY`.
- `central_pre_smoke_gate.py` passed its 212-test functional body, census,
  graph fixture, language contract, and readiness checks; it correctly stopped
  only at `exact pushed commit` because these changes were not yet committed
  and pushed at audit time.

## Remaining release boundary

This patch removes confirmed deterministic defects. It does not prove that a
new temperature-1 rollout will solve all four historical losses, and it cannot
make absent semantic validators authoritative. Before an 89-task run:

1. run a paid repair-mix smoke only with separate authorization;
2. require valid graph substrate for every applicable task, zero false progress
   frames, zero late/duplicate/ungrounded context, and no solve regression;
3. compare tokens, actual model calls, assistant steps, model actions, effective
   actions, host scans, latency, and wall time only on common uncensored solves.

The repaired implementation was committed and pushed as `dd2884e`. The exact
pushed-commit pre-smoke gate then passed its complete functional and substrate
scope and printed `SMOKE_APPROVED`. This authorizes no paid run by itself; it
establishes that a separately authorized smoke would test the intended code.

The correct product claim after this patch is narrow: GT's controller state,
action typing, graph transfer, and provider frames are more accurate and less
wasteful. No solve-rate or causal-efficiency claim is made without the next
live gate.
