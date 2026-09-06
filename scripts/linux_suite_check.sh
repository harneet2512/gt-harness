#!/bin/sh
# Run the harness test suite on Linux/Python 3.12 against the pinned producer.
#
# This exists because assembling this environment by guesswork costs six
# separate wrong answers, each of which produces a confident and wrong failure
# count. Every line below was learned by getting it wrong first; the comments
# say what breaks without it so the next reader does not have to rediscover it.
#
#   docker run --rm --memory=3g -v "<clone>:/work" -w /work \
#       python:3.12-slim sh /work/scripts/linux_suite_check.sh
#
# The clone MUST be made with core.autocrlf=false - clone inside Linux, or the
# container's git reads every CRLF working file as modified against its LF blob
# and `git diff HEAD` reports the whole tree dirty. Source-closure checks then
# fail with source_closure_differs_from_head for reasons unrelated to the code.
set -eu

ROOT="${1:-/work}"
cd "$ROOT"

git config --global --add safe.directory "$ROOT"

# gcc: scripts/red_evidence.py resolves it by name when capturing red evidence
# (_resolve_executable("gcc", ...)); python:3.12-slim ships no compiler and the
# capture fails with CaptureError("executable_not_found").
apt-get update -qq >/dev/null
apt-get install -y -qq gcc git >/dev/null

# Two wheels, not one. The command worker is launched as
#   python -I -m scripts.miniswe_supervisor
# from the TASK directory. -I drops cwd, PYTHONPATH and user-site from
# sys.path, so the product must be importable from site-packages or the worker
# cannot start and every contained command raises
# command_descendant_receipt_missing. Installing it is a precondition for that
# code path to run at all, not a convenience.
pip install --no-deps -q vendor/groundtruth_mcp-1.0.0-py3-none-any.whl
pip install --no-deps -q .

# The producer binary. groundtruth/_binary.py ensure_binary() serves this cache
# first and otherwise fetches
#   https://github.com/harneet2512/groundtruth/releases/download/v1.1.0/...
# which does not exist, so the graph-backed tests die on HTTP 404 rather than
# on anything they are testing. The vendored binary is the pinned one:
# its sha256 matches vendor/gt-index-linux-amd64.build-info.json and
# config/deepswe_product_bundle_v1.json groundtruth.producer_sha256.
mkdir -p "$HOME/.groundtruth/bin/v1.1.0"
cp vendor/gt-index-linux-amd64 "$HOME/.groundtruth/bin/v1.1.0/gt-index"
chmod +x "$HOME/.groundtruth/bin/v1.1.0/gt-index"

# Absolute, not relative. The pre-commit construction gate resolves the common
# repository hooks path and refuses a relative one with
# "core.hooksPath is not the common repository .githooks".
git config core.hooksPath "$ROOT/.githooks"

printf 'instrument: gcc=%s producer=%s hooks=%s\n' \
    "$(command -v gcc)" \
    "$(sha256sum "$HOME/.groundtruth/bin/v1.1.0/gt-index" | cut -c1-16)" \
    "$(git config --path --get core.hooksPath)"
printf 'tree clean vs HEAD: %s modified\n' "$(git diff --name-only HEAD | wc -l)"

# Chunked so peak memory stays bounded; the whole suite in one process is the
# difference between finishing and being OOM-killed on a small host.
ls tests/test_*.py | sort > /tmp/suite_files
split -n l/4 -d /tmp/suite_files /tmp/suite_chunk
for chunk in /tmp/suite_chunk*; do
    printf '===== %s =====\n' "$chunk"
    # shellcheck disable=SC2046
    python -m pytest $(tr '\n' ' ' < "$chunk") \
        -q --timeout=900 -p no:randomly -p no:cacheprovider \
        --continue-on-collection-errors 2>&1 | grep -E '^(FAILED|ERROR)' || true
done

# Read this before trusting any count above. pytest emits its summary only at
# the end, so a log grepped mid-run reports zero failures and every baseline
# failure looks fixed.
echo "ALL CHUNKS DONE"
