# GroundTruth Failure Campaign

Subject: `8931876541ec82ec96799f6c4462b5c0726e4518`

Verdict: **PASS (18/18 attacks)**.

The campaign verified explicit failure/degradation or correct recovery for:

1. missing indexer binary;
2. corrupt indexer binary;
3. corrupt graph DB / wrong checksum;
4. corrupt graph receipt;
5. deleted graph cache;
6. exclusive DB lock;
7. malformed source;
8. oversized source;
9. generated source;
10. mixed-language repository;
11. process killed during update;
12. graph-build timeout;
13. unsupported-only repository;
14. linked worktree / detached HEAD;
15. Git submodule;
16. source symlink and symlink loop;
17. unreadable source permission;
18. state-directory permission denial.

No case produced apparently healthy but silently invalid intelligence. Corrupt,
deleted, and interrupted state recovered atomically; unsupported or permission
failures remained non-queryable and explicit.

Additional live failures found and repaired after this 18-case campaign:

- a musl/QEMU task could not execute the glibc-linked indexer; the workflow now
  builds and verifies a static indexer;
- process exit 137 could preserve only a `RUNNING` checkpoint; the adapter now
  atomically terminalizes it without rewriting an already terminal receipt;
- Harbor cancellation could occur while provider retries or a shell operation were
  in flight; model attempts, transport time, and actions are now bounded by actual
  remaining GT time beneath the unchanged Harbor ceiling; and
- format-error API attempts were invisible to assistant-row counting; attestation
  now uses Mini-SWE trajectory statistics.

Receipt: `audit/receipts/codespaces-8931876/receipts/failure-campaign.json`.
