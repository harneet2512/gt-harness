# syntax=docker/dockerfile:1
# Backend image for the internal cloud coding agent.
#
# Build context is the REPOSITORY ROOT (see cloud/docker-compose.yml:
# build.context: ..), so all COPY paths below are repo-relative.
#
#   docker build -f cloud/Dockerfile -t gt-cloud-server .

# ---------------------------------------------------------------------------
# Stage 1 — the GroundTruth producer, built from source with the cloud patch.
#
# The vendored vendor/gt-index-linux-amd64 is the CERTIFIED BENCHMARK producer
# and aborts the whole resolution graph on a single derivation-invalid
# candidate, which makes it unable to index arbitrary user repositories
# (pallets/click is one line of proof). We rebuild it here from the same
# upstream commit with cloud/producer/0001-skip-invalid-candidates.patch, using
# the byte-for-byte build recipe from
# .github/workflows/deepswe_gt_harness_product.yml so nothing else about the
# binary changes. See cloud/producer/README.md.
# ---------------------------------------------------------------------------
FROM golang:1.22.5-bookworm AS producer

ARG PRODUCER_REPO=https://github.com/harneet2512/groundtruth
# Stamped into main.commitSHA so `gt-index -build-info` can never be mistaken
# for the certified benchmark producer.
ARG PRODUCER_VARIANT=cloud.1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        gcc \
        libc6-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY cloud/producer/ /producer/

# Shallow-fetch exactly the pinned commit, then apply the cloud patch.
WORKDIR /src
RUN set -eux; \
    SHA="$(tr -d '[:space:]' < /producer/PRODUCER_COMMIT)"; \
    git init -q .; \
    git remote add origin "${PRODUCER_REPO}"; \
    git fetch -q --depth 1 origin "${SHA}"; \
    git checkout -q FETCH_HEAD; \
    test "$(git rev-parse HEAD)" = "${SHA}"; \
    git apply --verbose /producer/0001-skip-invalid-candidates.patch

# CI's exact recipe: same fingerprint computation, same ldflags, static CGO.
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
    set -eux; \
    cd /src/gt-index; \
    SHA="$(tr -d '[:space:]' < /producer/PRODUCER_COMMIT)"; \
    SOURCE_FINGERPRINT="$(find . -type f \( -name '*.go' -o -name '*.c' -o -name '*.cc' -o -name '*.cpp' -o -name '*.h' -o -name '*.hpp' -o -name '*.s' -o -name 'go.mod' -o -name 'go.sum' \) -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d ' ' -f 1)"; \
    GO_TOOLCHAIN="$(go env GOVERSION)"; \
    BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"; \
    LDFLAGS="-linkmode external -extldflags=-static -X main.commitSHA=${SHA}+${PRODUCER_VARIANT} -X main.buildTimeUTC=${BUILD_TIME} -X main.sourceFingerprint=${SOURCE_FINGERPRINT} -X main.compiledBuildTags=sqlite_fts5 -X main.goToolchain=${GO_TOOLCHAIN}"; \
    mkdir -p /out; \
    CGO_ENABLED=1 GOOS=linux GOARCH=amd64 go build \
        -buildvcs=false -tags sqlite_fts5 -trimpath -mod=readonly \
        -ldflags "${LDFLAGS}" \
        -o /out/gt-index ./cmd/gt-index/; \
    chmod 0755 /out/gt-index

# Fail the build here rather than at session time if provenance is incomplete.
RUN set -eux; \
    /out/gt-index -build-info | tee /out/build-info.json; \
    grep -Fq '"complete":true' /out/build-info.json

# ---------------------------------------------------------------------------
# Stage 2 — the server runtime.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DB_PATH=/app/data/cloud_harness.db

# git: the runner clones target repositories.
# bash: mini-swe-agent shells out to bash for every tool call.
# curl: fetches the Docker CLI below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        bash \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI only — no daemon. With SANDBOX_MODE=docker the server drives the
# HOST daemon through the bind-mounted socket (see cloud/docker-compose.yml) to
# start, exec into and remove one sandbox container per session. The static
# binary avoids adding Docker's apt repository and its key to this image; the
# CLI negotiates its API version down to whatever the host daemon speaks.
ARG DOCKER_CLI_VERSION=27.5.1
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) dl_arch=x86_64 ;; \
        arm64) dl_arch=aarch64 ;; \
        *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://download.docker.com/linux/static/stable/${dl_arch}/docker-${DOCKER_CLI_VERSION}.tgz" \
        -o /tmp/docker.tgz; \
    tar -xzf /tmp/docker.tgz -C /tmp docker/docker; \
    install -m 0755 /tmp/docker/docker /usr/local/bin/docker; \
    rm -rf /tmp/docker /tmp/docker.tgz; \
    docker --version

WORKDIR /app

# Dependency metadata first so the pip layer is cached across source edits.
COPY pyproject.toml README.md ./

# Package sources declared in [tool.hatch.build.targets.wheel].
COPY nano/ ./nano/
COPY eval/ ./eval/
COPY gt_engine/ ./gt_engine/
COPY gt_harness/ ./gt_harness/
COPY scripts/ ./scripts/
COPY cloud/ ./cloud/

# GroundTruth: the vendored gateway wheel, plus the source-built indexer from
# the producer stage, so a session with gt_mode != off builds a real graph
# instead of degrading. The vendored binary is deliberately NOT used here --
# see the stage 1 comment and cloud/producer/README.md.
COPY vendor/groundtruth_mcp-1.0.0-py3-none-any.whl ./vendor/
COPY --from=producer /out/gt-index /usr/local/bin/gt-index
COPY --from=producer /out/build-info.json /usr/local/share/gt-index-build-info.json
ENV GT_INDEX_BINARY=/usr/local/bin/gt-index

RUN pip install --upgrade pip \
    && pip install vendor/groundtruth_mcp-1.0.0-py3-none-any.whl \
    && pip install -e ".[cloud,miniswe,gt]"

# Mount point for the SQLite session store (compose volume: db-data).
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "cloud.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
