# Security, as built

The threat model of the cloud coding agent at `645fe276`. The sandbox half is
condensed from [`docs/cloud-sandbox.md`](../cloud-sandbox.md), which is the
primary document and carries the evidence; the authentication and secrets halves
are from `cloud/server/auth.py`, `cloud/server/environment.py` and
`cloud/server/sandbox.py`.

- [The premise](#the-premise)
- [What is isolated](#what-is-isolated)
- [What is not](#what-is-not)
- [Authentication and authorisation](#authentication-and-authorisation)
- [Secrets](#secrets)
- [Egress policy](#egress-policy)
- [Resource caps](#resource-caps)
- [Input validation](#input-validation)
- [Known gaps](#known-gaps)

---

## The premise

An agent that runs arbitrary shell commands on a repository a user pasted in is
an arbitrary-code-execution endpoint by construction. `SANDBOX_MODE=docker`
makes that survivable: the command runs in a jail that cannot reach the host,
cannot reach the model provider, and cannot reach anything on the network that is
not on a short list.

`SANDBOX_MODE=local` — the default outside compose — keeps none of that. It runs
commands in the server process's own machine account. It exists for development
and it is a stated limitation.

**Fail closed.** A sandbox that will not start fails the *session*. There is no
fallback to local execution, because falling back would silently drop both the
isolation and the egress policy the sandbox exists to enforce, which is the worst
available failure mode: it looks like success.

---

## What is isolated

| Threat | Control |
|---|---|
| Reading or writing anything on the host outside the session | Only the session workspace is bind-mounted, at `/workspace`. Nothing else is visible. |
| Escalating to root inside the container | `--cap-drop ALL` and `--security-opt no-new-privileges`; commands run as uid 1000 (`agent`), never as root. A setuid binary gains nothing. |
| Escaping via the Docker daemon | The Docker socket is mounted into the **server**, not into a sandbox — compose puts `/var/run/docker.sock` on the `server` service only. A sandbox has no socket, no docker CLI, and no way to ask the daemon for anything. `tests/test_cloud_sandbox.py` asserts it from inside a real container. |
| Stealing model-provider credentials | Model calls happen in the server process, so the keys are never needed in a sandbox. The exec environment is an allow-list, and the credential scrub is applied **on top** of it. |
| Exfiltrating a repository, or pulling a payload from an arbitrary host | The sandbox network is `--internal`: no default route off the host, no external DNS. The only way out is the allow-listed proxy, which answers a denied host `403` **before opening a connection**, so it is never even resolved. A bare IP does not help: `is_allowed()` never matches one. |
| Starving the host | `--memory 2g --cpus 2 --pids-limit 512`, a tmpfs `/tmp` capped at 512 MB (`rw,nosuid,nodev,exec`), a per-session workspace watermark and a host-wide free-space floor. |
| Zombies exhausting the pid limit | `--init` runs tini as pid 1. Before it, pid 1 was `sleep infinity`, which never reaps: 510 forks left a container permanently out of pids and **every** later `docker exec` failed while the session still reported `idle` (HAR-84 G-03). |
| A container the daemon stopped, or one wedged out of pids | `--restart unless-stopped`; `ensure_running()` starts a stopped-but-present container; an `OCI runtime exec failed` rc-128 result is treated as a sandbox-health failure — the container is recreated, the command retried once, and only then does the turn end with `sandbox unavailable` and `finish_reason: error`. |
| A wedged or runaway command | Two timeouts. GNU `timeout --signal=KILL <n>s` **inside** the container kills the process tree the exec created; the client-side `communicate` timeout at `n + 10 s` is a backstop for a wedged daemon, followed by `pkill -KILL -u 1000` in the container. |
| Leaking host paths in error text | `clone_error_message` maps git's own words onto the product's and strips absolute paths, so a failure does not name `/srv/gt-workspaces/<session id>` or ask about a username nobody was going to be prompted for (HAR-84 G-22). |

### The chmod, and why it is not a chown

The two sides of the bind mount run as different users: the server (root in its
own container) clones and indexes; the sandbox edits as uid 1000. `chown` is
unavailable, because `--cap-drop ALL` takes `CAP_CHOWN` away from root *inside*
the container too, and dropping every capability is worth more than tidy
ownership. So `prepare_workspace()` does a server-side `chmod` pass adding
`a+rwX` — directories always get `x`, files only if they were executable already,
so the repository's own scripts stay runnable (`80be612b`).

Ownership staying with the server has a second benefit: the server's own `git
diff` never trips git's "dubious ownership" check.

The exposure that buys is bounded: the workspace is a throwaway clone, removed on
close, and anything on the host that can read it can already read everything else
the server owns. The pass is best-effort and POSIX-only — on Docker Desktop the
bind mount synthesises ownership anyway.

---

## What is not

- **The workspace itself.** The bind mount is the point: the server clones,
  indexes and diffs the same bytes the agent edits. An agent can destroy its own
  session's workspace. That is in scope for the product.
- **The server process.** It holds the Docker socket and the provider keys. A
  compromise of the *server* is total. The sandbox protects the host and the keys
  from the *agent*; it does not protect anything from a bug in the server.
- **Cross-session interference.** Sessions share a host, a CPU quota and one
  proxy. Nothing here is a hard multi-tenant boundary. A worker agent is a full
  session and shares the same host.
- **Content, once a host is allowed.** The allow-list is host-level. Anything a
  sandbox can reach on `github.com` it can reach in full — including pushing, if
  the agent ever obtains a credential. It does not get one from us.
- **The repository being cloned.** Only public GitHub HTTPS URLs are accepted and
  the server holds no git credentials, so a private repository simply fails to
  clone. But the *contents* of whatever is cloned are executed by an agent with
  network access to github.com and the package registries.

---

## Authentication and authorisation

| Property | As built |
|---|---|
| Identity | GitHub OAuth, confidential-client flow. The GitHub access token is exchanged server-side and never reaches the browser. |
| Session token | HS256 JWT over `JWT_SECRET`, carrying `sub`, `login`, `name`, `avatar_url`, `iat`, `exp`. Delivered as an `HttpOnly`, `SameSite=Lax` cookie, or accepted as `Authorization: Bearer`. |
| Coverage | `require_user` is attached at the **router**, so no `/api` endpoint can forget it. `/auth/me` uses the same dependency. There is no "auth disabled" mode. |
| Authorisation | `ALLOWED_GITHUB_LOGINS` is re-checked on **every** request, not only at the callback. Before HAR-84 G-10, a token signed for a login that was never allowed — or had since been removed — could read and write every session in the deployment until it expired. |
| Token lifetime | `JWT_TTL_SECONDS`, default 86400 (was 604800). There is no revocation list, so this is the eviction latency for a removed user when the allow-list is empty. |
| CSRF | The OAuth flow uses a `secrets.token_urlsafe(32)` state, single-use, expiring after 600 s. |
| CORS | No middleware at all unless `CORS_ORIGINS` names an origin. Never `*` — the endpoints are credentialed. |

### Per-object authorisation

There is **none**. Any authenticated, allow-listed user can list, read, message,
stop and close **every** session in the deployment. Sessions carry no owner
column. This is an internal tool with an explicit allow-list; on anything wider
it is a hole. Worker routes are the only place a relationship is checked, and
only structurally: `_require_worker` refuses a worker whose `parent_id` is not
this session.

---

## Secrets

| Rule | Where |
|---|---|
| Credentials live in `cloud/.env` and reach the server process only | compose `env_file` |
| Model calls happen in the server process, never in a sandbox | `runner.py:_build_agent` |
| A shell command never sees a credential | `CloudLocalEnvironment.execution_env()` filters `os.environ` through `is_sensitive_env_name` |
| Template variables never carry one | `get_template_vars()` runs `scrub_sensitive_mapping` in **both** environments |
| The sandbox exec environment is an allow-list, scrubbed again | `sandbox.is_allowed_env_name` = (`PATH`, `HOME`, `LANG`, `TERM`, the proxy variables, `SANDBOX_ENV_PASSTHROUGH`) **and not** sensitive |
| `PATH` and `HOME` come from the image, never from the server | leaking the server's `HOME` would point pip and npm at `/root` |

`is_sensitive_env_name` matches a known set (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `AWS_*`, `GITHUB_TOKEN`, `GH_TOKEN`, `GOOGLE_API_KEY`,
`GOOGLE_APPLICATION_CREDENTIALS`, `HF_TOKEN`, `AZURE_OPENAI_API_KEY`) plus any
name ending in `_API_KEY`, `_ACCESS_TOKEN`, `_AUTH_TOKEN`, `_PASSWORD` or
`_SECRET` — so a name that looks like a credential is dropped **even when it is
explicitly listed** in `SANDBOX_ENV_PASSTHROUGH`.

Not covered: a secret that is in neither the known set nor those suffixes, and a
secret written into the repository the agent is working in.

---

## Egress policy

The sandbox network is created `--internal`, so the proxy is the only route off
it and nothing can bypass it by dialling an IP directly.

| Always | `github.com`, `*.github.com`, `codeload.github.com`, `objects.githubusercontent.com` |
| Under `SANDBOX_ALLOW_REGISTRIES=1` (default) | `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org` |
| Extra | `SANDBOX_EGRESS_ALLOW`, comma-separated, `*.example.com` wildcards |
| Everything else | `403` with an `X-Egress-Policy` header — **the model API included** |

The proxy (`cloud/sandbox/proxy/proxy.py`, stdlib only, running as `nobody`)
handles exactly two request shapes, which is all git, pip, npm and curl produce:
`CONNECT host:443` and absolute-form plain HTTP. Ports 80 and 443 only. A denied
host is refused **before** any connection is opened, so it is never resolved.

The default list is duplicated in the proxy file because that file ships in its
own image and cannot import the server package; `tests/test_cloud_sandbox.py`
asserts the two copies stay identical.

Verified live (`a64fa592`, `pallets/click`, both GT modes): `github.com` 200,
`openrouter.ai` 403 carrying `X-Egress-Policy` from our proxy — a policy refusal,
not a network error that could be mistaken for one.

---

## Resource caps

| Cap | Default | Enforced by |
|---|---|---|
| Container memory | 2 GB | `--memory` (kernel) |
| Container CPUs | 2 | `--cpus` (kernel) |
| Container pids | 512 | `--pids-limit` (kernel) |
| `/tmp` | 512 MB | tmpfs `size=` (kernel) |
| One command | 30 s | in-container `timeout --signal=KILL`, plus a client backstop |
| One model call | 300 s | `MODEL_REQUEST_TIMEOUT` |
| One turn | 60 steps / 900 s | the agent loop |
| Workspace size | 2048 MB | `du -sm` watermark, **not** kernel-enforced |
| Free host disk | 2048 MB floor | checked before a clone, **not** kernel-enforced |
| Turns running | 3 | `MAX_CONCURRENT_SESSIONS` |
| Sessions creating | 3 | `MAX_CONCURRENT_CREATIONS` |
| Workers per session | 4 | `MAX_WORKERS_PER_SESSION` |
| Idle session lifetime | 6 h | the reaper |

An rc-137 with no output is mapped to an explicit `[killed: the command hit the
container memory limit (SANDBOX_MEMORY) or was killed from outside]`, because the
kernel says nothing and the agent had to guess (HAR-84 G-20).

---

## Input validation

| Input | Validation |
|---|---|
| `repo` | Must match `^https://github\.com/[\w\-\.]+/[\w\-\.]+(\.git)?$`. No `ssh://`, no arbitrary host, no local path. |
| `ref` | Non-blank, no control characters, no leading/trailing whitespace, not starting with `-` — which `git clone --branch` would read as a flag. Passed after `--` in every git argv. |
| `model` | Non-blank, then proved against the provider by the preflight. |
| `gt_mode` | A `Literal`; anything else is 422. |
| Message content | 1..100 000 characters, non-blank after strip. |
| `/spawn` | Every non-blank line must match; at most 4 tasks. |
| `Last-Event-ID` | Must parse as a non-negative integer; 400 otherwise. |
| `through_event` | `>= 0`. |
| Store field names | `update_session` / `update_status` reject any field not in `_SESSION_FIELDS`, so no caller can write an arbitrary column. |

Every subprocess call in `workspace.py`, `environment.py` and `sandbox.py` uses a
**list argv with `shell=False`**. The one place a shell is involved is the agent's
own command, which is deliberately handed to `bash -c` as a single argv element.

---

## Known gaps

Carried from the audit and the sandbox note; see
[known-limitations.md](known-limitations.md) for the full list with owners.

- **No per-object authorisation.** Any allow-listed user reaches every session.
- **No seccomp, AppArmor or SELinux profile beyond Docker's default**, and no
  user-namespace remapping. Docker's default profile plus `--cap-drop ALL` plus
  `no-new-privileges` is the entire kernel-attack-surface story.
- **No enforced disk quota** on the bind mount. Both disk controls are watermarks
  checked between commands; a single `dd` larger than the cap lands in full.
- **TLS is not inspected.** `CONNECT` is allowed or refused on the authority the
  client asked for; what travels inside the tunnel is not examined and SNI is not
  checked against the request.
- **The proxy is HTTP-level.** SSH, raw TCP, UDP, ICMP and `ssh://` git remotes
  are not proxied and have nowhere to go on an internal network. That is the
  intent, but "blocked by policy" and "unreachable" look the same to a tool.
- **No DNS inside the sandbox network.** A tool that resolves a name itself
  before connecting reports a name-resolution error rather than a policy denial.
- **One shared proxy.** Logs interleave, the allow-list is global, there is no
  per-session policy.
- **`SANDBOX_MODE=local` has no isolation at all** and is the default outside
  compose.
- **No revocation list** for issued JWTs; `JWT_TTL_SECONDS` is the only bound.
- **The OAuth pending-state map is process-local**, so a restart or a second
  worker loses in-flight logins.
- **Docker Desktop weakens the uid-1000 story**: bind-mount ownership and
  permissions are synthesised, so `prepare_workspace()` is effectively a no-op.
