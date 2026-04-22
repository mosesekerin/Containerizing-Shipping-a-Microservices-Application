# ─────────────────────────────────────────────────────────────────
# STAGE 1 — dependency installer
#
# Node's multi-stage story is different from Python's.
# `node_modules` can be enormous (hundreds of MB).
# We install ALL dependencies (including devDependencies) in this
# stage so we have everything available.
# The runtime stage then copies only what's actually needed.
#
# node:18-alpine vs node:18-slim:
#   alpine  = ~5MB base, uses musl libc (sometimes breaks native modules)
#   slim    = ~30MB base, uses glibc (more compatible)
# For a pure-JS Express app with no native bindings, alpine is safe
# and gives us the smallest possible final image.
# ─────────────────────────────────────────────────────────────────
FROM node:18-slim AS builder

WORKDIR /app

# Copy package files first — layer cache optimisation.
# npm ci (not npm install) is used here intentionally:
#   npm install : updates package-lock.json if versions drift
#   npm ci      : installs EXACTLY what's in package-lock.json,
#                 fails if lock file is out of sync.
# In production and CI, you always want npm ci — it guarantees
# reproducibility and catches drift early.
COPY package.json package-lock.json* ./

# --omit=dev installs only production dependencies.
# We don't need jest, eslint, nodemon etc. in the final image.
RUN npm ci --omit=dev


# ─────────────────────────────────────────────────────────────────
# STAGE 2 — runtime
# ─────────────────────────────────────────────────────────────────
FROM node:18-slim

LABEL maintainer="your-team@example.com" \
      service="frontend" \
      version="1.0.0"

WORKDIR /app

# ─────────────────────────────────────────────────────────────────
# NON-ROOT USER — Node has a built-in 'node' user on Alpine.
# We don't need to create one manually.
# We set it up BEFORE copying files so the files are owned
# correctly from the moment they land in the image.
# ─────────────────────────────────────────────────────────────────
RUN chown -R node:node /app
USER node

# Copy node_modules from the builder stage.
# This is the key multi-stage benefit: the node_modules in the
# runtime image came from a clean npm ci — no lock file drift,
# no accidentally included dev tools.
COPY --from=builder --chown=node:node /app/node_modules ./node_modules

# Copy application source.
# We copy this AFTER node_modules so a code change doesn't
# invalidate the (much slower) npm ci layer.
COPY --chown=node:node app.js .
COPY --chown=node:node views/ ./views/
COPY --chown=node:node package.json .

EXPOSE 3000

# ─────────────────────────────────────────────────────────────────
# HEALTHCHECK
#
# The frontend has a /health route (added in Phase 2).
# We use wget instead of curl because Alpine doesn't include curl
# by default, and we don't want to install it just for health
# checks — that adds bloat and a dependency to patch.
#
# wget --spider : sends a HEAD request (no body download).
#                 If the server responds with any 2xx/3xx,
#                 exit code is 0 (healthy).
#                 If refused or timeout, exit code is 1 (unhealthy).
# -q            : quiet mode — no output to clutter logs.
# ─────────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --spider -q http://localhost:3000/health || exit 1

# Exec form CMD — same reasoning as other services.
# SIGTERM goes directly to node, not a shell wrapper.
CMD ["node", "app.js"]
