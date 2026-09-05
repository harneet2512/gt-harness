#!/usr/bin/env bash
# End-to-end sandbox verification against a running compose deployment.
# Usage: bash cloud/sandbox/verify.sh [off|advisory]   (run on the deployment host)
#
# Deploy first, or this verifies a stale image:
#   bash cloud/deploy.sh              # pull, rebuild with --build, stamp, check
#   bash cloud/deploy.sh --sandbox    # ...and rebuild the sandbox image
set -uo pipefail
cd /workspaces/gt-harness
DC="docker compose -f cloud/docker-compose.yml"
MODE="${1:-off}"
MODEL="${MODEL:-nvidia/nemotron-3-super-120b-a12b:free}"
OUT="/tmp/verify-$MODE.log"
: > "$OUT"
say() { echo "== $*" | tee -a "$OUT"; }

TOKEN=$($DC exec -T server python -c "import jwt,os,time;print(jwt.encode({'sub':'1','login':'verify','exp':int(time.time())+7200},os.environ['JWT_SECRET'],algorithm='HS256'))" | tr -d '\r')
[ -n "$TOKEN" ] || { echo "no token"; exit 1; }
api() { $DC exec -T server curl -sS -H "Authorization: Bearer $TOKEN" "$@"; }
jqp() { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)"; }

say "deployment under test"
$DC exec -T server curl -sS http://127.0.0.1:8000/health | tee -a "$OUT"; echo | tee -a "$OUT"

say "creating session gt_mode=$MODE model=$MODEL"
ID=$(api -X POST http://127.0.0.1:8000/api/sessions -H 'Content-Type: application/json' \
  -d "{\"repo\":\"https://github.com/pallets/click\",\"ref\":\"main\",\"model\":\"$MODEL\",\"gt_mode\":\"$MODE\"}" \
  | jqp "d['id']")
say "session $ID"

for _ in $(seq 1 90); do
  ST=$(api "http://127.0.0.1:8000/api/sessions/$ID" | jqp "d['status']")
  [ "$ST" = "creating" ] || break
  sleep 5
done
say "status after create: $ST"

say "lifecycle events"
api -m 10 "http://127.0.0.1:8000/api/sessions/$ID/events?after_id=0" 2>/dev/null \
  | grep '^data:' | grep -E 'lifecycle' | tee -a "$OUT"

say "sandbox containers"
docker ps --filter "name=gt-sandbox-" --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | tee -a "$OUT"
docker inspect -f '{{.Name}} net={{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}} user={{.Config.User}} mem={{.HostConfig.Memory}} pids={{.HostConfig.PidsLimit}} binds={{.HostConfig.Binds}}' "gt-sandbox-$ID" 2>&1 | tee -a "$OUT"

if [ "$ST" != "idle" ]; then say "NOT IDLE - stopping"; exit 1; fi

PROMPT='Create a file SANDBOX.txt containing the output of "id" and "hostname", then run curl -sI https://openrouter.ai and curl -sI https://github.com and tell me exactly what each returned.'
PAYLOAD=$(python3 -c "import json,sys;print(json.dumps({'content':sys.argv[1]}))" "$PROMPT")
say "sending message"
api -X POST "http://127.0.0.1:8000/api/sessions/$ID/messages" -H 'Content-Type: application/json' -d "$PAYLOAD" >/dev/null

for _ in $(seq 1 180); do
  ST=$(api "http://127.0.0.1:8000/api/sessions/$ID" | jqp "d['status']")
  [ "$ST" = "running" ] || break
  sleep 5
done
say "status after turn: $ST"

say "agent reply"
api "http://127.0.0.1:8000/api/sessions/$ID/messages" \
  | python3 -c "
import sys,json
msgs=json.load(sys.stdin)
for m in msgs[-3:]:
    print(f\"--- {m['role']} ---\")
    print(m['content'][:4000])
" | tee -a "$OUT"

say "diff file list"
api "http://127.0.0.1:8000/api/sessions/$ID/diff" | jqp "[f['path'] for f in d['files']]" | tee -a "$OUT"

say "host workspace"
ls -l "/srv/gt-workspaces/$ID/SANDBOX.txt" 2>&1 | tee -a "$OUT"
cat "/srv/gt-workspaces/$ID/SANDBOX.txt" 2>&1 | tee -a "$OUT"

say "proxy log (last 25)"
docker logs --tail 25 gt-egress-proxy 2>&1 | tee -a "$OUT"

say "closing session"
api -X POST "http://127.0.0.1:8000/api/sessions/$ID/close" >/dev/null
sleep 3
say "sandbox containers after close (docker ps -a)"
docker ps -a --filter "name=gt-sandbox-" --format '{{.Names}}\t{{.Status}}' | tee -a "$OUT"
ls -d "/srv/gt-workspaces/$ID" 2>&1 | tee -a "$OUT"
say "SESSION_ID=$ID"
