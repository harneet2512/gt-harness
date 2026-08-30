#!/usr/bin/env bash
set -euo pipefail

# HAR-6A: build the accepted Groundtruth producer outside gt-harness/vendor.
# The checkout and output are disposable build artifacts; only the pinned
# executable is staged for the evaluation container.
PRODUCER_URL="https://github.com/harneet2512/groundtruth.git"
PRODUCER_COMMIT="4967e0080cef47f614b1761a3152b784c0355a30"
BUILD_TAGS="sqlite_fts5"
WORK_ROOT="${GT_INDEX_BUILD_ROOT:-${RUNNER_TEMP:-/tmp}/groundtruth-${PRODUCER_COMMIT}}"
SOURCE_ROOT="${WORK_ROOT}/source"
SOURCE_DIR="${GT_INDEX_SOURCE_DIR:-${SOURCE_ROOT}/gt-index}"
OUTPUT="${GT_INDEX_BINARY_OUTPUT:-${WORK_ROOT}/gt-index-linux-amd64}"

mkdir -p "${WORK_ROOT}"
if [[ -z "${GT_INDEX_SOURCE_DIR:-}" ]]; then
  if [[ ! -d "${SOURCE_ROOT}/.git" ]]; then
    git clone --no-checkout "${PRODUCER_URL}" "${SOURCE_ROOT}"
  fi
  git -C "${SOURCE_ROOT}" fetch --no-tags origin "${PRODUCER_COMMIT}"
  git -C "${SOURCE_ROOT}" checkout --detach --force "${PRODUCER_COMMIT}"
fi
[[ "$(git -C "${SOURCE_DIR}" rev-parse HEAD)" == "${PRODUCER_COMMIT}" ]]

SOURCE_FINGERPRINT="$({
  cd "${SOURCE_DIR}"
  find . -type f \( -name '*.go' -o -name '*.c' -o -name '*.cc' -o -name '*.cpp' \
    -o -name '*.h' -o -name '*.hpp' -o -name '*.s' -o -name 'go.mod' -o -name 'go.sum' \) \
    -print0 | sort -z | xargs -0 sha256sum
} | sha256sum | awk '{print $1}')"
GO_TOOLCHAIN="$(go version | awk '{print $3}')"
mkdir -p "$(dirname "${OUTPUT}")"
CGO_ENABLED=1 GOOS=linux GOARCH=amd64 go build \
  -C "${SOURCE_DIR}" -tags "${BUILD_TAGS}" -trimpath -mod=readonly \
  -ldflags "-X main.commitSHA=${PRODUCER_COMMIT} -X main.buildTimeUTC=$(date -u +%FT%TZ) -X main.sourceFingerprint=${SOURCE_FINGERPRINT} -X main.compiledBuildTags=${BUILD_TAGS} -X main.goToolchain=${GO_TOOLCHAIN}" \
  -o "${OUTPUT}" ./cmd/gt-index/
chmod +x "${OUTPUT}"

GT_INDEX_BINARY="${OUTPUT}" python3 - "${OUTPUT}" <<'PY'
import hashlib
import json
import os
import subprocess
import sys

binary = sys.argv[1]
info = json.loads(subprocess.check_output([binary, "-build-info"], text=True))
assert info["schema"] == "gt-index.build.v1"
assert info["complete"] is True
assert info["git_commit"] == "4967e0080cef47f614b1761a3152b784c0355a30"
assert "sqlite_fts5" in info["build_tags"]
assert info["graph_schema_version"] == "v15.2-trust-tier"
assert "call_resolution_v2" in info["capabilities"]
assert info["executable_sha256"] == hashlib.sha256(open(binary, "rb").read()).hexdigest()
print(json.dumps({"binary": os.path.realpath(binary), "build_info": info}, sort_keys=True))
PY
echo "GT_INDEX_BINARY_HOST=${OUTPUT}"
