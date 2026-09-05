# Cloud VM Substrate Evaluation

Evaluates three compute substrates for running the GT cloud coding agent in production.

## Requirements

1. **Container-per-session**: each coding session runs in its own isolated environment
2. **Mid-run steering**: the service must accept inbound messages during a running session
3. **Persistent connections**: SSE streaming to the browser for real-time updates
4. **Dynamic repo ingress**: clone arbitrary repos on demand
5. **Cost transparency**: per-session cost must be quantifiable

## Option A: GitHub Actions as Compute

**How it works**: `workflow_dispatch` triggers a per-task job. The agent runs inside the Actions runner. Results are uploaded as artifacts.

| Aspect | Assessment |
|--------|-----------|
| Isolation | Good — each job is a fresh VM |
| Steering | **Poor** — runner jobs are non-interactive. Mid-run steering would require polling a shared store (Redis, S3, or GitHub Actions artifact) between steps. Latency: seconds to minutes depending on poll interval. No inbound connections. |
| SSE streaming | **Not possible** — no inbound HTTP to a runner. Events would need to be pushed to an external store and polled by the client. |
| Concurrency | Repo-scoped. Default 20 concurrent jobs per repo (can request increase). Queue semantics are FIFO per concurrency group. |
| Cost | Free for public repos. Private: $0.008/min (Linux), $0.016/min (Windows). 6-hour job ceiling. A typical 15-min coding session costs ~$0.12. |
| Cold start | 20-40 seconds for runner allocation + image pull. |

**Verdict**: Best for **batch CI evaluation** (already used in this repo). Not suitable for the interactive cloud agent — no steering, no SSE, no persistent connections.

## Option B: GitHub Codespaces

**How it works**: REST API creates a per-user dev container with full Linux VM access. Port forwarding exposes services. SSH and HTTP access available.

| Aspect | Assessment |
|--------|-----------|
| Isolation | Good — each codespace is a full VM with Docker support |
| Steering | **Good** — the FastAPI server runs inside the codespace with full HTTP access. Port forwarding (via `gh codespace ports`) exposes the API. |
| SSE streaming | **Good** — persistent HTTP connections work through port forwarding |
| Concurrency | Per-user limit (default 2 active codespaces). Can request increase. |
| Cost | **Expensive for always-on**: 2-core ($0.18/hr), 4-core ($0.36/hr), 8-core ($0.72/hr). Storage: $0.07/GB/month. Idle auto-stop after configurable period (default 30 min). A 15-min coding session on 4-core: ~$0.09 compute + storage. |
| Cold start | 30-90 seconds for codespace creation. Faster if pre-built. |
| API | `gh codespace create/start/stop/delete/ssh/ports`. REST API available. |

**Money gate**: A 4-core codespace running 8 hours/day for a month costs ~$86. Always-on costs ~$259/month. Acceptable for internal use if sessions are demand-started and auto-stopped.

**Verdict**: Viable for an interactive cloud agent. The port forwarding + auto-stop model fits demand-driven coding sessions. Main concern is cold start latency and per-user concurrency limits.

## Option C: Plain VM with Docker

**How it works**: A single VM (cloud or on-prem) runs the FastAPI server. Each coding session runs in a Docker container on that VM. The GT substrate Docker image cache already exists.

| Aspect | Assessment |
|--------|-----------|
| Isolation | **Best** — Docker container per session with network policy (egress limited to model API + git endpoints). The DeepSWE workflow already uses this pattern. |
| Steering | **Best** — FastAPI server accepts HTTP directly. No tunneling or port forwarding needed. |
| SSE streaming | **Best** — native persistent connections. No proxy limitations. |
| Concurrency | Limited by VM resources. A 4-core/16GB VM can comfortably run 3-4 concurrent sessions. |
| Cost | Depends on provider. AWS t3.xlarge (4 vCPU, 16 GB): ~$0.17/hr ($122/month always-on, ~$40/month 8hr/day). Hetzner CPX31: ~€15/month. |
| Cold start | Near-zero if the server is already running. Docker container start: 1-3 seconds with cached images. |
| Control | Full control over networking, storage, Docker configuration, egress policy, and scaling. |

**Verdict**: **Recommended**. Full control, lowest latency, native steering and SSE, Docker isolation matches the existing CI pattern. The only cost is infrastructure management.

## Recommendation

**Phase 1 (now)**: Plain VM with Docker (Option C). Deploy the FastAPI server on a single VM. Each coding session gets a Docker container with egress limited to model API + git endpoints. This gives the best developer experience (instant SSE, native steering) with the lowest operational friction.

