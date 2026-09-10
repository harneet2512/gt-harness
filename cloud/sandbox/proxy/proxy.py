"""Allow-listed HTTP/CONNECT forward proxy for GT session sandboxes.

The sandbox network is created ``--internal``, so a sandbox has no route off
the host and no external DNS. This process — attached to both that network and
the default bridge — is the only way out, and it serves exactly the hosts on
its allow-list. Everything else gets ``403``, including the model API: model
calls happen in the server process, never in a sandbox.

Two request shapes are handled, which is all git, pip, npm and curl produce:

* ``CONNECT host:443`` — the TLS tunnel. Allowed hosts get a raw byte pump;
  denied hosts get ``403`` *before* any connection is opened, so a blocked host
  is never even resolved.
* ``GET http://host/path`` — absolute-form plain HTTP, rewritten to origin form
  and forwarded with ``Connection: close``.

No dependencies beyond the standard library, and no config file: the policy is
the ``DEFAULT_ALLOW`` tuple below plus ``EGRESS_ALLOW``.

``DEFAULT_ALLOW``/``REGISTRY_ALLOW`` are duplicated from
``cloud/server/sandbox.py`` because this file ships in its own image and cannot
import that package; ``tests/test_cloud_sandbox.py`` asserts they stay equal.
"""
from __future__ import annotations

import fnmatch
import os
import select
import socket
import socketserver
import sys
import threading
from urllib.parse import urlsplit

#: git endpoints — must mirror cloud/server/sandbox.py:GIT_ALLOW
DEFAULT_ALLOW = (
    "github.com",
    "*.github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
)
#: package registries — must mirror cloud/server/sandbox.py:REGISTRY_ALLOW
REGISTRY_ALLOW = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
)

ALLOWED_PORTS = (80, 443)
CONNECT_TIMEOUT = 15
IDLE_TIMEOUT = 300
HEADER_LIMIT = 64 * 1024
MAX_HEADERS = 100
BUFFER_SIZE = 65536

_DENY_BODY = b"blocked by the GT sandbox egress policy\n"
_LOG_LOCK = threading.Lock()


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() not in {"", "0", "false", "no"}


def allow_list() -> tuple[str, ...]:
    """Host patterns this proxy will serve, lowercased."""
    hosts = list(DEFAULT_ALLOW)
    if truthy(os.environ.get("EGRESS_ALLOW_REGISTRIES", "1")):
        hosts += list(REGISTRY_ALLOW)
    hosts += [
        item.strip()
        for item in os.environ.get("EGRESS_ALLOW", "").split(",")
        if item.strip()
    ]
    return tuple(dict.fromkeys(host.lower() for host in hosts))


def is_allowed(host: str, patterns: tuple[str, ...]) -> bool:
    """Exact match, or a ``*.example.com`` wildcard. Never a bare IP."""
    candidate = (host or "").strip().lower().rstrip(".")
    if not candidate:
        return False
    return any(fnmatch.fnmatchcase(candidate, pattern) for pattern in patterns)


def split_host_port(authority: str, default_port: int) -> tuple[str, int]:
    """``host:port`` / ``host`` / ``[v6]:port`` → ``(host, port)``."""
    authority = authority.strip()
    if authority.startswith("["):
        host, _, rest = authority[1:].partition("]")
        port = rest.lstrip(":")
        return host, int(port) if port.isdigit() else default_port
    host, _, port = authority.rpartition(":")
    if not host:
        return authority, default_port
    return host, int(port) if port.isdigit() else default_port


def log(*parts: object) -> None:
    with _LOG_LOCK:
        print(*parts, file=sys.stdout, flush=True)


