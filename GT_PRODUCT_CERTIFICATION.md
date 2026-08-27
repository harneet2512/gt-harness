# GroundTruth Product Certification

Verdict: `NOT_CERTIFIED`

Last exact hosted subject: `73217b6f42d0ec5678fe140077d06b9ef5bd0227`.

Hosted provider-free run
[`33105512694`](https://github.com/harneet2512/gt-harness/actions/runs/33105512694)
passed clean install, doctor, Python, Go, canonical lint, built product surface,
CLI lifecycle, real-repository matrix, graph truth, graph lifecycle, language
lifecycle, pinned dense provisioning, harness E2E, and the failure campaign.
It failed localization truth and the fail-closed product certifier. Provider
calls were zero and provider credentials were not inspected.

The failure was real. A clean install selected py-tree-sitter `0.26.0`, whose
`Point` borrowed-reference defect produced corrupted line objects and seven
signal-11 worker exits. Eight completed tasks also had implementation-owner
precision `0.4783` because cross-role paths and repeated unbound rank-only
owners inflated provider context. Therefore the previous
`CERTIFIED_WITH_DECLARED_LIMITATIONS` verdict is superseded.

The current unhosted candidate repairs the demonstrated causes:

- pins and receipts py-tree-sitter `0.25.2`;
- fails semantic graph construction before parsing under a drifted runtime;
- tests a real parser point above source line 256;
- keeps implementation, public, integration, test, and new-file roles disjoint;
- emits one representative for unbound same-role rank evidence while retaining
  distinct typed multi-file requirements;
- prevents generic prose/path nouns from manufacturing owner identity; and
- gives complete adjacent artifact phrases precedence over incidental helper
  symbol overlap.

Local production-path witnesses for ABS, Actionlint, and fd have implementation
precision, implementation fact recall, and required coverage of `1.0`, with no
treatment or dense-readiness failures. Adaptix retains an honest two-path exact
ambiguity set: recall and coverage are `1.0`, precision is `0.5`, and neither
candidate receives edit authority. The complete Python suite, Go SQLite-FTS5
suite, wheel/product surface, CLI lifecycle, canonical lint, changed-file Ruff,
and diff integrity pass locally.

Certification remains blocked until an immutable candidate SHA passes the
complete `prerelease_product_matrix.yml` Linux campaign, including all twenty
provider-free localization cases and the product certifier. No paid agent
benchmark is authorized.
