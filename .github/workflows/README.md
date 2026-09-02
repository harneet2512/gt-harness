# Active workflows

The supported workflow set is exactly:

- `deepswe_gt_harness_product.yml`, the canonical provider-free acceptance workflow. It has no
  secrets, provider calls, benchmark calls, paid requests, or GCP actions. It consumes
`config/deepswe_product_bundle_v1.json`, runs the same acceptance code as the documented local
command, validates workflow reachability, asserts zero-spend receipts, and uploads the closeout.
- `deepswe_gt_harness_product_p0731.yaml`, the approval-gated paid smoke wrapper. It depends on the
  provider-free workflow and cannot dispatch paid tasks without its explicit approval inputs and
  evidence gates.

Historical Terminal-Bench, SWE-bench, live-smoke, release, and provider workflows remain available
in Git history. Their presence in history does not make them active or supported.
