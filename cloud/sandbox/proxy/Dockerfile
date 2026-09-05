# syntax=docker/dockerfile:1
# Egress proxy for GT session sandboxes.
#
#   docker build -t gt-egress-proxy:latest cloud/sandbox/proxy
#
# Attached to BOTH the internal sandbox network (where it is the only reachable
# route off-net) and the default bridge (where it forwards). Policy lives in
# proxy.py: git endpoints always, package registries under
# EGRESS_ALLOW_REGISTRIES, extra hosts from EGRESS_ALLOW. Nothing else — the
# model API included.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    EGRESS_PROXY_PORT=3128 \
    EGRESS_ALLOW_REGISTRIES=1 \
    EGRESS_ALLOW=""

COPY proxy.py /opt/egress/proxy.py

EXPOSE 3128
USER nobody

CMD ["python3", "-u", "/opt/egress/proxy.py"]
