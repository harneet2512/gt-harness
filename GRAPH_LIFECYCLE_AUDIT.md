# GroundTruth Graph Lifecycle Audit

Subject: `79321e0da09174805a0909f69dc695dd129a5ebf`

Verdict: **PASS (9/9 phases)**.

| Phase | Result | Required invariant |
| --- | --- | --- |
| Cold start | PASS | No state to exact-revision READY graph |
| Warm start | PASS | Same identity and query results reused |
| New file | PASS | New nodes/relations appear |
| Modified file | PASS | New relations appear and stale edges disappear |
| Renamed file | PASS | Old identity disappears; new identity appears |
| Deleted file | PASS | Nodes and incident stale edges disappear |
| Commit change | PASS | New commit/source identity replaces prior graph |
| Restart during build | PASS | Partial state non-queryable; atomic recovery succeeds |
| Concurrent reads/update | PASS | Readers observe explicit FAILED/STALE during transition and deterministic READY afterward |

Updates deliberately use atomic full rebuilds until file-keyed incremental
relationship parity is proven. That is slower than an incremental writer but
correct: no SHA-A graph is served as current for SHA B.

Receipt: `audit/receipts/codespaces-79321e0/graph-lifecycle.json`.
