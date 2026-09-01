# Active workflow

`deepswe_gt_harness_product.yml` is the only supported workflow. It is provider-free: no secrets,
provider calls, benchmark calls, paid requests, or GCP actions. It consumes
`config/deepswe_product_bundle_v1.json`, runs the same acceptance code as the documented local
command, validates workflow reachability, asserts zero-spend receipts, and uploads the closeout.

Historical Terminal-Bench, SWE-bench, live-smoke, release, and provider workflows remain available
in Git history. Their presence in history does not make them active or supported.
