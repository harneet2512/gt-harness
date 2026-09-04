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

## References

- [GitHub Actions billing](https://docs.github.com/en/billing/managing-billing-for-github-actions/about-billing-for-github-actions)
- [GitHub Codespaces pricing](https://docs.github.com/en/billing/managing-billing-for-github-codespaces/about-billing-for-github-codespaces)
- [Codespaces REST API](https://docs.github.com/en/rest/codespaces)
- [Codespaces port forwarding](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace)
