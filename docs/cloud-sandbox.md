# Cloud sandbox — design, policy and evidence

The sandbox package for the GT cloud coding agent: one container per session,
an internal network, and an allow-listed egress proxy. Implemented in
`a64fa592` (branch `cloud/internal-harness`); this is the design note and the
verification record that commit did not carry.

Code: `cloud/server/sandbox.py`, `cloud/sandbox/Dockerfile`,
`cloud/sandbox/proxy/{Dockerfile,proxy.py}`, `cloud/docker-compose.yml`,
`cloud/sandbox/verify.sh`. Tests: `tests/test_cloud_sandbox.py` (42).

## 1. What this is for

An agent that runs arbitrary shell commands on a repository a user pasted in is
an arbitrary-code-execution endpoint by construction. `SANDBOX_MODE=docker`
makes that survivable: the command runs in a jail that cannot reach the host,
cannot reach the model provider, and cannot reach anything on the network that
is not on a short list.

Two properties, and everything below is in service of one of them:

1. **Isolation** — the command runs as an unprivileged user in a container with
   no capabilities, no Docker socket, and hard resource caps.
2. **Egress policy** — the container's network has no route off the host; the
   only way out is a proxy that serves an allow-list.

`SANDBOX_MODE=local` (the default outside compose) keeps the previous
behaviour: commands run in the server process's own machine account, with none
of this. It exists for development, and it is the reason `SANDBOX_MODE` is
listed under Known Limitations in `cloud/README.md`.

## 2. Threat model

### What is isolated

