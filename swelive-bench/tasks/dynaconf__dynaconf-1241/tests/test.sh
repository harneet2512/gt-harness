#!/bin/bash
# SWE-bench-Live verifier entrypoint (separate verifier environment).
# Mirrors microsoft/SWE-bench-Live evaluation/evaluation.py:
#   1. apply test_patch (hidden; baked into this image, never agent-visible)
#   2. apply the agent's model.patch artifact
#   3. run the instance's test_cmds, capture the log
#   4. grade with the pytest log parser -> /logs/verifier/reward.json
# /logs/verifier is bind-mounted to the host trial verifier dir by pier.
set -uo pipefail
trap 'if [ ! -f /logs/verifier/reward.json ] && [ ! -f /logs/verifier/reward.txt ]; then mkdir -p /logs/verifier; echo -1 > /logs/verifier/reward.txt; fi' EXIT
log() { echo "[verifier] $*"; }

# sweb.eval images may rely on shell profiles for env activation (conda etc.).
# `docker compose exec bash -c` skips .bashrc, so make PATH robust explicitly.
for _p in /opt/miniconda3/envs/testbed/bin /opt/conda/envs/testbed/bin \
          /opt/miniconda3/bin /opt/conda/bin /usr/local/bin /root/.local/bin; do
  if [ -d "$_p" ]; then
    case ":$PATH:" in *":$_p:"*) ;; *) PATH="$_p:$PATH" ;; esac
  fi
done
if [ -f /opt/miniconda3/etc/profile.d/conda.sh ]; then
  . /opt/miniconda3/etc/profile.d/conda.sh
  conda activate testbed >/dev/null 2>&1 || true
fi
export PATH

cd /testbed || { mkdir -p /logs/verifier; exit 6; }
# Some instances nest the repo one level below /testbed (official eval fallback).
[ -d .git ] || { g=$(find . -maxdepth 2 -mindepth 2 -type d -name .git -print -quit); [ -n "$g" ] && cd "${g%/.git}"; }
git config --global --add safe.directory '*' 2>/dev/null || true
log "repo root: $(pwd)"

mkdir -p /logs/verifier
export RUN_LOG=/logs/verifier/run.log
: > "$RUN_LOG" 2>/dev/null || true

# --- 1. Hidden test patch (official order: test_patch before model patch) ---
log "applying test_patch"
git apply --reject --whitespace=nowarn /tests/test_patch.diff >> "$RUN_LOG" 2>&1
log "test_patch apply rc=$?"

# --- 2. Agent's model patch (empty/missing => pristine tree => graded 0) ---
if [ -s /logs/artifacts/model.patch ]; then
  log "applying model.patch ($(wc -l < /logs/artifacts/model.patch) lines)"
  git apply --reject --whitespace=nowarn /logs/artifacts/model.patch >> "$RUN_LOG" 2>&1
  log "model.patch apply rc=$?"
else
  log "no model.patch artifact or empty patch; grading pristine tree"
fi

# --- 3. Instance test commands (verbatim from the dataset row) ---
set +e
bash /tests/run_tests.sh > /logs/verifier/testlog.out 2>&1
tests_rc=$?
set -e
log "run_tests rc=$tests_rc"
cat /logs/verifier/testlog.out

# --- 4. Grade ---
echo "===== grade ====="
python3 /tests/grade.py /logs/verifier/testlog.out
log "reward.json=$(cat /logs/verifier/reward.json 2>/dev/null)"
