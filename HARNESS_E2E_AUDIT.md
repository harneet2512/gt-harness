# GT Harness End-to-End Audit

Subject: `8931876541ec82ec96799f6c4462b5c0726e4518`

Verdict: **PASS** through the actual `gt-harness run` / Mini-SWE treatment seam.

The clean Linux campaign performed:

`exact itsdangerous checkout -> graph build -> pinned dense build -> query ->`
`Mini-SWE observation -> source edit -> graph+dense rebuild -> same-observation`
`context -> restart -> exact persistent graph+dense reuse`.

Observed facts:

- Mini-SWE-Agent version: `2.2.8`.
- Retrieval mode: `hybrid_required`.
- Pinned model SHA-256: `564e6c65ee0c739a486702e9e3e9b33c3f697c19c34dbe886bce9eec497ce971`.
- Embedding dimension: `768`; supported source files indexed: `17`.
- Four non-empty dense queries returned 12 candidates each; `signer.py` ranked first.
- Dense source revision changed after the edit and matched the updated graph source revision.
- Raw action output was preserved.
- Updated GT evidence was attached to the same observation.
- `before_model_call` performed no late context injection.
- Restart reused the exact current graph and dense state.
- Initial/update provider context was `257/256` conservative tokens (limits `500/350`).
- Provider/network calls by graph+dense/treatment: `0/0`.
- Provider credentials inspected: `false`.

Receipt: `audit/receipts/codespaces-8931876/receipts/harness-e2e.json`.

## Live Harbor qualification

Run `32680131105` exercised the actual adapter on all 20 repair tasks: all 20
adapter receipts, GT receipts, and Mini-SWE trajectories were recovered. Fourteen
treatments built a query-ready graph+dense index and delivered 41 context packets;
six explicitly abstained. Eighteen product receipts terminated normally. Scheme
exit 137 and Corewars outer timeout left two receipts `RUNNING`, so the live run is
not an E2E PASS for its older subject SHA `b6e1609`.

The current subject adds external process/cancellation finalization, dynamic
provider/action deadlines, and trajectory-backed call accounting. These boundaries
are regression tested and the real killed Scheme receipt was replayed successfully,
but the current SHA has not received a third paid live run.
