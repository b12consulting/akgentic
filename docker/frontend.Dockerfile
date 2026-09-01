# Frontend image — the Angular web UI served by nginx.
#
# ONE Dockerfile, TWO sources for the bundle, selected by ARG FRONTEND_SOURCE
# (fed from the FRONTEND_SOURCE env var by docker-compose.yml). Both stages
# normalise their output to /web, and the final stage COPYs from whichever was
# selected. BuildKit only builds the stage the final image actually needs, so
# the release path never runs npm and the source path never hits the network.
# This mirrors the BUILD_SOURCE mechanism in the department/enterprise tiers.
#
#   release  (default)  Downloads the prebuilt bundle from the akgentic-frontend
#            GitHub release named by FRONTEND_VERSION. ~1 MB and a few seconds,
#            no Node toolchain, and — the real win — NO submodule checkout: a
#            bare clone of this repo can `docker compose up` without ever
#            running `git submodule update --init packages/akgentic-frontend`.
#            The published artifact is the deployable web root itself
#            (dist/akgent-app/browser), built by that repo's release workflow
#            at --configuration production.
#
#   source              Builds from packages/akgentic-frontend. Use this when
#            you are CHANGING the frontend, or need a configuration the release
#            does not ship (BUILD_MODE=development for source maps). Requires
#            the submodule to be initialised.
#
# Build context is the repository ROOT for both, because .dockerignore is only
# ever read from the context root and the 583 MB node_modules under
# packages/akgentic-frontend has to be excluded from the upload. Hence the
# submodule path prefix on every COPY in the source stage.

ARG FRONTEND_SOURCE=release

# ---- release: prebuilt bundle from the GitHub release ------------------------
FROM alpine:3.20 AS release

RUN apk add --no-cache curl tar jq

# The version is pinned in ./package.json (dependencies.akgentic-frontend), so
# the release this stack serves is declared in a tracked manifest rather than
# living only in someone's .env. Copied as its own layer: editing package.json
# invalidates the download below and nothing else.
#
# akgentic-frontend is NOT on the npm registry — `npm install` against this
# manifest would 404. The file is a version pin this build reads, and the
# published artifact is the release tarball fetched below.
COPY package.json /tmp/package.json

# Empty default: FRONTEND_VERSION is an OPTIONAL override for a one-off test.
# Unset, the pin in package.json wins.
ARG FRONTEND_VERSION=

# Resolving in a single RUN keeps the precedence in one readable place.
# `set -eu` is load-bearing: it is what makes each step below abort the build.
# A pin that is absent, null or empty fails here rather than composing a URL
# with an empty version that 404s with a confusing message. `curl -f` turns a
# mistyped version into a failed build instead of an HTML error page unpacked
# as a web root, and the index.html check catches a well-formed tarball with an
# unexpected layout.
#
# b12consulting/akgentic-frontend is a public repo, so this needs no token.
WORKDIR /web
RUN set -eu; \
    VERSION="${FRONTEND_VERSION:-$(jq -r '.dependencies["akgentic-frontend"] // empty' /tmp/package.json)}"; \
    if [ -z "${VERSION}" ]; then \
      echo "No frontend version: set dependencies.akgentic-frontend in package.json, or pass FRONTEND_VERSION"; \
      exit 1; \
    fi; \
    echo "Fetching akgentic-frontend v${VERSION}"; \
    curl -fsSL -o /tmp/frontend.tar.gz \
      "https://github.com/b12consulting/akgentic-frontend/releases/download/v${VERSION}/akgentic-frontend-v${VERSION}.tar.gz"; \
    tar -xzf /tmp/frontend.tar.gz -C /web; \
    rm /tmp/frontend.tar.gz; \
    test -f /web/index.html

# ---- source: build from the submodule ---------------------------------------
FROM node:lts AS source

WORKDIR /frontend

COPY packages/akgentic-frontend/package.json packages/akgentic-frontend/package-lock.json ./
RUN npm ci

COPY packages/akgentic-frontend/public ./public
COPY packages/akgentic-frontend/src ./src
COPY packages/akgentic-frontend/angular.json ./
COPY packages/akgentic-frontend/tsconfig.json packages/akgentic-frontend/tsconfig.app.json ./

# 'production' optimises and hashes the bundle; 'development' keeps source maps.
ARG BUILD_MODE=production
RUN npm run build -- --configuration ${BUILD_MODE}

# Angular 17+ emits the browser bundle into a browser/ subdirectory. Normalise
# to /web so the final stage can COPY from either source identically.
RUN cp -r /frontend/dist/akgent-app/browser /web

# ---- final: serve whichever web root was selected ---------------------------
FROM ${FRONTEND_SOURCE} AS selected

FROM nginx:alpine

COPY --from=selected /web /usr/share/nginx/html

# SPA routing plus static-asset caching. Served from this repo rather than the
# submodule so the release path needs no submodule — see docker/nginx.conf.
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 4200

CMD ["nginx", "-g", "daemon off;"]
