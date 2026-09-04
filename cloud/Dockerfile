# Backend image for the internal cloud coding agent.
#
# Build context is the REPOSITORY ROOT (see cloud/docker-compose.yml:
# build.context: ..), so all COPY paths below are repo-relative.
#
#   docker build -f cloud/Dockerfile -t gt-cloud-server .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DB_PATH=/app/data/cloud_harness.db

# git: the runner clones target repositories.
# bash: mini-swe-agent shells out to bash for every tool call.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        bash \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

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

RUN pip install --upgrade pip \
    && pip install -e ".[cloud,miniswe]"

# Mount point for the SQLite session store (compose volume: db-data).
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "cloud.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
