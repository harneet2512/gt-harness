#!/usr/bin/env bash
# Deploy the cloud coding agent, and prove what got deployed.
#
#   bash cloud/deploy.sh              # pull, rebuild, restart, verify
#   bash cloud/deploy.sh --no-pull    # skip the pull (dirty tree, local edits)
#   bash cloud/deploy.sh --sandbox    # also rebuild the sandbox image
#
# Round-2 QA lost half a day to `docker compose up -d` quietly reusing a stale
# `cloud-ui` image: the served SPA was two commits behind the server and nothing
# in the artefacts said so. Three rules follow from that, and this script is
# where they live:
#
#   1. `--build` is never optional.
#   2. Every image is stamped with the commit (BUILD_SHA -> /health, and into
#      the JS bundle via vite's `define`).
#   3. The deploy prints the commit, the served bundle name and /health, so a
#      stale deployment is visible in one screen instead of after an hour of
#      confused debugging.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

COMPOSE=(docker compose -f cloud/docker-compose.yml)
PULL=1
SANDBOX=0
for arg in "$@"; do
  case "$arg" in
    --no-pull) PULL=0 ;;
    --sandbox) SANDBOX=1 ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n== %s\n' "$*"; }

if [ "$PULL" = 1 ]; then
  say "git pull --ff-only"
  # A dirty tree (files copied onto the box, local hotfix) must not abort the
  # deploy — the rebuild is the point, the pull is a convenience.
  git pull --ff-only || echo "   pull skipped (dirty tree or no upstream)"
fi

BUILD_SHA="$(git rev-parse --short HEAD)"
export BUILD_SHA
DIRTY=""
git diff --quiet HEAD -- cloud || DIRTY=" (+ uncommitted changes under cloud/)"
say "building ${BUILD_SHA}${DIRTY}"

"${COMPOSE[@]}" up -d --build

if [ "$SANDBOX" = 1 ]; then
  say "rebuilding the sandbox image"
  "${COMPOSE[@]}" --profile build build sandbox-image
fi

say "commit"
echo "  git HEAD    : ${BUILD_SHA}${DIRTY}"
echo "  server image: $("${COMPOSE[@]}" exec -T server printenv BUILD_SHA 2>/dev/null | tr -d '\r' || echo '(unset)')"

say "served UI bundle"
"${COMPOSE[@]}" exec -T ui sh -c 'ls -1 /usr/share/nginx/html/assets/*.js 2>/dev/null | xargs -n1 basename' \
  || echo "  (could not list the bundle)"

say "GET /health"
# uvicorn needs a moment after `up -d`; without the wait this reports a
# failure for a deployment that is merely three seconds old.
HEALTH=""
for _ in $(seq 1 30); do
  HEALTH="$("${COMPOSE[@]}" exec -T server curl -fsS http://127.0.0.1:8000/health 2>/dev/null || true)"
  if [ -n "$HEALTH" ]; then break; fi
  sleep 2
done
if [ -z "$HEALTH" ]; then
  echo "  /health FAILED after 60s" >&2
  "${COMPOSE[@]}" logs --tail 40 server >&2
  exit 1
fi
echo "  $HEALTH"
case "$HEALTH" in
  *"\"commit\":\"${BUILD_SHA}\""*) ;;
  *) echo "  WARNING: /health does not report ${BUILD_SHA} — stale server image" >&2 ;;
esac

say "containers"
"${COMPOSE[@]}" ps

cat <<EOF

Deployed ${BUILD_SHA}. Check the bundle name above against the one the browser
loads: if they differ, the browser is holding a cached index.html — hard-reload.
Rebuild the sandbox image with --sandbox whenever cloud/sandbox/Dockerfile moves.
EOF
