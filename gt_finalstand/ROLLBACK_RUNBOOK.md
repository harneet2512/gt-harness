# GroundTruth Rollback Runbook

## Trigger

Rollback is required after any false replacement, raw-evidence loss, stale exact artifact, provider-byte mismatch, solve-rate regression, or suppression outside a fresh closed blocker.

## Procedure

1. Freeze the failing run manifest and provider-bound receipt chain.
2. Disable the implicated capability kill switch.
3. If isolation is uncertain, set the global GroundTruth mode to off so stock Mini-SWE Bash remains the only action path.
4. Replay the exact failing action against the frozen repository snapshot.
5. Preserve both pre-rollback and post-rollback observations and hashes.
6. Do not delete legacy artifacts needed to reconstruct the failure.

## Verification

- Stock Mini-SWE tool schema and prompt match the frozen GT-off golden.
- Unknown, stale, ambiguous, incomplete, compound, and mixed actions preserve raw behavior.
- The provider-bound request contains no bytes from the disabled capability.
- The original task can continue through ordinary Bash.
- The rollback setting is frozen in the run manifest and cannot change mid-run.

## Receipt

Record the triggering delivery ID, capability ID, configuration before and after, exact final provider-request hashes, replay result, verifier identity, and immutable workflow artifact URL. Set `rehearsed` to true only after the procedure is executed in an isolated workflow; a document-only review is insufficient.
