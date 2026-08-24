# GroundTruth Language Support Audit

Subject: `79321e0da09174805a0909f69dc695dd129a5ebf`

Verdict: **PASS for six structural graph languages with declared parser limits**.

| Language | Repository | Cold state | Add/modify/delete | Stale sampled edges | Declared limits |
| --- | --- | --- | --- | ---: | --- |
| Python | itsdangerous | READY | PASS | 0 | None in fixture |
| JavaScript | Express | READY_WITH_DECLARED_LIMITATIONS | PASS | 0 | 3 recovered template syntax regions |
| TypeScript | Redux | READY_WITH_DECLARED_LIMITATIONS | PASS | 0 | 5 recovered TSX/type regions; 1 oversized file |
| Go | gorilla/mux | READY | PASS | 0 | None in fixture |
| Rust | ripgrep | READY_WITH_DECLARED_LIMITATIONS | PASS | 0 | 2 recovered non-Rust script regions; 1 non-regular file |
| Java | Gson | READY_WITH_DECLARED_LIMITATIONS | PASS | 0 | 3 recovered protobuf regions; 1 unresolved language path |

All languages use the same graph build, persistence, identity, modification,
deletion, and warm-reuse gates. The higher semantic-graph layer currently emits
Python AST facts only; other languages retain structural graph intelligence and
explicitly declare the semantic-language limitation.

Receipt: `audit/receipts/codespaces-79321e0/language-lifecycle.json`.
