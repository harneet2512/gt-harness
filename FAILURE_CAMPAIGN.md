# GroundTruth Failure Campaign

Subject: `79321e0da09174805a0909f69dc695dd129a5ebf`

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

Receipt: `audit/receipts/codespaces-79321e0/failure-campaign.json`.
