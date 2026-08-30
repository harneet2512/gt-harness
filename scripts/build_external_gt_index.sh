#!/usr/bin/env bash
set -euo pipefail

# HAR-6A: build the accepted Groundtruth producer outside gt-harness/vendor.
# The checkout and output are disposable build artifacts; only the pinned
# executable is staged for the evaluation container.
PRODUCER_URL="https://github.com/harneet2512/groundtruth.git"
PRODUCER_COMMIT="4967e0080cef47f614b1761a3152b784c0355a30"
PRODUCER_SOURCE_TREE="d6f5ef0177ddc35c4588c919569ee918119fd0f7"
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
SOURCE_REPO="$(git -C "${SOURCE_DIR}" rev-parse --show-toplevel)"
[[ "$(git -C "${SOURCE_DIR}" rev-parse HEAD)" == "${PRODUCER_COMMIT}" ]]
[[ "$(git -C "${SOURCE_REPO}" rev-parse "${PRODUCER_COMMIT}:gt-index")" == "${PRODUCER_SOURCE_TREE}" ]]
if [[ -n "$(git -C "${SOURCE_REPO}" status --porcelain --untracked-files=all -- gt-index)" ]]; then
  echo "Groundtruth source checkout is dirty; refusing to certify it" >&2
  exit 1
fi

SOURCE_FINGERPRINT="${PRODUCER_SOURCE_TREE}"
GO_TOOLCHAIN="$(go version | awk '{print $3}')"
mkdir -p "$(dirname "${OUTPUT}")"
CGO_ENABLED=1 GOOS=linux GOARCH=amd64 go build \
  -C "${SOURCE_DIR}" -tags "${BUILD_TAGS}" -trimpath -mod=readonly \
  -ldflags "-X main.commitSHA=${PRODUCER_COMMIT} -X main.buildTimeUTC=$(date -u +%FT%TZ) -X main.sourceFingerprint=${SOURCE_FINGERPRINT} -X main.compiledBuildTags=${BUILD_TAGS} -X main.goToolchain=${GO_TOOLCHAIN} -linkmode external -extldflags \"-static\"" \
  -o "${OUTPUT}" ./cmd/gt-index/
chmod +x "${OUTPUT}"
command -v file >/dev/null
command -v readelf >/dev/null
file "${OUTPUT}" | grep -F "statically linked"
if readelf -d "${OUTPUT}" 2>&1 | grep -q '(NEEDED)'; then
  echo "gt-index contains a dynamic-library dependency" >&2
  exit 1
fi

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
