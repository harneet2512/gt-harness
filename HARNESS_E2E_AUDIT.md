# GT Harness End-to-End Audit

Subject: `79321e0da09174805a0909f69dc695dd129a5ebf`

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

Receipt: `audit/receipts/codespaces-79321e0/harness-e2e.json`.
