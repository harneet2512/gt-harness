# review_me_gt

Independent post-implementation, read-only review of `gt_engine_repair.md`.
Tests may write isolated artifacts. No source repair, paid/provider call, cloud
change, credential access or baseline rerun is authorized by this review.

## 1. Identify the product

Read AGENTS, current handoff, implementation plan/receipts, policy and manifest.
Record harness/producer/installed wheel/executable/Mini-SWE/parser/dense/LSP/policy
identities, actual import locations and dirty state. Different artifacts do not
prove the current product. Check credential isolation and absence of protected
identifiers/secrets in changes or artifacts. Preserve unrelated user changes.

## 2. Trace the complete native path

Follow query -> final provider request -> dispatch -> environment execution ->
observation -> GT state -> admission -> next request -> completion. Inspect unchanged
surrounding code, not only diff. At every boundary identify state owner, blocking
work, exception behavior, bytes/revisions, duplicate-action risk, observation loss
and submission authority. Reject competing freshness states, unbounded synchronous
builds, hidden extra execution and unknown upgraded to fact.

## 3. Re-run the original defects

Require RED-before/GREEN-after witnesses for dirty union, obsolete build publication,
payload loss/opposite assertion digest collision, localization/latch starvation,
new-file suppression, stale covering, pipeline/no-tests false pass, same-epoch
recovery, lexical-restricted dense recall, recipe/cache invalidation, refresh import
and native action loss under GT failure. Tests must reach the actual changed seam.

## 4. Differential native preservation

Script identical model responses for stock pinned Mini-SWE, harness GT-off, empty
GT and failed GT. Compare exact action order/count/args, cwd/env, output, IDs,
accounting, completion, workspace and patch. Inject index/query/parser/dense/LSP/
render/admission/storage failure and failure late in a multi-action response.
Inspect post-degradation provider payload/schema; a disabled flag is not proof.

## 5. Inspect deterministic context quality

For all 19 identities inspect positive, negative and stale/malformed cases where
applicable. Follow actual sent fact back to source/execution: meaningful payload,
exact support, current dependencies, relevance, honest uncertainty, compactness,
admission and stronger-fact suppression. Reject confidence-as-proof, potential
callers as certain, historical hints as requirements and path-mention causality.

## 6. Inspect graph/retrieval/storage efficiency

Base artifact immutable; overlay masks old entities; dirty changes survive overlap;
jobs bind source; all graph consumers share validation. Dense searches complete
eligible corpus, recipe/content cache binding correct, warm query no doc inference.
Candidate compression preserves semantics. Reconstruct exact requests from CAS;
check physical unique bytes. Report cold work and missing scale input separately.
Counters must reflect actual operations, not just success labels.

## 7. Audit proof matrix

Exactly 19 distinct expected identities. Separate capability execution, production,
admission, sent bytes, response and behavior. Fail zero required witnesses, legacy
bridge-only proof, mocks labeled installed execution, attempts labeled consumption.
Explicitly disabled enforcement is not a broken feature; don't force it on to
inflate coverage. Historical receipts are not retroactively upgraded.

## 8. Execute tests

With exact bound imports, run:

```text
python -m pytest tests/test_failfast_binds_to_real_paths.py
python -m pytest tests/test_gt_session.py tests/test_miniswe_agent_parity.py
python -m pytest tests/test_miniswe_runtime.py tests/test_miniswe_evidence.py
python -m pytest tests/test_miniswe_runtime_limits.py tests/test_miniswe_integration.py
python -m pytest tests/test_dense_runtime.py tests/test_contract_embeddings.py
python -m pytest tests/test_miniswe_receipt.py
python -m scripts.gt_engine_acceptance --suite all
python -m pytest
```

Run producer parser/resolution/storage Go suites and revised feature verifier on
new matrix. Record command, exit, collected tests, skips/failures and identities.
Missing dependencies is INCOMPLETE, not a successful skip.

In an isolated temporary copy perform biting mutations: remove freshness guard,
drop payload, pre-admit dedup, restrict dense pool, exit-code-only success, disable
action conservation. Each relevant regression must turn RED; restore/reconfirm.

## 9. Verdict

Write ignored artifacts plus human report: severity, exact scenario/function,
evidence/confidence, work package/feature, missing proof and required next change.

- ENGINE_REPAIR_ACCEPTED: all required engine/native/context/local runtime gates.
- REJECTED: correctness, preservation, evidence or required behavior defect remains.
- INCOMPLETE: artifact/dependency/scale/runtime proof missing.
- BENCHMARK_READINESS_PENDING: separate clean Linux/release gates still outstanding.

Never convert engine acceptance to proven solve-rate improvement. No paid smoke
until release gates and explicit one-task approval; larger comparison separately
authorized against the existing baseline joined by task_name with all trials kept.
