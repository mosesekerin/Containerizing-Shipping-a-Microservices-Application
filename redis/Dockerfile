# ─────────────────────────────────────────────────────────────────
# Redis does not need a custom Dockerfile for basic use —
# the official image is production-hardened.
#
# We write a thin wrapper Dockerfile only to:
#   1. Bake in our custom redis.conf at build time
#   2. Apply the correct label metadata
#   3. Ensure our config is loaded on every startup
#
# The official redis:7-alpine image:
#   - Already runs as a non-root user (redis:redis, UID 999)
#   - Is Alpine-based (~7MB base)
#   - Is maintained by Redis Ltd and Docker Official Images
#   - Receives security patches promptly
#
# DO NOT use `redis:latest` — pin the major version.
# `latest` will silently pull Redis 8 when it releases and
# potentially break your configuration.
# ─────────────────────────────────────────────────────────────────
FROM redis:7-alpine

LABEL maintainer="your-team@example.com" \
      service="redis" \
      version="1.0.0"

# Copy our custom configuration file into the image.
# The official Redis image's entrypoint checks for this path.
COPY redis.conf /usr/local/etc/redis/redis.conf

# EXPOSE documents the port. Redis default is 6379.
EXPOSE 6379

# ─────────────────────────────────────────────────────────────────
# HEALTHCHECK
#
# redis-cli ping sends a PING command to the Redis server.
# Redis responds with PONG if healthy.
# This is the canonical Redis health check — it tests the actual
# Redis protocol, not just whether the process is alive.
# ─────────────────────────────────────────────────────────────────
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD redis-cli ping | grep -q PONG || exit 1

# Start Redis with our custom config.
# The official image entrypoint handles the redis-server binary.
CMD ["redis-server", "/usr/local/etc/redis/redis.conf"]
