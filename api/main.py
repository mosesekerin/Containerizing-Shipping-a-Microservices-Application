from fastapi import FastAPI, HTTPException
import redis
import uuid
import os
import logging

# ─────────────────────────────────────────────
# Logging — structured output for production
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI()

# ─────────────────────────────────────────────
# FIX 1 & 5: Read connection config from
# environment variables — never hardcode.
# FIX 5b: Read the Redis password from env too.
# ─────────────────────────────────────────────
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", None)

r = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD,
    decode_responses=True   # avoids manual .decode() on every read
)


# ─────────────────────────────────────────────
# FIX (new): Health check endpoint.
# Docker and load balancers need this to know
# whether the service is actually ready.
# It checks the real Redis connection, not just
# whether the process is running.
# ─────────────────────────────────────────────
@app.get("/health")
def health():
    try:
        r.ping()
        return {"status": "ok", "redis": "connected"}
    except redis.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Redis unavailable")


# ─────────────────────────────────────────────
# FIX 7: Wrap every Redis call in try/except.
# Return clean JSON errors — never let raw
# Python tracebacks reach the client.
# ─────────────────────────────────────────────
@app.post("/jobs")
def create_job():
    job_id = str(uuid.uuid4())
    try:
        r.lpush("job", job_id)
        r.hset(f"job:{job_id}", "status", "queued")
        logger.info(f"Job created: {job_id}")
        return {"job_id": job_id}
    except redis.exceptions.ConnectionError as e:
        logger.error(f"Redis connection failed: {e}")
        raise HTTPException(status_code=503, detail="Queue unavailable. Try again shortly.")
    except Exception as e:
        logger.error(f"Unexpected error creating job: {e}")
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    try:
        status = r.hget(f"job:{job_id}", "status")
        if not status:
            raise HTTPException(status_code=404, detail="Job not found.")
        return {"job_id": job_id, "status": status}
    except redis.exceptions.ConnectionError as e:
        logger.error(f"Redis connection failed: {e}")
        raise HTTPException(status_code=503, detail="Queue unavailable. Try again shortly.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error fetching job {job_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error.")