| Threat | Control |
|---|---|
| Reading or writing anything on the host outside the session | Only the session workspace is bind-mounted, at `/workspace`. Nothing else is visible. |
| Escalating to root inside the container | `--cap-drop ALL` and `--security-opt no-new-privileges`; commands run as uid 1000 (`agent`), never as root. A setuid binary gains nothing. |
| Escaping via the Docker daemon | **The Docker socket is mounted into the *server*, not into a sandbox** — `cloud/docker-compose.yml` puts `/var/run/docker.sock` on the `server` service only. A sandbox has no socket, no docker CLI, and no way to ask the daemon for anything. `test_the_sandbox_has_no_docker_socket` asserts it from inside. |
| Stealing model-provider credentials | **Model keys never enter a sandbox.** Model calls happen in the server process. The exec environment is an allow-list — `PATH`, `HOME`, `LANG`, `TERM`, the proxy variables, and whatever `SANDBOX_ENV_PASSTHROUGH` names — and `is_sensitive_env_name` is then applied *on top* of it, so a name ending in `_API_KEY` / `_ACCESS_TOKEN` / `_AUTH_TOKEN` / `_SECRET` / `_PASSWORD` (or a known one such as `OPENAI_API_KEY`) is dropped even when it is listed explicitly. `PATH` and `HOME` come from the image, never from the server process — leaking the server's `HOME` would point pip and npm at `/root`. |
| Exfiltrating a repository, or pulling a payload from an arbitrary host | The sandbox network is created `--internal`: no default route off the host, no external DNS. `HTTP_PROXY`/`HTTPS_PROXY` point at `gt-egress-proxy`, which serves only the allow-list and answers everything else `403` **before opening a connection**, so a denied host is never even resolved. A bare IP does not help: `is_allowed()` never matches one. |
| Starving the host | `--memory 2g --cpus 2 --pids-limit 512`, plus a tmpfs `/tmp` capped at 512 MB (`rw,nosuid,nodev,exec`). |
| A wedged or runaway command | Two timeouts. GNU `timeout --signal=KILL <n>s` **inside** the container kills the process tree the exec created (`pkill -g` on an exec's pgid is unreliable across `docker exec`), and the client-side `communicate` timeout at `n + 10s` is a backstop for a wedged daemon, followed by `pkill -KILL -u 1000` in the container. |

### What is not

* **The workspace itself.** The bind mount is the point: the server clones,
  indexes and diffs the same bytes the agent edits. An agent can destroy its
  own session's workspace. That is in scope for the product.
* **The server process.** It holds the Docker socket and the provider keys. A
  compromise of the *server* is total. The sandbox protects the host and the
  keys from the *agent*; it does not protect anything from a bug in the server.
* **Cross-session interference.** Sessions share a host, a CPU quota and one
  proxy. Nothing here is a hard multi-tenant boundary.
* **Content, once a host is allowed.** The allow-list is host-level. Anything a
  sandbox can reach on `github.com` it can reach in full — including pushing,
  if the agent ever obtains a credential. It does not get one from us.

### `--cap-drop ALL` and the chmod that follows from it

The two sides of the bind mount run as different users: the server (root in its
own container) clones and indexes; the sandbox edits as uid 1000. Something has
to reconcile that, and there are only two candidates.

`chown` is unavailable: `--cap-drop ALL` takes `CAP_CHOWN` away from root
*inside* the container too, and dropping every capability is worth more than
tidy ownership. So `prepare_workspace()` does the other one — a server-side
`chmod` pass adding `a+rwX` (directories always get `x`, files only if they
were executable already). Ownership stays with the server, which has a second
benefit: the server's own `git diff` never trips git's "dubious ownership"
check on a tree owned by somebody else.

The exposure that buys is bounded. The workspace is a throwaway clone, it is
removed on `close()`, and anything on the host that can read it can already
read everything else the server owns. The pass is best-effort and POSIX-only;
on Docker Desktop the bind mount synthesises ownership and permissions anyway.

### Bind-mount path equality

`docker run -v <workspace>:/workspace` is resolved **by the daemon, on the
host** — not inside the server container. If the server saw the workspaces
directory at a different path than the host does, every sandbox would bind the
wrong directory, or fail outright.

The fix is deliberate and load-bearing: compose mounts
`${WORKSPACES_HOST_DIR:-/srv/gt-workspaces}` into the server **at the same
absolute path** and sets `WORKSPACES_DIR` to it. The path the server writes and
the path the daemon binds are then literally the same string, so there is no
translation layer left to get wrong. `run_argv()` says so at the point where it
matters, and its docstring is the reason it is a pure function: the argv is
asserted in tests without a daemon.

## 3. Lifecycle

One container per session, named `gt-sandbox-<session_id>` and labelled
`gt.sandbox.session=<session_id>`.

| Phase | What happens | Lifecycle event |
|---|---|---|
| Session created | — | `creating` |
| Clone | `git clone --depth 1` into `WORKSPACES_DIR/<session_id>` | `cloning` |
| Sandbox start | `ensure_network()` (creates `gt-sandbox-net` `--internal` if absent) → `ensure_proxy()` → `prepare_workspace()` → `docker run -d` → a probe `docker exec -u agent … true` | `sandbox_starting`, then `sandbox_ready {container, image, image_digest}` |
| Sandbox failure | any of the above raises `SandboxError` | `sandbox_failed {error}`, then `failed {error}` — **the session fails** |
| GT indexing | in the **server**, against the host path of the same workspace | `indexing` → `gt_ready` or `gt_unavailable {error}` |
| Ready | | `idle` |
| Each turn | `ensure_running()` re-derives and re-checks the container name (in-memory state is not trusted across a restart); every action is then one `docker exec` | `running`, then `assistant` / `tool_call` / `tool_result` … |
| Close | `remove_sandbox()` **before** `remove_workspace()` | `closed` |
| Server restart | `recover()` → `reap_sandboxes(keep)` | — |

The order at both ends is not incidental. The sandbox starts **after** the
clone (there is nothing to mount before it) and **before** GT indexing. It is
removed **before** the workspace, because a container still holding the bind
mount makes the directory undeletable on some hosts.

**Reaping keeps usable sandboxes.** A sandbox outlives a server restart on
purpose: the workspace is still on disk and the session is still `idle`, so the
same container keeps serving it. `_reap_sandboxes()` removes only containers
whose session no longer exists, or is `closed` / `failed`.

**Fail closed.** `_start_sandbox()` re-raises and `_create_blocking()` turns
that into a failed session. There is no fallback to local execution: falling
back would silently drop both the isolation and the egress policy the sandbox
exists to enforce, which is the worst available failure mode because it looks
like success.

## 4. Configuration

All of these are documented in `cloud/.env.example`; the defaults live in
`cloud/server/sandbox.py`.

| Variable | Default | Meaning |
|---|---|---|
| `SANDBOX_MODE` | `local` (compose sets `docker`) | `local` runs commands in the server's own machine account. `docker` turns all of this on. |
| `SANDBOX_IMAGE` | `gt-sandbox:latest` | The per-session image. |
| `SANDBOX_NETWORK` | `gt-sandbox-net` | The `--internal` network sandboxes join. |
| `SANDBOX_PROXY_CONTAINER` | `gt-egress-proxy` | Proxy container name and network alias. |
| `SANDBOX_PROXY_IMAGE` | `gt-egress-proxy:latest` | Used only when the proxy is not already running (local dev, integration tests). |
| `SANDBOX_PROXY_URL` | `http://gt-egress-proxy:3128` | What `HTTP_PROXY`/`HTTPS_PROXY` are set to inside the container. |
| `SANDBOX_ALLOW_REGISTRIES` | `1` | Adds `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org` — on by default because agents run test suites that install packages. |
| `SANDBOX_EGRESS_ALLOW` | *(empty)* | Extra hosts, comma-separated; `*.example.com` wildcards supported. |
| `SANDBOX_MEMORY` / `SANDBOX_CPUS` / `SANDBOX_PIDS_LIMIT` / `SANDBOX_TMPFS_SIZE` | `2g` / `2` / `512` / `512m` | Resource caps. |
| `SANDBOX_ENV_PASSTHROUGH` | *(empty)* | Extra environment variable **names** forwarded into exec. Subject to the credential scrub regardless. |
| `WORKSPACES_HOST_DIR` | `/srv/gt-workspaces` | Compose only: the host directory holding workspaces, mounted into the server at the same absolute path (§2). |
| `DOCKER_BINARY` | `docker` | The CLI to shell out to. There is deliberately no docker SDK dependency. |

Always reachable (git): `github.com`, `*.github.com`, `codeload.github.com`,
`objects.githubusercontent.com`. Everything else gets `403` — **the model API
included**. The default list is duplicated in `cloud/sandbox/proxy/proxy.py`
because that file ships in its own image and cannot import the server package;
`tests/test_cloud_sandbox.py` asserts the two copies stay identical.

## 5. Operations

Build the images. The sandbox image sits behind a build profile so it is never
started as a service — the server runs it, one container per session:

```bash
docker compose -f cloud/docker-compose.yml --profile build build
docker compose -f cloud/docker-compose.yml up -d
```

Standalone equivalents: `docker build -t gt-sandbox:latest cloud/sandbox` and
`docker build -t gt-egress-proxy:latest cloud/sandbox/proxy`.

Inspect what is running:

```bash
docker ps --filter name=gt-sandbox- --format '{{.Names}} {{.Image}} {{.Status}}'
docker inspect -f '{{.Config.User}} {{.HostConfig.Memory}} {{.HostConfig.PidsLimit}} {{.HostConfig.Binds}}' gt-sandbox-SESSIONID
docker logs --tail 50 gt-egress-proxy      # one ALLOW/DENY line per request
```

* **A sandbox failure fails the session.** The docker error text rides on the
  `sandbox_failed` and `failed` lifecycle events. There is no degraded mode;
  check `docker version` from inside the server container first.
* **A server restart keeps live sandboxes** and reaps the rest. Nothing needs
  cleaning up by hand.
* **The proxy is shared** and `restart: unless-stopped`. While it is down,
  sandboxes have no egress at all — the safe direction.

## 6. Verification

### `cloud/sandbox/verify.sh`

An end-to-end check against a running compose deployment. Run it **on the
deployment host**: it uses `docker compose exec` and reads the host bind mount
directly.

```bash
bash cloud/sandbox/verify.sh off          # gt_mode=off
bash cloud/sandbox/verify.sh advisory     # gt_mode=advisory
MODEL=nvidia/nemotron-3-super-120b-a12b:free bash cloud/sandbox/verify.sh off
```

It mints a JWT inside the server container, creates a session on
`pallets/click@main`, waits out `creating`, dumps the lifecycle events and a
`docker inspect` of the sandbox (network, user, memory, pids, binds), then
sends one message:

> Create a file SANDBOX.txt containing the output of "id" and "hostname", then
> run `curl -sI https://openrouter.ai` and `curl -sI https://github.com` and
> tell me exactly what each returned.

That single turn exercises identity, the host bind mount, an allowed host and a
denied host. The script then prints the reply, the `/diff` file list, the file
as seen on the host at `/srv/gt-workspaces/<id>/SANDBOX.txt`, the last 25 proxy
log lines, closes the session, and re-checks that both the container and the
workspace are gone. Everything is teed to `/tmp/verify-<mode>.log`.

### Codespace runs, 2026-09-05

Two runs on the Codespaces deployment (`cloud/docker-compose.yml`,
`SANDBOX_MODE=docker`), one per GT mode, both against `pallets/click`. From the
commit message of `a64fa592`, verbatim:

> Verified on the codespace (gt off and gt advisory, pallets/click): the
> agent ran as uid 1000 in the container, github.com 200, openrouter.ai 403
> with X-Egress-Policy from the proxy, SANDBOX.txt visible via /diff and on
> the host bind mount, GT indexed to gt_ready alongside the sandbox, and
> close removed both the container and the workspace. 42 sandbox tests.

Point by point, and what each one actually proves:

| Observation | Property it establishes |
|---|---|
| the agent ran as **uid 1000** in the container | commands run neither as the server's user nor as root |
| **`github.com` → 200** | the allow-list is not a blanket block; git works through the proxy |
| **`openrouter.ai` → 403**, carrying `X-Egress-Policy` | the model API is unreachable *from the sandbox*, and the refusal came from our proxy — not from a network error that could be mistaken for one |
| **`SANDBOX.txt` visible via `/diff` and on the host bind mount** | the bind mount and path equality (§2): the server diffs the same bytes the agent wrote |
| **GT indexed to `gt_ready` alongside the sandbox** | server-side indexing still reaches the workspace while a container holds it |
| **close removed both the container and the workspace** | the teardown order works and nothing is left behind |
| **42 sandbox tests** | `tests/test_cloud_sandbox.py`; the docker-dependent ones skip with `no docker daemon available` where there is none |

The `advisory` run of that pair is the same session shape documented from the
GT side in [cloud-gt-run.md](cloud-gt-run.md).

## 7. Limitations

* **No seccomp profile beyond Docker's default.** No custom seccomp, no
  AppArmor or SELinux profile, no user-namespace remapping. Docker's default
  profile plus `--cap-drop ALL` plus `no-new-privileges` is the entire
  kernel-attack-surface story; a kernel bug reachable from the default syscall
  set is not defended against.
* **No disk quota on the bind mount.** `/tmp` is a capped tmpfs, but the
  workspace is a host directory: an agent can fill the host's disk. Memory,
  CPU and pids are capped; bytes on disk are not.
* **The proxy is HTTP-level.** It speaks exactly two things: `CONNECT
  host:443` and absolute-form plain HTTP, on ports 80 and 443 only. Everything
  else — SSH, raw TCP, UDP, ICMP, a git `ssh://` remote — is not proxied and,
  on an `--internal` network, has nowhere to go. That is the intent, but it
  means "blocked by policy" and "unreachable" look the same to a tool, so error
  messages can be confusing.
* **No DNS inside the sandbox network.** An `--internal` network has no
  external resolver, so a tool that resolves a name itself before connecting
  fails rather than being proxied. In practice this is fine, because everything
  goes through the proxy via `HTTP_PROXY`/`HTTPS_PROXY` and the proxy resolves
  on the bridge, where DNS works — but a tool that ignores the proxy
  environment reports a name-resolution error, not a policy denial.
* **TLS is not inspected.** `CONNECT` is allowed or refused on the authority
  the client asked for; what travels inside the tunnel is not examined, and SNI
  is not checked against the request.
* **One shared proxy.** All sessions use `gt-egress-proxy`, so its logs
  interleave and its allow-list is global. There is no per-session policy.
* **Docker Desktop caveats.** Bind-mount ownership and permissions are
  synthesised, so `prepare_workspace()` is effectively a no-op there and the
  uid-1000 story is weaker than it is on Linux.
