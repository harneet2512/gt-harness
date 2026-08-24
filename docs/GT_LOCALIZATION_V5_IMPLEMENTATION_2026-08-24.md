# GT Localization v5 Implementation Receipt

Implementation SHA: `2bab25973fd1e4e90372aac30231bbbe3009b863`

Observed on `2026-08-24` in the clean 32-GB Linux Codespace
`gt-final-cert-9411ed1-5rwq4r5jjv7f4gxp`.

## Defects and fixes

| Demonstrated defect | Root cause | Production fix | Regression evidence |
| --- | --- | --- | --- |
| Boa localized runtime abort APIs instead of engine evaluation APIs | Later unqualified API clarifications rebound a qualified requested member to a global same-name symbol | Qualified owner/member identity is authoritative across the complete task | `test_qualified_api_context_prevents_unqualified_clarification_from_global_binding` |
| Long issues omitted named owners | Bounded FTS/BM25 materialization could crowd exact owners out before context compilation | Read-only SQLite exact identity seeding for syntax-marked owners and longest existing API prefixes | `test_query_builder_materializes_qualified_owner_and_existing_api_analogs` |
| Rank-window omission removed decision-grade owner facts | Context compilation trusted only statistical top-k rows even when the bounded revision-checked repository contained exact owner facts | Owner-scoped exact facts are seeded outside the statistical window, then bounded by set cover | `test_owner_scoped_exact_facts_are_seeded_when_rank_window_omits_them` |
| Same file implied false facet coverage | `_matching_facet_ids` allowed path co-location to replace a symbol match | Symbol evidence is mandatory; owner path is an additional constraint | `test_file_location_cannot_substitute_for_owner_symbol_match` |
| One candidate list conflated edit, public surface, integration, and verification | Candidate role was lost before provider compaction | `gt.agent_context.v5` keeps typed roles and reserves one provider anchor per available role | role/compaction tests in `test_product_treatments.py` and compiler tests |
| Lifecycle overstated model use | Reads, unrelated edits, and read-only validation could advance a feature | Content hashes bind `FOLLOWED`, `EDITED`, and `VALIDATED` to attributed paths and applicable post-edit checks | treatment lifecycle regressions |

## Exact archived-task replays

The task text was extracted from the first user message of each official local
Mini-SWE trajectory, truncated only before the appended `IMPORTANT:` harness
instructions, and transported byte-for-byte. Comparator patch paths were applied
only after context generation as an audit oracle.

### Awilix asynchronous container initialization

- repository commit: `82ac179c1de4c216c4e333093044fac643303f0c`
- task SHA-256: `9cace2139a042c37b0381384358e9c71642eea6b62dacaec08fe0e1b67771143`
- graph identity: `70a12d713f590ca59d5cfcfa4f9e9e3cb291e1d788be09f647dd2fa1dd25d833`
- treatment: `ACTIVE`; errors: none
- existing comparator-path recall: `4/4 = 1.0`
- delivered roles: edit `src/resolvers.ts`, edit `src/errors.ts`, public surface
  `src/awilix.ts`, integration `src/container.ts`
- full remote replay JSON SHA-256:
  `ae924514c3d341f0f3e43041987e0e8cf5ab9ea429beed25a981486880a8436e`

### Boa hierarchical evaluation cancellation

- repository commit: `70409a5052984325dccfdc5f6520818568a81f39`
- task SHA-256: `3b20d9499fd1c67d67880b92da834fc4148e441b79bd695e029fa8ef2abd5522`
- graph identity: `182142477860bb1a2ef712d502b35ae8eb74232ed2f1d263f2551cab4e103283`
- treatment: `ACTIVE`; errors: none
- existing comparator-path recall: `4/8 = 0.5` under the 500-token packet
- delivered roles: edit `core/engine/src/context/mod.rs`, edit
  `core/engine/src/module/mod.rs`, edit `core/engine/src/script.rs`, public surface
  `core/engine/src/lib.rs`, proposed new file `core/engine/src/evaluation.rs`
- wrong runtime, AST, and example targets delivered: `0`
- full remote replay JSON SHA-256:
  `9ab55a9b1c79f4727c1123392adfaf884a7280bb28355bc471528636885e4e1c`

The four undelivered existing comparator files are secondary promise, error, job,
and VM surfaces. GT does not label them edit targets without exact owner or certified
relationship evidence. This is an explicit compactness/relationship-recall limit,
not a claim of complete patch-path prediction.

## Provider-free gate

Command:

```text
pytest -q -m 'not external_evidence' --ignore=tests/test_gt_finalstand.py
```

Result: 1,956 selected; 1,954 passed; two explicitly skipped. Five
`external_evidence` tests were deselected. The historical `test_gt_finalstand.py`
suite was excluded by path because it binds expired hosted artifacts, not current
production behavior. Provider calls: zero.

## GitNexus relationship

No GitNexus source was copied. GT independently retains its own graph builder,
schema, revision receipt, dense index, Mini-SWE treatment seam, lifecycle ledger,
and benchmark attestation. Public GitNexus research influenced the emphasis on
hybrid retrieval, compact higher-order context, process views, and explicit graph
query surfaces. GT differs by fail-closed exact working-tree identity, treatment
delivery receipts, same-observation updates, feature lifecycle attribution, and a
common-scaffold benchmark product boundary.

The current result proves implementation behavior and exact regression repair. It
does not prove causal solve-rate uplift or broad competitive superiority; the
authorized official 20-task smoke is the next evidence gate.
