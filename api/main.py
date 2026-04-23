from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
import redis
from redis.retry import Retry
from redis.backoff import ExponentialBackoff
import uuid
import os
import logging
import time
import json

# =================================================================
# STRUCTURED LOGGING
#
# Plain text logs are fine for reading in a terminal.
# In production, logs go to a centralised system (Datadog,
# CloudWatch, ELK stack). Those systems need to PARSE your logs
# to build dashboards, alerts, and search.
#
# JSON logs are machine-readable. Every field is queryable.
# You can search "show me all ERROR logs where service=api
# in the last 10 minutes" — impossible with plain text.
# =================================================================


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "service": "api",
            "message": record.getMessage(),
        }
        # Attach any extra fields passed to the logger
        if hasattr(record, 'job_id'):
            log_entry['job_id'] = record.job_id
        if record.exc_info:
            log_entry['exception'] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(__name__)


# =================================================================
# REDIS CONNECTION WITH RETRY + CONNECTION POOL
#
# The base redis.Redis() client makes one connection and if it
# drops, your next call fails immediately.
#
# Production needs two things:
#
# 1. CONNECTION POOL
#    Instead of one connection, keep a pool of 10.
#    When the API handles multiple simultaneous requests,
#    each request grabs a connection from the pool, uses it,
#    returns it. No request waits for another to finish.
#    Without this, under load, requests queue behind each other.
#
# 2. RETRY WITH EXPONENTIAL BACKOFF
#    If Redis has a momentary blip (restart, network hiccup),
#    don't fail immediately. Try again, wait a bit, try again,
#    wait a bit longer. The backoff prevents hammering a
#    struggling Redis with a flood of retries all at once.
#
#    Exponential backoff means:
#      attempt 1 fails → wait 0.1s
#      attempt 2 fails → wait 0.2s
#      attempt 3 fails → wait 0.4s
#    Each wait doubles. After 3 attempts, give up and return 503.
# =================================================================
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", None)

retry_policy = Retry(ExponentialBackoff(), retries=3)

r = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD,
    decode_responses=True,
    # Connection pool — 10 connections ready to use
    connection_pool=redis.ConnectionPool(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
        max_connections=10,
    ),
    # Retry on these specific transient errors only
    retry=retry_policy,
    retry_on_error=[
        redis.exceptions.ConnectionError,
        redis.exceptions.TimeoutError,
    ],
    # Socket timeout — don't hang forever waiting for Redis
    socket_timeout=5,
    socket_connect_timeout=5,
)


# =================================================================
# STARTUP VERIFICATION — lifespan
#
# FastAPI's lifespan runs code BEFORE the server starts accepting
# requests. We use it to verify Redis is reachable.
#
# Without this: the API starts, immediately accepts a request,
# tries to connect to Redis, fails, returns 503.
# The user sees an error on the very first request.
#
# With this: if Redis is unreachable at startup, the API
# refuses to start at all — the container exits with an error,
# Docker restarts it, and the health check never passes until
# Redis is genuinely available.
#
# This prevents the silent failure mode where the API is "running"
# but broken — it either works completely or doesn't start.
# =================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── STARTUP ──────────────────────────────────────────────────
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            r.ping()
            logger.info("Redis connection verified at startup.")
            break
        except redis.exceptions.ConnectionError:
            if attempt == max_retries:
                logger.error(
                    "Cannot connect to Redis after %d attempts. Refusing to start.",
                    max_retries
                )
                raise SystemExit(1)
            wait = 2 ** attempt  # 2s, 4s, 8s, 16s
            logger.warning(
                "Redis not ready (attempt %d/%d). Retrying in %ds...",
                attempt, max_retries, wait
            )
            time.sleep(wait)

    yield  # Application runs here

    # ── SHUTDOWN ─────────────────────────────────────────────────
    logger.info("API shutting down. Closing Redis connection pool.")
    r.connection_pool.disconnect()


app = FastAPI(lifespan=lifespan)


# =================================================================
# HEALTH CHECK
#
# Returns 200 only when Redis is reachable.
# Returns 503 when Redis is down — tells Docker/load balancer
# to stop sending traffic here until recovery.
#
# This is the ONLY endpoint Docker's healthcheck calls.
# It is also what a load balancer uses to decide whether to
# route requests to this instance.
# =================================================================
@app.get("/health")
def health():
    try:
        r.ping()
        return {"status": "ok", "redis": "connected", "service": "api"}
    except redis.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail={"status": "degraded", "redis": "unreachable"}
        )


# =================================================================
# JOB ENDPOINTS
# =================================================================
@app.post("/jobs")
def create_job():
    job_id = str(uuid.uuid4())
    try:
        r.lpush("job", job_id)
        r.hset(f"job:{job_id}", "status", "queued")
        logger.info("Job created", extra={"job_id": job_id})
        return {"job_id": job_id}
    except redis.exceptions.ConnectionError as e:
        logger.error("Redis unavailable creating job: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Queue unavailable. Try again shortly."
        )
    except Exception as e:
        logger.error("Unexpected error creating job: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    try:
        status = r.hget(f"job:{job_id}", "status")
        if not status:
            raise HTTPException(status_code=404, detail="Job not found.")
        return {"job_id": job_id, "status": status}
    except redis.exceptions.ConnectionError as e:
        logger.error("Redis unavailable fetching job %s: %s", job_id, e)
        raise HTTPException(
            status_code=503,
            detail="Queue unavailable. Try again shortly."
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Unexpected error fetching job %s: %s", job_id, e)
        raise HTTPException(status_code=500, detail="Internal server error.")
