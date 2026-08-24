# GroundTruth Graph Truth Audit

Subject: `8931876541ec82ec96799f6c4462b5c0726e4518`

Verdict: **PASS for the bounded independent corpus; universal recall is not claimed**.

Expected sets were derived independently from frozen repository source, language
tooling, and explicit source manifests. GT output was not used to create truth.

| Metric | Result |
| --- | ---: |
| Independently specified questions/fact sets | 11 |
| Expected/true-positive relationships | 62 |
| False positives | 0 |
| False negatives | 0 |
| Precision | 1.000 |
| Recall | 1.000 |
| False-positive rate | 0.000 |
| False-negative rate | 0.000 |
| Unsupported rate | 0.000 |
| Wrong-file rate | 0.000 |
| Wrong-symbol rate | 0.000 |

The corpus spans Python, JavaScript, TypeScript, Go, Rust, and Java and covers
callers, callees, imports, re-exports, and subclasses. Static stale-edge rate is
not measured here; lifecycle tests establish zero stale sampled edges after
modify/delete operations.

Receipt: `audit/receipts/codespaces-8931876/receipts/graph-truth.json`.