class ProxyHandler(socketserver.StreamRequestHandler):
    # Unbuffered: the handler switches to raw byte pumping after the headers,
    # so nothing may be left sitting in a read buffer.
    rbufsize = 0
    wbufsize = 0
    timeout = 60

    def handle(self) -> None:
        try:
            self._handle()
        except (OSError, ValueError) as exc:
            log("error", type(exc).__name__, exc)

    def _handle(self) -> None:
        request_line = self.rfile.readline(HEADER_LIMIT)
        if not request_line:
            return
        parts = request_line.decode("latin-1").split()
        if len(parts) != 3:
            self.deny(400, "malformed request line", "-")
            return
        method, target, version = parts
        headers = self.read_headers()
        if method.upper() == "CONNECT":
            self.do_connect(target)
        else:
            self.do_forward(method, target, version, headers)

    def read_headers(self) -> list[str]:
        headers: list[str] = []
        while len(headers) < MAX_HEADERS:
            line = self.rfile.readline(HEADER_LIMIT)
            if not line or line in (b"\r\n", b"\n"):
                break
            headers.append(line.decode("latin-1").rstrip("\r\n"))
        return headers

    # -- policy ---------------------------------------------------------------

    def permit(self, host: str, port: int, what: str) -> bool:
        patterns = allow_list()
        if port not in ALLOWED_PORTS:
            self.deny(403, f"port {port} is not allowed", f"{what} {host}:{port}")
            return False
        if not is_allowed(host, patterns):
            self.deny(403, "host is not on the egress allow-list",
                      f"{what} {host}:{port}")
            return False
        log("ALLOW", what, f"{host}:{port}")
        return True

    def deny(self, status: int, reason: str, what: str) -> None:
        log("DENY", what, f"{status} {reason}")
        body = _DENY_BODY + reason.encode("utf-8") + b"\n"
        self.wfile.write(
            f"HTTP/1.1 {status} {'Forbidden' if status == 403 else 'Bad Request'}\r\n"
            f"Content-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"X-Egress-Policy: {reason}\r\n"
            f"Connection: close\r\n\r\n".encode("latin-1")
        )
        self.wfile.write(body)

    # -- verbs ----------------------------------------------------------------

    def do_connect(self, target: str) -> None:
        host, port = split_host_port(target, 443)
        if not self.permit(host, port, "CONNECT"):
            return
        upstream = self.dial(host, port)
        if upstream is None:
            return
        try:
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            pump(self.connection, upstream)
        finally:
            upstream.close()

    def do_forward(
        self, method: str, target: str, version: str, headers: list[str]
    ) -> None:
        split = urlsplit(target)
        if not split.netloc or split.scheme not in ("http", ""):
            self.deny(400, "only absolute http:// targets are proxied",
                      f"{method} {target[:120]}")
            return
        host, port = split_host_port(split.netloc, 80)
        if not self.permit(host, port, method.upper()):
            return
        upstream = self.dial(host, port)
        if upstream is None:
            return
        try:
            path = split.path or "/"
            if split.query:
                path = f"{path}?{split.query}"
            forwarded = [f"{method} {path} {version}"]
            forwarded += [
                header
                for header in headers
                if not header.lower().startswith(("proxy-", "connection:"))
            ]
            forwarded.append("Connection: close")
            upstream.sendall(("\r\n".join(forwarded) + "\r\n\r\n").encode("latin-1"))
            pump(self.connection, upstream)
        finally:
            upstream.close()

    def dial(self, host: str, port: int) -> socket.socket | None:
        try:
            upstream = socket.create_connection((host, port), CONNECT_TIMEOUT)
        except OSError as exc:
            log("UPSTREAM-FAIL", f"{host}:{port}", type(exc).__name__)
            self.deny(502, f"upstream connection failed: {type(exc).__name__}",
                      f"{host}:{port}")
            return None
        upstream.settimeout(None)
        return upstream


def pump(client: socket.socket, upstream: socket.socket) -> None:
    """Relay bytes both ways until either side closes or goes idle."""
    sockets = [client, upstream]
    while True:
        readable, _, errored = select.select(sockets, [], sockets, IDLE_TIMEOUT)
        if errored or not readable:
            return
        for source in readable:
            target = upstream if source is client else client
            try:
                chunk = source.recv(BUFFER_SIZE)
            except OSError:
                return
            if not chunk:
                return
            try:
                target.sendall(chunk)
            except OSError:
                return


class ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    port = int(os.environ.get("EGRESS_PROXY_PORT", "3128"))
    host = os.environ.get("EGRESS_PROXY_HOST", "0.0.0.0")  # noqa: S104
    log("egress-proxy listening on", f"{host}:{port}")
    log("egress-proxy allow-list:", ",".join(allow_list()))
    with ProxyServer((host, port), ProxyHandler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
