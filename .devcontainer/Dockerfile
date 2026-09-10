FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# This application uses npm. The inherited optional Yarn apt source has an
# expired/missing signing key and prevents the Docker feature's apt update.
# Remove that unused source; retain signature verification for every package.
RUN rm -f /etc/apt/sources.list.d/yarn.list
