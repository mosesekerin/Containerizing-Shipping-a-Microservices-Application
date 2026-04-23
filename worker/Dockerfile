# ─────────────────────────────────────────────────────────────────
# STAGE 1 — builder
#
# Same pattern as the API Dockerfile.
# Install dependencies in isolation, then discard the build tools.
# The worker only needs the `redis` library, so this stage is
# extremely lean — but the pattern is correct and consistent.
# ─────────────────────────────────────────────────────────────────
FROM python:3.11-alpine3.19 AS builder

# Then update system packages
RUN apk update && apk upgrade

WORKDIR /app

# Copy requirements first to leverage Docker layer caching.
# pip install only re-runs if requirements.txt has changed.
COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ─────────────────────────────────────────────────────────────────
# STAGE 2 — runtime
# ─────────────────────────────────────────────────────────────────
FROM python:3.11-slim

LABEL maintainer="your-team@example.com" \
      service="worker" \
      version="1.0.0"

WORKDIR /app

# Copy installed packages from builder.
COPY --from=builder /install /usr/local

# Copy the worker script.
COPY worker.py .

# ─────────────────────────────────────────────────────────────────
# NON-ROOT USER
# Same reasoning as the API — limit blast radius of any breach.
# ─────────────────────────────────────────────────────────────────
RUN addgroup --system appgroup && \
    adduser --system --ingroup appgroup --no-create-home appuser

RUN chown -R appuser:appgroup /app

USER appuser

# ─────────────────────────────────────────────────────────────────
# HEALTHCHECK — for a background worker
#
# The worker has no HTTP endpoint. So how do we health-check it?
# We check that it can reach Redis — its one true dependency.
#
# This Python one-liner creates a Redis client and pings it.
# If the ping fails, the container is marked unhealthy.
#
# Why does a worker need a health check?
# Docker Compose's `depends_on: condition: service_healthy`
# uses this to know when the worker is truly ready.
# Without it, the worker might be declared "up" while Redis
# is still starting, causing immediate connection failures.
#
# --start-period=15s : workers take a moment to initialise their
#                      Redis connection — give them grace time.
# ─────────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "\
import redis, os; \
r = redis.Redis( \
    host=os.environ.get('REDIS_HOST','localhost'), \
    port=int(os.environ.get('REDIS_PORT', 6379)), \
    password=os.environ.get('REDIS_PASSWORD', None) \
); \
r.ping()" \
    || exit 1

# ─────────────────────────────────────────────────────────────────
# CMD — exec form so SIGTERM reaches Python directly.
#
# This is critical for the worker. In Phase 2 we implemented
# graceful shutdown: on SIGTERM, the worker finishes its current
# job before exiting. But that only works if Python receives
# the signal. Exec form guarantees Python is PID 1.
# ─────────────────────────────────────────────────────────────────
CMD ["python", "-u", "worker.py"]

# -u flag: unbuffered stdout/stderr.
# Without it, Python buffers output. In a container, this means
# your logs might never appear — they sit in a buffer until the
# process exits. -u flushes output immediately so logs are
# visible in real time via `docker logs`.