**Phase 2 (scale)**: If demand grows beyond a single VM, evaluate Codespaces (Option B) for on-demand scaling without infrastructure management. The cold start is the main tradeoff.

**Keep**: GitHub Actions (Option A) for batch CI evaluation runs — the existing workflow is already optimized for this.

## What we actually did

The evaluation above recommended a plain VM. The internal deployment was built
on **Codespaces** instead, because it needed no infrastructure to exist: the
repo already had a devcontainer, and one `gh codespace create` produced a
public HTTPS origin with a certificate. This section records what that took, so
the next person does not rediscover it.

**The machine.** A 4-core codespace (`standardLinux32gb`, 4 vCPU / 16 GB, ~15 GB
usable, cgroup v2) on `cloud/internal-harness`, with the
`ghcr.io/devcontainers/features/docker-in-docker:2` feature — the server manages
one sandbox container per session through the Docker socket, so a Docker daemon
inside the codespace is not optional. `.devcontainer/devcontainer.json` also
declares `hostRequirements: { cpus: 4, memory: 16gb }`, which is what makes the
create pick that SKU rather than the 2-core default.

**The stack.** `docker compose -f cloud/docker-compose.yml up -d --build` brings
up three things: `server` (FastAPI/uvicorn on 8000), `ui` (nginx on **80**,
serving the built Vite bundle and reverse-proxying `/api`, `/auth` and `/health`
to `server:8000`), and `egress-proxy`. Everything the browser touches is on port
80 and therefore **same-origin** — which is the whole reason the UI is served by
nginx rather than by `vite preview` on 5173. `proxy_buffering off` plus a
3600 s read timeout on `/api/` is what keeps the SSE stream alive through nginx;
without it the event feed buffers and the UI looks frozen.

**OAuth.** The forwarded port becomes
`https://<codespace-name>-80.app.github.dev`. That exact origin has to be the
GitHub OAuth App's callback host — `https://<codespace-name>-80.app.github.dev/auth/callback`
— and because the UI is same-origin, `UI_ORIGIN=/` is correct in `.env`; setting
it to a `localhost` URL sends the browser off the codespace after login. Port 80
must also be set to **public** visibility, or the OAuth redirect lands on
GitHub's port-auth page instead of the app.

**The port-forward trap.** A codespace only auto-registers forwarded ports for
listeners it observes, and it observes them through a connected VS Code client.
A stack started over SSH (`gh codespace ssh -- docker compose up -d`) with no
editor attached leaves `-80.app.github.dev` returning **404 from the Codespaces
edge** — not from nginx, which is up and answering on the box. Two fixes, and we
used both: declare `forwardPorts: [80, 8000]` in `.devcontainer/devcontainer.json`
so the port exists from creation, and, for a codespace already running, hold a
detached `gh codespace ports forward 80:18080 -c <name>` from a workstation. The
detached tunnel is fragile — ours died with
`websocket: close 1006 (abnormal closure)` and took the public URL with it — so
treat it as the interim, not the mechanism.

**Cost.** Personal accounts get free Codespaces core-hours each month (120 on
Free, 180 on Pro, plus 15–20 GB-months of storage; check the current figures
before relying on them). A 4-core machine burns **4 core-hours per wall-clock
hour**, so the free allowance is roughly 30 h/month on Free and 45 h on Pro of
*running* time — comfortable for demand-driven demos, nowhere near an always-on
service. Beyond the allowance the 4-core rate is $0.36/hr. Stopping the
codespace stops compute billing; storage keeps accruing until it is deleted.

**What changes on a plain VM.** Very little of the stack, and all of the
plumbing. The same compose file runs unchanged; `ui` on 80 and `server` on 8000
stay as they are. What goes away: port-forward registration (a real interface
and a real DNS name), the `-80.app.github.dev` hostname in the OAuth App (one
stable callback URL instead of one per codespace), and the free-hours ceiling.
What arrives: TLS is yours (nginx + a certificate, or a load balancer in front),
the machine has to be patched and monitored, and `WORKSPACES_HOST_DIR` should
point at a real disk rather than the codespace's ephemeral one. Idle auto-stop
also goes away, so an always-on VM must be sized against the bill, not against
the peak.

## References

- [GitHub Actions billing](https://docs.github.com/en/billing/managing-billing-for-github-actions/about-billing-for-github-actions)
- [GitHub Codespaces pricing](https://docs.github.com/en/billing/managing-billing-for-github-codespaces/about-billing-for-github-codespaces)
- [Codespaces REST API](https://docs.github.com/en/rest/codespaces)
- [Codespaces port forwarding](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace)
