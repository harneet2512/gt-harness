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

# System tools FIRST. python:3.12-slim ships neither git nor a compiler, and
# every git line below - including the safe.directory one - is a hard failure
# without git. Ordering this after them is how the first bare-image run of
# this script died on line 21.
#   gcc: scripts/red_evidence.py resolves it by name (_resolve_executable
#   ("gcc", ...)) and the capture fails with CaptureError("executable_not_found").
apt-get update -qq >/dev/null
apt-get install -y -qq gcc git >/dev/null

git config --global --add safe.directory "$ROOT"

# The runtime and test dependencies. Both product installs below are --no-deps
# (so nothing silently resolves a different version), which means nothing else
# installs these. Omitting them is not a slow failure: modules importing
# pydantic fail at COLLECTION, so the suite reports errors that look like the
# product and are the environment.
#
# First block: pyproject's own pins, copied exactly.
pip install -q \
    "pytest==9.1.1" "pytest-asyncio==1.4.0" \
    "anthropic==0.120.2" "openai==2.50.0" "pydantic==2.13.4" "rich==15.0.0" \
    "datacurve-pier==0.3.1" "harbor==0.20.0" "mini-swe-agent==2.4.6" \
    "numpy==2.5.1" "onnxruntime==1.20.1" "tokenizers==0.23.1"

# Second block: NOT in pyproject, and exact here on purpose. pytest-timeout
# parses the --timeout=900 the chunk loop relies on, so its behaviour is
# load-bearing; structlog and mcp are the groundtruth wheel's own declared
# RANGES. A range in the instrument of record is tomorrow's instrument defect -
# it resolves differently on a later day and the change is invisible. These
# versions were read from a resolved environment, not chosen: pinning them
# makes any future bump a visible edit instead of a silent resolution.
pip install -q "pytest-timeout==2.4.0" "structlog==25.5.0" "mcp==1.29.1"

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

# Name the environment in the output. A run whose setup lives partly in a
# prebuilt image rather than in this script is not reproducible FROM this
# script, and a count it produces cannot be checked by anyone else. pip check
# is here for the same reason: an unsatisfied pin is an environment failure
# that reads as a product failure.
#
# For an ACCEPTANCE-path run this line is not sufficient: python:3.12-slim
# mutates under its tag, so invoke the image by DIGEST (python@sha256:...)
# and record that digest. What is printed below identifies a tag-based
# triage run only, and must not be cited as an installed-check environment.
printf 'base: %s / %s\n' "$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release 2>/dev/null)" "$(python -V 2>&1)"
pip check
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
