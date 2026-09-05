# Project operating context

For HAR-83, Mini-SWE integration, GT architecture, benchmark hardening, or
performance work, read `GT_HARNESS_SESSION_HANDOFF.md` before acting.
Read `BENCHMARK_READINESS_STATUS.md` for current proof and smoke authorization.
Before any benchmark dispatch, also read and execute
`BENCHMARK_DISPATCH_CHECKLIST.md` in priority order. A later green check never
overrides an earlier red or missing check.

The shipping product is the canonical DeepSWE path:

`GitHub Actions -> pinned DeepSWE task images -> Harbor -> Mini-SWE-Agent 2.4.6 -> gt-harness -> certified Groundtruth wheel and Go producer -> task execution -> official verifier -> typed receipts`

The local `nano` CLI and historical benchmark workflows are development or compatibility surfaces, not product-acceptance evidence. Provider-free acceptance must pass before any paid smoke. A paid smoke starts with one task; the remaining cohort requires a valid one-task result and separate approval. Never run a full benchmark as part of smoke repair.

## Provider route

- The only model authorized for the HAR-83 provider-backed smoke is `deepseek/deepseek-v4-flash-0731` through OpenRouter, pinned to the `relace` provider with fallbacks disabled.
- Source its API credential from the `final_openrouter_musecontributor` entry in `C:\Users\Lenovo\Desktop\cloud_access.md` at runtime.
- The credential identifier may appear in plans and internal instructions. The credential value must never be printed, committed, uploaded, copied into a receipt, passed to task shells, or exposed to the model-visible environment.
- The paid workflow resolves its active OpenRouter route from `config/provider_route.v1.json`; do not duplicate provider/model/base-URL literals in YAML or runner defaults. The DeepSeek cohort is reported by absolute outcomes and is not a causal comparison against the retained Muse baseline.
- Every paid dispatch requires an explicit approval input recorded in the immutable run plan and final receipt. Provider availability and funding gates fail closed without logging monetary amounts.

## Existing Muse baseline evidence

- Do not rerun the GT-off baseline. The downloaded DeepSWE v1.1 result bundle is at `D:\muse-spark-1.2_DeepSWE_v1.1`.
- It contains 113 tasks with four official Muse trials each (452 trials total): 248 pass and 204 fail/error. Join comparisons by `task_name`, never row position.
- Verify the bundle through `SHA256SUMS.txt`. Anchors: `SUMMARY.json` SHA-256 `d39cc6b6fc4c1827d4aad635cc91cbb3a18ec96ca51b4beec915cd1b89b89036`; per-task comparison SHA-256 `48336a5a102242cbf9cb7a01030543f31ba28bed02d3a0a87415345cc05fd3fa`; 452-trial JSON SHA-256 `ea4f001474d37eeae1fde4ee9020f2a8834588db6fb7d33b053c75c46a1e5d02`.
- The next GT-on smoke uses the frozen 20-task subset from this Muse dataset. The local baseline is read-only comparison evidence and does not authorize a provider call.

## Current release gate

The product remains not ready until installed-bundle identity, official-verifier binding, credential isolation, task-result conservation, workflow reachability, and provider-free clean-container acceptance all pass. Missing, malformed, setup-failed, provider-failed, or unverified trials remain typed failures with unknown reward; never manufacture a score.

Do not perform GCP authentication, account switching, credential mutation, or paid provider calls while repairing or auditing these gates.
