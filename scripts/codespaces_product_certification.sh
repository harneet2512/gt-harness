#!/usr/bin/env bash
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel)"
OUTPUT_ROOT="${1:-$(mktemp -d -t gt-codespaces-certification-XXXXXX)}"
WORKSPACE="$OUTPUT_ROOT/workspace"
RECEIPTS="$OUTPUT_ROOT/receipts"
LOGS="$OUTPUT_ROOT/logs"
STEPS="$OUTPUT_ROOT/steps.tsv"
MODEL_DIR="$OUTPUT_ROOT/models/snowflake-arctic-embed-m"
SNOWFLAKE_MODEL_SHA256="564e6c65ee0c739a486702e9e3e9b33c3f697c19c34dbe886bce9eec497ce971"
SNOWFLAKE_TOKENIZER_SHA256="91f1def9b9391fdabe028cd3f3fcc4efd34e5d1f08c3bf2de513ebb5911a1854"

mkdir -p "$WORKSPACE" "$RECEIPTS" "$LOGS"
: >"$STEPS"

run_step() {
  local name="$1"
  shift
  local started completed status
  started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if "$@" >"$LOGS/$name.log" 2>&1; then
    status="PASS"
  else
    status="FAIL"
  fi
  completed="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '%s\t%s\t%s\t%s\n' "$name" "$status" "$started" "$completed" >>"$STEPS"
  printf '{"step":"%s","status":"%s"}\n' "$name" "$status"
  test "$status" = "PASS"
}

finalize() {
  local shell_status="$1"
  python - "$ROOT" "$OUTPUT_ROOT" "$shell_status" <<'PY'
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
output_root = Path(sys.argv[2])
shell_status = int(sys.argv[3])
steps = []
steps_path = output_root / "steps.tsv"
if steps_path.exists():
    for line in steps_path.read_text(encoding="utf-8").splitlines():
        name, status, started, completed = line.split("\t")
        steps.append(
            {
                "name": name,
                "status": status,
                "started": started,
                "completed": completed,
                "log": str(output_root / "logs" / f"{name}.log"),
            }
        )

def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, encoding="utf-8"
    ).strip()

receipt = {
    "schema": "gt.codespaces_product_certification.v1",
    "completed": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "status": "PASS"
    if shell_status == 0 and steps and all(s["status"] == "PASS" for s in steps)
    else "FAIL",
    "repository": git("config", "--get", "remote.origin.url"),
    "commit_sha": git("rev-parse", "HEAD"),
    "branch": git("branch", "--show-current"),
    "working_tree_state": "clean" if not git("status", "--porcelain") else "dirty",
    "platform": platform.platform(),
    "python": platform.python_version(),
    "provider_calls": 0,
    "provider_credentials_inspected": False,
    "steps": steps,
    "receipts": sorted(str(path) for path in (output_root / "receipts").glob("*.json")),
}
(output_root / "codespaces-product-certification.json").write_text(
    json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps({"status": receipt["status"], "output_root": str(output_root)}))
PY
}

trap_status=0
trap 'trap_status=$?; finalize "$trap_status"; exit "$trap_status"' EXIT

cd "$ROOT"

run_step install python -m pip install -e '.[dev,eval,gt,retrieval]'
run_step doctor gt-harness doctor
run_step python_tests python -m pytest -q -m 'not external_evidence'
run_step go_tests bash -c 'cd vendor/gt-index-src && go test ./...'
run_step canonical_lint python -m ruff check \
  gt_engine/repository_graph_service.py \
  gt_harness \
  scripts/product_repository_matrix.py \
  scripts/graph_truth_audit.py \
  scripts/graph_lifecycle_campaign.py \
  scripts/language_lifecycle_matrix.py \
  scripts/harness_real_repository_campaign.py \
  scripts/failure_campaign.py \
  gt_harness/product_certification.py \
  tests/test_repository_graph_service.py \
  tests/test_product_repository_matrix.py \
  tests/test_product_certification.py \
  tests/test_miniswe_product_runner.py

run_step repository_matrix python scripts/product_repository_matrix.py \
  --workspace "$WORKSPACE" \
  --output "$RECEIPTS/real-repository-matrix.json" \
  --timeout 1200 \
  --query-repetitions 10 \
  --warm-repetitions 10

run_step graph_truth python scripts/graph_truth_audit.py \
  --workspace "$WORKSPACE" \
  --output "$RECEIPTS/graph-truth.json"

run_step graph_lifecycle python scripts/graph_lifecycle_campaign.py \
  --source-repository "$WORKSPACE/repositories/python-small-itsdangerous" \
  --commit 672971d66a2ef9f85151e53283113f33d642dabd \
  --run-dir "$WORKSPACE/lifecycle-run" \
  --output "$RECEIPTS/graph-lifecycle.json"

run_step language_lifecycle python scripts/language_lifecycle_matrix.py \
  --workspace "$WORKSPACE" \
  --run-dir "$WORKSPACE/language-lifecycle-run" \
  --output "$RECEIPTS/language-lifecycle.json" \
  --timeout 1200

run_step dense_model bash -c '
  set -euo pipefail
  mkdir -p "$1"
  gh release download gt-retrieval-runtime-v1 \
    --repo harneet2512/gt-harness \
    --pattern model.onnx --pattern tokenizer.json --pattern manifest.json \
    --dir "$1"
  echo "$2  $1/model.onnx" | sha256sum -c -
  echo "$3  $1/tokenizer.json" | sha256sum -c -
' _ "$MODEL_DIR" "$SNOWFLAKE_MODEL_SHA256" "$SNOWFLAKE_TOKENIZER_SHA256"

run_step harness_e2e python scripts/harness_real_repository_campaign.py \
  --source-repository "$WORKSPACE/repositories/python-small-itsdangerous" \
  --commit 672971d66a2ef9f85151e53283113f33d642dabd \
  --run-dir "$WORKSPACE/harness-run" \
  --output "$RECEIPTS/harness-e2e.json" \
  --dense-model-dir "$MODEL_DIR"

run_step failure_campaign python scripts/failure_campaign.py \
  --source-repository "$WORKSPACE/repositories/python-small-itsdangerous" \
  --commit 672971d66a2ef9f85151e53283113f33d642dabd \
  --large-repository "$WORKSPACE/repositories/python-large-django" \
  --run-dir "$WORKSPACE/failure-run" \
  --output "$RECEIPTS/failure-campaign.json"

# Materialize the complete wrapper, then exercise the public verifier against
# that exact evidence bundle.  The EXIT trap rewrites the wrapper afterward so
# the final receipt also records this step.
finalize 0
run_step product_certifier gt-harness certify \
  --receipt-dir "$OUTPUT_ROOT" \
  --root "$ROOT" \
  --expected-commit "$(git rev-parse HEAD)" \
  --output "$RECEIPTS/product-certification.json"
