# GroundTruth benchmark output contract

TB2 uses the frozen `repair20-v1` task order for its release smoke. Every task
must retain its task result, verifier outcome, provider-route/model identity,
and the central receipt when GT is enabled. The merged row carries solved /
unsolved / ungraded-or-censored classification plus provider calls, assistant
and decision steps, effective actions, total tokens, provider cost (or explicit
missing-cost), wall time, and GT context/persistent-state lifecycle counters.

DeepSWE and Live Lite are separate benchmark contracts: DeepSWE uses its 113
task pinned manifest and DeepSWE step limit; Live Lite uses its 300-task
manifest, step limit 150, and official evaluator timeout. Their partial result
artifacts are intentionally independent of the final merge gate and must not
be mixed into a TB2 solve-rate claim.

No summary may infer a solve from job success, or infer an unsolved result from
missing artifacts. Missing/malformed verifier output is reported as ungraded
or infrastructure failure with its reason.
