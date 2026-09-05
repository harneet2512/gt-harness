# syntax=docker/dockerfile:1
# Per-session sandbox image for the GT cloud coding agent.
#
#   docker build -t gt-sandbox:latest cloud/sandbox
#
# One container per session (`gt-sandbox-<session_id>`), started by
# cloud/server/sandbox.py right after the clone and removed on close. The
# session workspace is bind-mounted at /workspace; the container itself holds
# no session state, so it can be rebuilt or replaced at will.
#
# Deliberately NOT here: the Docker CLI or socket, any credential, and any
# route off the sandbox network — egress goes through the allow-listed proxy
# (HTTP_PROXY is set at `docker run` time).
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    GIT_TERMINAL_PROMPT=0

# bash: every agent action is `bash -c`.
# coreutils: GNU `timeout`, which enforces the per-command limit in-container.
# build-essential/python3/nodejs: agents run and install the repo's test suite.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        coreutils \
        curl \
        git \
        jq \
        less \
        nodejs \
        npm \
        procps \
        python3 \
        python3-dev \
        python3-venv \
        ripgrep \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# uid 1000 to match what the server chowns the workspace to.
RUN useradd --uid 1000 --create-home --shell /bin/bash agent

# The bind-mounted workspace is owned by uid 1000, but Docker Desktop and
# rootless setups synthesise ownership; git refuses to operate on a tree it
# thinks belongs to someone else, so declare it safe once, globally.
RUN git config --system --add safe.directory /workspace \
    && git config --system init.defaultBranch main \
    && git config --system advice.detachedHead false

USER agent

# Debian's python3 is externally managed: `pip install` into it fails, and as
# uid 1000 there is nothing writable there anyway. A venv owned by the agent,
# first on PATH, makes `python`/`pip` behave the way an agent expects. npm gets
# the same treatment for `-g` installs.
RUN python3 -m venv /home/agent/venv \
    && /home/agent/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
    && mkdir -p /home/agent/.npm-global

ENV VIRTUAL_ENV=/home/agent/venv \
    NPM_CONFIG_PREFIX=/home/agent/.npm-global \
    PATH=/home/agent/venv/bin:/home/agent/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

WORKDIR /workspace

# The container is a long-lived execution jail: it holds nothing itself, it
# just has to stay alive so `docker exec` has somewhere to land.
CMD ["sleep", "infinity"]
