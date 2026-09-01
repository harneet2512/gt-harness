# GT Harness contributor contract

The product is the pinned DeepSWE → Pier/Harbor → Mini-SWE-Agent 2.4.6 → GT Harness →
Groundtruth lifecycle described in `README.md` and `archdone.md`. The `nano` CLI and historical
benchmark workflows are compatibility artifacts, not the shipping acceptance surface.

Before changing product behavior:

1. Add or identify a behavior-visible RED test.
2. Keep bare and Groundtruth installation structurally identical.
3. Preserve credential isolation and the closed GT configuration allowlist.
4. Preserve typed conservative outcomes; unknown totals stay unknown.
5. Update the bundle source manifest and architecture documentation with changed identities.
6. Run the provider-free acceptance command and relevant serial/xdist/lint checks.

Never run a provider benchmark without a separate approval receipt. Never perform GCP
authentication, account switching, credential deletion, or credential mutation. Never commit
provider, cloud, GitHub, GCP, account, project, or key material.
