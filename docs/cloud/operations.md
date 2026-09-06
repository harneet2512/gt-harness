# Operations runbook

For the deployment at `645fe276` on `cloud/internal-harness`. The primary
deployment is a GitHub Codespace; the same compose file runs unchanged on a
plain VM.

- [Prerequisites](#prerequisites)
- [The GitHub OAuth app](#the-github-oauth-app)
- [Environment variables](#environment-variables)
- [Deploying](#deploying)
- [Proving what is deployed](#proving-what-is-deployed)
- [Codespaces specifics](#codespaces-specifics)
- [Images](#images)
- [Restart and health policy](#restart-and-health-policy)
- [The idle TTL reaper](#the-idle-ttl-reaper)
- [Quotas and the disk floor](#quotas-and-the-disk-floor)
- [Logs and inspection](#logs-and-inspection)
- [Verifying a deployment](#verifying-a-deployment)
- [Failure modes and recovery](#failure-modes-and-recovery)
- [The database is dropped on a schema bump](#the-database-is-dropped-on-a-schema-bump)

---

## Prerequisites

| Requirement | Why |
|---|---|
| Docker with a running daemon | The stack is compose; `SANDBOX_MODE=docker` also needs the server to drive the **host** daemon through the bind-mounted socket. |
| 4 vCPU / 16 GB, ~15 GB usable disk | `.devcontainer/devcontainer.json` declares `hostRequirements: {cpus: 4, memory: "16gb"}`. Three long-lived containers plus one sandbox and one full repo clone per session. |
| A GitHub OAuth App | Sign-in. See below. |
| A provider API key | `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY`, and `OPENAI_BASE_URL` for an OpenAI-compatible gateway. |
| Python 3.12 | `pyproject.toml` sets `requires-python = ">=3.12"`. The 3.11 CI leg was removed for that reason (`f6c393d0`). |
| Node 20 | The UI image and CI build with it. |

Python extras: `pip install -e ".[cloud,miniswe]"` for the server and tests;
`".[cloud,miniswe,gt]"` (what the image installs) adds GroundTruth.

---

## The GitHub OAuth app

At [GitHub → Developer settings → OAuth Apps](https://github.com/settings/developers):

| Field | Value |
|---|---|
| Homepage URL | the origin the browser will use |
| Authorization callback URL | that origin + `/auth/callback` |

The callback host must match the forwarded origin **exactly**. On Codespaces
that is `https://<codespace-name>-80.app.github.dev/auth/callback`, and a
codespace recreated under a new name gets a new hostname — one more reason a
plain VM with a stable DNS name is the end state.

Because the UI is same-origin behind nginx, `UI_ORIGIN=/` is correct. Setting it
to a `localhost` URL sends the browser off the deployment after login.

Restrict who may sign in with `ALLOWED_GITHUB_LOGINS`.

---

## Environment variables

Every variable the server reads, with its default and what it gates. The
canonical copy with prose is [`cloud/.env.example`](../../cloud/.env.example);
compose sets some of them itself in
[`cloud/docker-compose.yml`](../../cloud/docker-compose.yml).

### Authentication

| Variable | Default | Gates |
|---|---|---|
| `GITHUB_CLIENT_ID` | *(empty)* | `/auth/login`; a 500 without it. |
| `GITHUB_CLIENT_SECRET` | *(empty)* | The token exchange at `/auth/callback`. |
| `JWT_SECRET` | `dev-secret-change-me` | Signing and verifying every session token. **Change it.** |
| `JWT_TTL_SECONDS` | `86400` | How long a signed-in session lasts (min 60). There is no revocation list, so this is also how long a removed user keeps access when the allow-list is not used. |
| `ALLOWED_GITHUB_LOGINS` | *(empty)* | Comma-separated logins. Empty means anybody with a token signed by `JWT_SECRET`. Checked at the callback **and on every authenticated request**. |
| `UI_ORIGIN` | `/` | Where `/auth/callback` redirects the browser. |
| `CORS_ORIGINS` | *(empty)* | Comma-separated origins. Empty adds **no CORS middleware at all**, which is correct for a same-origin UI. Never `*` — these endpoints are credentialed. |

### Model

| Variable | Default | Gates |
|---|---|---|
| `OPENAI_BASE_URL` | *(unset)* | An OpenAI-compatible gateway. When set, a model name is prefixed `openai/` and `api_base` is passed to LiteLLM. |
| `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY` | *(empty)* | Provider credentials. Read by the **server process only**; they never enter a sandbox or a template variable. |
| `MODEL_PREFLIGHT` | `1` | One 1-token completion at session creation, so an unusable model is a 400 in ~3 s rather than a dead session after 250 s. `0` disables it (tests, air-gapped). |
| `MODEL_REQUEST_TIMEOUT` | `300` | Seconds per model call. LiteLLM's retries are pinned off in both places (`num_retries` and `max_retries`), so this is the real ceiling on one call — and, because a model call is not cancellable, on how long `/stop` can take while the model is thinking. |
| `MSWEA_COST_TRACKING` | forced to `ignore_errors` in `app.py` | Not a knob. LiteLLM aborts a run it cannot price, and the free models have no price entry. Consequence: `cost` is always 0.0. |

### Budgets and concurrency

| Variable | Default | Gates |
|---|---|---|
| `TURN_WALL_SECONDS` | `900` | Per-turn wall clock. A session may override with `wall_seconds` (60..3600) at creation. `0` disables. |
| `MAX_CONTEXT_CHARS` | `240000` | Past this the oldest tool observations collapse to `[truncated N chars]`; user messages and agent replies are never touched. |
| `MAX_CONCURRENT_SESSIONS` | `3` | Agent turns running at once. Over it, a message is 429. |
| `MAX_CONCURRENT_CREATIONS` | `3` | Sessions cloning + GT-indexing at once. Creation is as expensive as a turn and used to take no slot at all (HAR-84 G-21). |
| `MAX_WORKERS_PER_SESSION` | `4` | Live workers per session. A spawn over it is 429 and creates nothing. One `/agents` call carries at most 4 tasks regardless. The real ceiling is the smallest of this, `MAX_CONCURRENT_CREATIONS` and `MAX_CONCURRENT_SESSIONS`. |
| `SESSION_IDLE_TTL_SECONDS` | `21600` (6 h) | How long a session may sit `idle` before the reaper closes it exactly like `/close`. `0` disables the reaper. |
| `SESSION_REAP_INTERVAL_SECONDS` | `300` | How often the reaper looks. Never zero. |
| `SSE_HEARTBEAT_SECONDS` | `15` | Comment heartbeat on an idle stream. |

### Storage

| Variable | Default | Gates |
|---|---|---|
| `DB_PATH` | `cloud_harness.db` (compose: `/app/data/cloud_harness.db`) | The SQLite store. |
| `WORKSPACES_DIR` | `./workspaces` | One clone per session. |
| `WORKSPACES_HOST_DIR` | `/srv/gt-workspaces` | **Compose only.** The host directory, mounted into the server at the *same absolute path* and used as `WORKSPACES_DIR`, because the daemon resolves a sandbox's bind mount on the host. |
| `WORKSPACES_MIN_FREE_MB` | `2048` | Free space below which a **new** session is refused outright, with a readable reason. `0` disables. |
| `SANDBOX_WORKSPACE_MAX_MB` | `2048` | Per-session workspace cap, measured with `du -sm` after write-shaped commands. Over it the command is killed and the turn ends `error`. `0` disables. |

### Sandboxing and egress

| Variable | Default | Gates |
|---|---|---|
| `SANDBOX_MODE` | `local` (compose sets `docker`) | `local` runs commands in the server's own machine account. `docker` gives every session a container. |
| `SANDBOX_IMAGE` | `gt-sandbox:latest` | The per-session image. |
| `SANDBOX_NETWORK` | `gt-sandbox-net` | The `--internal` network sandboxes join. |
| `SANDBOX_PROXY_CONTAINER` | `gt-egress-proxy` | Proxy container name and network alias. |
| `SANDBOX_PROXY_IMAGE` | `gt-egress-proxy:latest` | Used only when the proxy is not already running (local dev, integration tests). |
| `SANDBOX_PROXY_URL` | `http://gt-egress-proxy:3128` | What `HTTP_PROXY`/`HTTPS_PROXY` are set to inside a container. |
| `SANDBOX_ALLOW_REGISTRIES` | `1` | Adds `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org` to the allow-list. |
| `SANDBOX_EGRESS_ALLOW` | *(empty)* | Extra hosts, comma-separated; `*.example.com` wildcards supported. |
| `SANDBOX_MEMORY` / `SANDBOX_CPUS` / `SANDBOX_PIDS_LIMIT` / `SANDBOX_TMPFS_SIZE` | `2g` / `2` / `512` / `512m` | Per-container resource caps. |
| `SANDBOX_ENV_PASSTHROUGH` | *(empty)* | Extra environment variable **names** forwarded into an exec. Subject to the credential scrub regardless of being listed. |
| `DOCKER_BINARY` | `docker` | The CLI to shell out to. There is deliberately no docker SDK dependency. |

### GroundTruth — leave these unset

| Variable | Why unset |
|---|---|
| `GT_PRODUCER_ARTIFACT` | Pins `gt-index` to one exact certified benchmark artifact. The cloud image deliberately ships a source-built, patched producer, so setting this makes `_binary_certification()` fail closed and **every** session degrades to `gt_unavailable`. |
| `GT_TASK_ID`, `GT_PRODUCT_SOURCE_SHA` | Setting only one puts `gt_engine` in `benchmark_invalid` scope and indexing refuses with `GT_INDEX_IDENTITY_INVALID`. Setting both makes a failed index raise `BenchmarkGraphRequired` instead of degrading. Cloud sessions want `local_unbound` — correct-or-quiet. |

`GT_INDEX_BINARY` is set to `/usr/local/bin/gt-index` by the image, and
`GT_STATE_DIR` is read by the HAR-86 snapshot exclusion when it is set.

### Build

| Variable | Default | Gates |
|---|---|---|
| `BUILD_SHA` | `unknown` / `dev` | Stamped into both images by `deploy.sh`; returned by `/health` and baked into the JS bundle by vite's `define`. |

---

## Deploying

Use the script. It exists because round-2 QA lost half a day to `docker compose
up -d` quietly reusing a stale `cloud-ui` image — the served SPA was two commits
behind the server and nothing said so.

```bash
bash cloud/deploy.sh              # pull, rebuild with --build, restart, verify
bash cloud/deploy.sh --no-pull    # skip the pull (dirty tree, local hotfix)
bash cloud/deploy.sh --sandbox    # also rebuild the sandbox image
```

Three rules it enforces:

1. `--build` is never optional.
2. Every image is stamped with the commit.
3. The deploy prints the commit, the served bundle name and `/health`, so a
   stale deployment is visible in one screen.

By hand:

```bash
export BUILD_SHA="$(git rev-parse --short HEAD)"
docker compose -f cloud/docker-compose.yml up -d --build
docker compose -f cloud/docker-compose.yml --profile build build sandbox-image
curl -s localhost/health
```

`deploy.sh` waits up to 60 s for `/health` (uvicorn needs a moment after `up
-d`), dumps the last 40 server log lines and exits 1 if it never answers, and
warns if `/health`'s `commit` does not match the built SHA. A dirty tree does
not abort it — the rebuild is the point, the pull is a convenience — and the
`(+ uncommitted changes under cloud/)` marker says when the tree was dirty.

Rebuild the sandbox image with `--sandbox` whenever `cloud/sandbox/Dockerfile`
moves.

---

## Proving what is deployed

Four things must agree. If any disagrees, something is stale:

| Signal | Where |
|---|---|
| `git rev-parse --short HEAD` | The box's checkout. |
| `/health` → `{"status":"ok","commit":"<sha>"}` | The server image. |
| `build <sha>` on the sign-in card and in the session switcher | The JS bundle. |
| The bundle filename under `/usr/share/nginx/html/assets/` | Whether the browser has a cached `index.html` — hard-reload if it did not change after a UI edit. |

---

## Codespaces specifics

Rationale, cost and the plain-VM delta:
[`docs/cloud-vm-substrate.md`](../cloud-vm-substrate.md).

### Creating

```bash
gh codespace create -R harneet2512/gt-harness -b cloud/internal-harness -m standardLinux32gb
NAME=$(gh codespace list --json name,repository -q '.[0].name')
gh codespace ssh -c "$NAME"
```

`.devcontainer/devcontainer.json` is what makes that work:

```json
{
  "name": "gt-cloud-agent",
  "image": "mcr.microsoft.com/devcontainers/universal:2",
  "features": { "ghcr.io/devcontainers/features/docker-in-docker:2": {} },
  "forwardPorts": [80, 8000],
  "portsAttributes": {
    "80": { "label": "GT Cloud Agent (UI + API)", "onAutoForward": "silent" },
    "8000": { "label": "API (direct)", "onAutoForward": "silent" }
  },
  "hostRequirements": { "cpus": 4, "memory": "16gb" }
}
```

- **docker-in-docker is not optional**: the server manages one sandbox container
  per session through the Docker socket.
- `hostRequirements` is what makes the create pick a 4-core SKU rather than the
  2-core default.
- `forwardPorts` makes the ports exist from creation. Without it, a stack started
  over SSH with no VS Code client attached is never *observed*, and
  `https://<name>-80.app.github.dev` returns **404 with an empty body** — that is
  the Codespaces edge saying the port is not registered, not nginx failing.

### Port visibility resets on every deploy

Recreating the compose containers re-registers ports 80/8000 with the tunnel at
their default **private** visibility, and the public URL then 302s to GitHub
sign-in. After **every** deploy:

```bash
gh codespace ports visibility 80:public -c <codespace-name>
gh codespace ports -c <codespace-name>          # confirm
```

Run it from a machine with a `codespace`-scoped `gh` login — the codespace's own
token lacks the scope. `deploy.sh` prints the reminder at the end.

As an interim for a codespace already running without `forwardPorts`:

```bash
gh codespace ports forward 80:18080 -c "$NAME" &   # then use localhost:18080
```

Expect it to drop (`websocket: close 1006 (abnormal closure)`) on a flaky link.
It is the interim, not the mechanism.

### A tunnel-id URL goes stale, and a stale one can answer as somebody else

Every start of a codespace creates a **new** dev tunnel with a new id, and the
old id does not simply stop resolving — it can later answer as a different
tunnel. Seen 2026-09-06: requests to a tunnel URL from twenty minutes earlier
returned `200` on one route and `404` on another while **the server logged
nothing at all**, which is what a request reaching a different tunnel looks
like from the outside.

Two consequences worth knowing before debugging anything else:

- **When the app answers but its log is silent, you are talking to the wrong
  tunnel.** Compare `gh api 'user/codespaces/<name>?internal=true'
  --jq .connection.tunnelProperties.tunnelId` against the host you are using
  before believing any other symptom.
- **`PUBLIC_BASE_URL` must be re-set after every restart** while the deployment
  is reached by tunnel id, because it is what the server puts in the
  `ingest_url` handed to somebody's laptop. A stale one sends an external
  agent's events into the void, or worse, at a host that is no longer yours.

This is a consequence of using the tunnel id at all, which is itself the
workaround for the codespace name not resolving. A plain VM with a stable DNS
name retires the whole class of problem.

### A new codespace comes up with no docker and no ports (recovery container)

Seen 2026-09-06, both `EastUs` and `WestUs2`, on two different base images:
`gh codespace create` reports success and the codespace reaches *Available*,
but `docker` is missing, `gh codespace ports` lists nothing, and
`/workspaces/.codespaces/shared/merged_devcontainer.json` shows
`mcr.microsoft.com/devcontainers/base:alpine` with `features: []` — none of
this repo's devcontainer. The creation log says what happened:

```
Using image: mcr.microsoft.com/devcontainers/universal
Error code: 1302 (UnifiedContainersErrorFatalCreatingContainer)
Creating recovery container.
```

The devcontainer failed to start and GitHub substituted its **recovery
container**. That container is unprivileged, so installing docker by hand does
not help either — `dockerd` dies with `iptables ... Permission denied (you must
be root)`. `gh codespace rebuild --full` re-lands in recovery.

Check `/workspaces/.codespaces/.persistedshare/creation.log` before debugging
anything else; if it names error 1302, the codespace is not salvageable and the
fault is upstream. Deploy to an existing healthy codespace and retry creation
later.

### The codespace name can stop resolving after a cold start

Seen 2026-09-06 after the codespace had been *Shutdown* (idle timeout) and was
started again: every `https://<name>-<port>.app.github.dev` URL returned **404
with an empty body** — including the private port (which should 302 to sign-in)
and the tunnel's own internal ports — while `gh codespace ports` listed 80 as
public, the compose stack was healthy, and `gh codespace ports forward` worked.
Neither re-publishing the port, holding a forwarder, nor a full `stop` /
`start` changed it.

The diagnosis and the workaround both go through the dev-tunnel behind the
codespace. `gh api '/user/codespaces/<name>?internal=true'` returns
`connection.tunnelProperties` — `tunnelId`, `clusterId`, `serviceUri` and a
`managePortsAccessToken` (scope `manage:ports`; treat it as a secret). With it:

```bash
# tunnel status: hostConnectionCount must be 1; ports must list 80 as protocol http
curl -s -H "Authorization: tunnel $TOKEN"   "$SERVICE_URI/tunnels/$TUNNEL_ID?clusterId=$CLUSTER&includePorts=true&api-version=2023-09-27-preview"
# the tunnel reached by its id instead of the codespace name
curl -s https://$TUNNEL_ID-80.app.github.dev/health
```

A cold start creates a **new tunnel** (a new `tunnelId`; the old one answers
404 from the management API). When the id-based URL serves the app but the
name-based one 404s, the tunnel is fine and GitHub's alias from the codespace
name to the new tunnel is what has not been (re)registered — a lookup of the
tunnel by name on the management API 404s too. The `manage:ports` token cannot
touch the tunnel's `name` (403, `expected [manage]`), so this is not fixable
from outside GitHub.

What to do: the id-based URL is a working front door for smoke tests
(`/health`, the SPA), but **sign-in fails on it**: the server sends no
`redirect_uri`, so GitHub returns the browser to the OAuth app's registered
callback, which is the name-based host. Nothing in the server's own
configuration binds the host (`UI_ORIGIN=/`, and the cookie belongs to whichever
host served it), so the only change needed to use the tunnel id for real is the
OAuth app's *Authorization callback URL* — knowing the id changes on the next
cold start. Otherwise wait for GitHub to heal the alias. Keeping the codespace
awake (the forwarder, or a shorter idle-timeout policy) avoids the cold start
altogether.

### Cost

A 4-core machine burns **4 core-hours per wall-clock hour**. Personal accounts
get free core-hours monthly (120 on Free, 180 on Pro, plus 15–20 GB-months of
storage — check the current figures before relying on them), so roughly 30 h/month
on Free and 45 h on Pro of *running* time. Beyond the allowance the 4-core rate
is $0.36/hr. `gh codespace stop -c "$NAME"` stops compute billing; **storage
keeps accruing until the codespace is deleted.**

### What changes on a plain VM

The compose file runs unchanged. What goes away: port-forward registration, the
`-80.app.github.dev` hostname in the OAuth App (one stable callback URL instead
of one per codespace), and the free-hours ceiling. What arrives: TLS is yours,
the machine must be patched and monitored, `WORKSPACES_HOST_DIR` should point at
a real disk, and idle auto-stop goes away — so size an always-on VM against the
bill, not the peak.

---

## Images

| Image | Built from | Notes |
|---|---|---|
| `server` | `cloud/Dockerfile`, context = repo root | Two stages: the GT producer (golang:1.22.5-bookworm), then python:3.12-slim with git, bash, curl, the **static Docker CLI** (no daemon), the `groundtruth_mcp` wheel and `pip install -e ".[cloud,miniswe,gt]"`. |
| `ui` | `cloud/ui/Dockerfile`, context = `cloud/ui` | node:20-alpine `npm ci` + `npm run build`, served by nginx:alpine on port 80. |
| `gt-egress-proxy` | `cloud/sandbox/proxy/Dockerfile` | python:3.12-slim, stdlib only, runs as `nobody`. |
| `gt-sandbox` | `cloud/sandbox/Dockerfile` | debian:bookworm-slim with bash, coreutils (GNU `timeout`), build-essential, python3 + a venv owned by uid 1000, node/npm, git, ripgrep, jq. **Build profile only** — never started as a service. |

Standalone builds:

```bash
docker build -t gt-sandbox:latest cloud/sandbox
docker build -t gt-egress-proxy:latest cloud/sandbox/proxy
docker build -f cloud/Dockerfile -t gt-cloud-server .
```

The producer stage fails the build rather than a session if provenance is
incomplete: it runs `gt-index -build-info` and greps for `"complete":true`.

---

## Restart and health policy

The audit found the codespace down twice in one session: both containers
`Exited (255)` after a daemon restart, with no restart policy, while the public
URL served 502 (HAR-84 G-01). Every long-lived service is now
`restart: unless-stopped` **and** has a healthcheck, and
`tests/test_cloud_compose.py` asserts both.

| Service | Healthcheck | Depends on |
|---|---|---|
| `server` | `curl -fsS http://127.0.0.1:8000/health` every 15 s, 30 s start period | `egress-proxy` started |
| `ui` | busybox `wget` on nginx's own root every 15 s | `server` **healthy** — the UI proxies `/api`, so coming up first is how a fresh deploy serves 502s |
| `egress-proxy` | TCP connect to `127.0.0.1:3128` every 30 s | — |

Sandbox containers get `--restart unless-stopped` from `run_argv` for the same
reason.

`restart: unless-stopped` has one deliberate hole: a `docker kill` or an explicit
`docker stop` counts as a manual stop and is not undone. That is correct docker
semantics, and the reason to bounce a service with `docker compose restart
<svc>`.

Measured: `dockerd` killed and restarted on the codespace, all five containers
back in 34 s unattended (`15f845a0`).

---

## The idle TTL reaper

A background task started by the app lifespan. Every
`SESSION_REAP_INTERVAL_SECONDS` it lists sessions that have been **`idle`** since
before `now - SESSION_IDLE_TTL_SECONDS`, re-reads each one (a message may have
started a turn since the query) and closes it through exactly the same path as
`/close`: sandbox, then workspace, then the row — with `closed_reason: "expired"`
and a `lifecycle closed {reason: "expired"}` frame.

- Only `idle` sessions are eligible. A `running` session is busy however old its
  row is, and `creating` is still cloning.
- `POST /stop` bumps `updated_at`, so a just-stopped session is not mistaken for
  a long-idle one.
- One bad pass never ends the loop.
- `recover()` also runs one pass at startup.
- `SESSION_IDLE_TTL_SECONDS=0` disables it entirely, which is logged at boot.

Without it, a repo clone and a container live per session until somebody
remembers to press close, so host disk is a monotonic function of how many
sessions anyone ever opened.

---

## Quotas and the disk floor

Three things bound disk, and only one of them would be a real quota:

1. **A floor before creation.** `WORKSPACES_MIN_FREE_MB` (2048): a session that
   cannot fit fails cleanly with a readable reason rather than filling the host.
   The workspaces directory shares a filesystem with the database, the images and
   every other session's clone, so the last free gigabyte is not one session's to
   spend.
2. **A per-session cap during a turn.** `SANDBOX_WORKSPACE_MAX_MB` (2048),
   measured with `du -sm` on the turn worker after **every** command — not only
   write-shaped ones, because `dd if=/dev/zero of=big` matches no write verb and
   the audit's repro was a single-command turn. Over it, the command in flight is
   killed, a `lifecycle quota_exceeded` frame is published and the turn ends
   `error`. If one measurement overruns 2 s the stride rises to one `du` per ten
   commands for the rest of the session.
3. **The idle TTL**, above.

**This is a watermark, not enforcement.** A single `dd` larger than the cap still
lands on disk in full and is caught immediately afterwards. A hard bound needs
`WORKSPACES_HOST_DIR` on its own volume — Docker's `--storage-opt size=` works
only on overlay2 over xfs mounted `pquota`, and not at all on a codespace's ext4
overlay. See [`docs/cloud-sandbox.md`](../cloud-sandbox.md) §5.

---

## Logs and inspection

```bash
DC="docker compose -f cloud/docker-compose.yml"

$DC ps
$DC logs --tail 100 server
$DC logs --tail 50 ui
docker logs --tail 50 gt-egress-proxy      # one ALLOW/DENY line per request

docker ps --filter name=gt-sandbox- --format '{{.Names}} {{.Image}} {{.Status}}'
docker inspect -f '{{.Config.User}} {{.HostConfig.Memory}} {{.HostConfig.PidsLimit}} {{.HostConfig.Binds}}' gt-sandbox-SESSIONID

df -h /                                    # the workspaces filesystem
du -sm /srv/gt-workspaces/* | sort -n
```

The proxy log is the egress record: `ALLOW CONNECT github.com:443`,
`DENY CONNECT openrouter.ai:443 403 host is not on the egress allow-list`, and
the allow-list itself printed at startup.

---

## Verifying a deployment

`cloud/sandbox/verify.sh` is an end-to-end check. Run it **on the deployment
host** — it uses `docker compose exec` and reads the host bind mount directly.
Deploy first, or it verifies a stale image.

```bash
bash cloud/sandbox/verify.sh off
bash cloud/sandbox/verify.sh advisory
MODEL=nvidia/nemotron-3-super-120b-a12b:free bash cloud/sandbox/verify.sh off
```

It mints a JWT inside the server container — borrowing the first
`ALLOWED_GITHUB_LOGINS` entry, because since HAR-84 G-10 a token minted for a
login nobody allow-listed is 403 on every route — creates a session on
`pallets/click@main`, waits out `creating`, dumps the lifecycle events and a
`docker inspect` of the sandbox, then sends one message:

> Create a file SANDBOX.txt containing the output of "id" and "hostname", then
> run `curl -sI https://openrouter.ai` and `curl -sI https://github.com` and tell
> me exactly what each returned.

That single turn exercises identity, the host bind mount, an allowed host and a
denied host. It then prints the reply, the `/diff` file list, the file as seen on
the host at `/srv/gt-workspaces/<id>/SANDBOX.txt`, the last 25 proxy log lines,
closes the session, and re-checks that both the container and the workspace are
gone. Everything is teed to `/tmp/verify-<mode>.log`.

Note the script hard-codes `cd /workspaces/gt-harness` and
`/srv/gt-workspaces/...`; adjust both on a non-Codespaces host.

---

## Failure modes and recovery

| Symptom | Cause | What to do |
|---|---|---|
| Public URL 302s to GitHub sign-in | Codespaces port visibility reset by the deploy. | `gh codespace ports visibility 80:public -c <name>` from a machine with a `codespace`-scoped `gh` login. |
| The app answers but the server log shows none of your requests | A stale tunnel-id URL now resolves to a different tunnel. | Re-read the tunnel id (see [A tunnel-id URL goes stale](#a-tunnel-id-url-goes-stale-and-a-stale-one-can-answer-as-somebody-else)) and re-set `PUBLIC_BASE_URL`. |
| A new codespace has no `docker` and no forwarded ports | GitHub substituted a **recovery container**; the devcontainer failed to start (creation.log: error 1302). | Not fixable in the codespace — see [A new codespace comes up with no docker and no ports](#a-new-codespace-comes-up-with-no-docker-and-no-ports-recovery-container). Deploy to an existing one and retry later. |
| Public URL returns **404, empty body** | The Codespaces edge: port not registered. nginx is fine. | `forwardPorts` in the devcontainer (takes effect on the next codespace), or hold `gh codespace ports forward 80:18080 -c <name>`. |
| **Every** `<name>-<port>.app.github.dev` URL 404s after a cold start, private ports included, ports listed and stack healthy | The codespace name no longer resolves to its (new) tunnel. | See [The codespace name can stop resolving after a cold start](#the-codespace-name-can-stop-resolving-after-a-cold-start): confirm with the id-based URL, then wait for GitHub or re-point the OAuth app's callback URL at the tunnel id. |
| Public URL returns **502** | `ui` came up before `server` was answering, or `server` is down. | `$DC ps`; the compose file already makes `ui` depend on `server` being *healthy*. `docker compose restart server`. |
| Containers `Exited (255)` and staying down | A daemon restart plus a manual `docker stop`/`kill` (which `unless-stopped` does not undo). | `docker compose -f cloud/docker-compose.yml up -d`. |
| Every session `gt_status: unavailable` | `GT_PRODUCER_ARTIFACT` set (fails closed), the wheel or `gt-index` missing, or an indexer failure. | Check `gt_error` on the session row; unset the GT pins; `docker compose exec server gt-index -build-info`. |
| A session becomes permanently unusable while reporting `idle` | A container out of pids (pre-`--init` zombies) answering every `docker exec` with `OCI runtime exec failed`. | Fixed: `--init` plus recreate-and-retry (`sandbox_restarted`). If it recurs, `docker rm -f gt-sandbox-<id>` and message the session again — `ensure_running` rebuilds it on the same workspace. |
| A turn ends `error` with `sandbox unavailable` | The container could not be recreated. | Check the daemon, then close and recreate the session. |
| Session creation fails with a disk message | `WORKSPACES_MIN_FREE_MB` floor. | Close idle sessions, `du -sm /srv/gt-workspaces/*`, prune images. The audit reclaimed 4.756 GB this way. |
| A turn ends `error` with `workspace quota exceeded` | `SANDBOX_WORKSPACE_MAX_MB`. | Expected. Raise the cap or tell the agent to stop writing. |
| A turn card stuck on *Working* after a restart | The turn was interrupted. | `recover()` writes `finish_reason: "interrupted"`, a `system_note` and a `turn_finished` frame; the browser renders it. If the tab predates that, reload. |
| `/stop` seems to hang | The model is thinking; the call is not cancellable. | Wait up to `MODEL_REQUEST_TIMEOUT`. |
| The UI is a version behind the server | compose reused a stale image. | `bash cloud/deploy.sh` (never `up -d` without `--build`), then check the four signals under [Proving what is deployed](#proving-what-is-deployed). |
| Sandboxes left behind after a crash | — | Restart the server: `recover()` reaps every container whose session no longer exists or is closed/failed, and keeps the rest. |

---

## The database is dropped on a schema bump

`SessionStore.init()` compares `PRAGMA user_version` against `SCHEMA_VERSION`
(currently **6**) and, when they differ, **drops and recreates every table**:
`diff_snapshots`, `events`, `turns`, `messages`, `sessions`.

This is deliberate — it is a dev tool — but it means:

- **Deploying a commit that bumps the schema erases every session, message, turn
  receipt and event.** Workspaces on disk are *not* removed by that, so their
  directories are orphaned; remove them by hand (`rm -rf /srv/gt-workspaces/*`)
  after a bump.
- There is no migration path and no backup mechanism. If a receipt matters, copy
  it out first: `docker compose cp server:/app/data/cloud_harness.db ./backup.db`,
  or `sqlite3` the volume.
- The database lives on the `db-data` volume, so it survives an image rebuild —
  only a version mismatch destroys it.

Schema versions on this branch: v2 (`a26d02a6`, the chat rebuild), v3
(`34f11f69`, `graph_db`), v4 (`1d08976a`, `gt_error`), v5 (`24f9e0fb`,
`closed_reason`), v6 (`9c394863`, worker columns), v7 (`9c0212d5`,
`gt_actions` / `gt_exact_matches`).
