# ─────────────────────────────────────────────────────────────────
# STAGE 1 — builder
#
# Purpose: install dependencies in a clean, isolated environment.
# This stage will be DISCARDED before the final image is produced.
# Nothing from this stage reaches production except what we
# explicitly copy out.
#
# Why separate stages?
# Installing packages requires build tools (gcc, pip, etc.).
# Those tools are only needed at build time, not at runtime.
# A multi-stage build lets us keep build tools out of the final
# image entirely — smaller image, smaller attack surface.
# ─────────────────────────────────────────────────────────────────
FROM python:3.11-alpine3.19 AS builder

# Then update system packages
RUN apk update && apk upgrade

# Set a working directory inside the builder container.
# All subsequent commands run from here.
WORKDIR /app

# Copy ONLY the requirements file first.
# Why? Docker caches each layer (instruction) individually.
# If requirements.txt hasn't changed, Docker reuses the cached
# pip install layer on the next build — even if your code changed.
# This makes rebuilds dramatically faster in CI and locally.
COPY requirements.txt .

# Install dependencies into an isolated prefix directory (/install)
# rather than the system Python.
# --no-cache-dir : don't store pip's download cache — saves space
# --prefix       : install to a custom path we'll copy to runtime
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ─────────────────────────────────────────────────────────────────
# STAGE 2 — runtime (final image)
#
# This is the image that actually gets shipped and run.
# It starts completely fresh — only what we explicitly copy lands
# here. No build tools, no pip cache, no compiler.
# ─────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Labels are metadata — they don't affect runtime behaviour but
# they're invaluable for auditing images in registries.
# 'maintainer' and 'version' show up in `docker inspect`.
LABEL maintainer="your-team@example.com" \
      service="api" \
      version="1.0.0"

WORKDIR /app

# Copy the installed packages from the builder stage.
# COPY --from=builder means "take this path from the builder
# container, not from the local filesystem."
COPY --from=builder /install /usr/local

# Copy the application source code.
# We do this AFTER copying dependencies so that a code change
# only invalidates this layer, not the pip install layer above.
COPY main.py .

# ─────────────────────────────────────────────────────────────────
# NON-ROOT USER EXECUTION
#
# By default Docker runs as root (UID 0) inside containers.
# Root in a container isn't full root on the host, but it's still
# dangerous: if an attacker breaks out of the app into the
# container, they have root access to everything inside it —
# all files, all processes.
#
# We create a dedicated system user 'appuser' with:
#   --system  : no login shell, no home directory, no password
#   --group   : create a matching group
#   --no-create-home : don't create /home/appuser (not needed)
#
# Then we switch to that user for all subsequent instructions.
# ─────────────────────────────────────────────────────────────────
RUN addgroup --system appgroup && \
    adduser --system --ingroup appgroup --no-create-home appuser

# Change ownership of the app directory to our new user.
# Without this the process can't read its own files.
RUN chown -R appuser:appgroup /app

# Switch to the non-root user.
# Every instruction from here — including CMD — runs as appuser.
USER appuser

# Document which port the service listens on.
# EXPOSE is documentation only — it does NOT publish the port.
# The actual publishing happens in docker-compose.yml or
# `docker run -p`. But it tells operators what to expect.
EXPOSE 8000

# ─────────────────────────────────────────────────────────────────
# HEALTHCHECK
#
# Docker uses this to know whether the container is healthy.
# Without a HEALTHCHECK, Docker only knows if the process is
# running — not whether it's actually serving requests.
#
# A container can be "running" but completely broken inside.
# With a HEALTHCHECK, Docker marks it "unhealthy" and:
#   - docker-compose will see it as unhealthy
#   - orchestrators (Kubernetes, ECS) will restart it
#   - `depends_on: condition: service_healthy` works correctly
#
# We hit /health — the endpoint we added in Phase 2 that actually
# pings Redis. This verifies the WHOLE stack is up, not just
# whether uvicorn answered.
#
# --interval=30s  : check every 30 seconds
# --timeout=5s    : if no response in 5s, count as a failure
# --start-period=10s : grace period after startup before
#                      failures count (uvicorn needs time to boot)
# --retries=3     : 3 consecutive failures → status = unhealthy
# ─────────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

# ─────────────────────────────────────────────────────────────────
# CMD — the command that starts the service.
#
# We use the JSON array form (exec form), NOT shell form.
# Shell form:  CMD uvicorn main:app ...
# Exec form:   CMD ["uvicorn", "main:app", ...]
#
# The difference matters for signals.
# Shell form wraps the command in /bin/sh -c, which means
# SIGTERM from Docker goes to sh, not to uvicorn. Uvicorn
# never receives the shutdown signal → graceful shutdown broken.
# Exec form makes uvicorn PID 1 — it receives signals directly.
#
# --host 0.0.0.0 : listen on all interfaces, not just localhost.
#   A container's localhost is not reachable from outside it.
#   0.0.0.0 means "accept connections from any network interface"
#   which is how other containers and the host reach this service.
# --workers 1    : single worker for simplicity; scale via
#                  multiple container replicas, not in-process.
# ─────────────────────────────────────────────────────────────────
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
