# GroundTruth Graph Truth Audit

Subject: `79321e0da09174805a0909f69dc695dd129a5ebf`

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

Receipt: `audit/receipts/codespaces-79321e0/graph-truth.json`.
