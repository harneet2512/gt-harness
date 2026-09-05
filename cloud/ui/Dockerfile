# Frontend image: build the Vite bundle, serve it from nginx.
#
# Build context is cloud/ui (see cloud/docker-compose.yml: build.context: ui).
#
#   docker build -t gt-cloud-ui cloud/ui

# ---------- stage 1: build ----------
FROM node:20-alpine AS build

WORKDIR /app

# npm ci needs both files and an in-sync lockfile.
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json vite.config.ts index.html ./
COPY src/ ./src/

# Baked into the bundle by vite (`define: __BUILD_SHA__`), so the served SPA
# says which commit it is — the check that would have caught the stale
# `cloud-ui` image in round-2 QA.
ARG BUILD_SHA=dev
ENV BUILD_SHA=${BUILD_SHA}

RUN npm run build

# ---------- stage 2: serve ----------
FROM nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/ /usr/share/nginx/html/

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
