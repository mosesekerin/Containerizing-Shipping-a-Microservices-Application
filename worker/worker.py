import redis
import time
import os
import signal
import logging
import sys

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# FIX 2: Read Redis config from environment.
# FIX 5b: Include password from environment.
# ─────────────────────────────────────────────
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", None)

r = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD,
    decode_responses=True
)

# ─────────────────────────────────────────────
# FIX 6: Graceful shutdown.
#
# When Docker stops a container it sends SIGTERM
# first — "please wrap up." If the process
# doesn't stop within 10s, Docker sends SIGKILL
# — "get out now."
#
# Without this fix: a job gets pulled off the
# queue by BRPOP, the worker is killed mid-
# processing, the job is lost forever — stuck
# as "queued" in Redis with no one to finish it.
#
# With this fix: the worker finishes its current
# job, then exits cleanly on the next loop tick.
# ─────────────────────────────────────────────
shutdown_requested = False

def handle_shutdown(signum, frame):
    global shutdown_requested
    logger.info("Shutdown signal received. Finishing current job then exiting...")
    shutdown_requested = True

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)


# ─────────────────────────────────────────────
# FIX 8: Error handling inside process_job.
# If the Redis write fails, we log it and
# re-queue the job rather than losing it.
# The worker process itself does NOT crash.
# ─────────────────────────────────────────────
def process_job(job_id):
    logger.info(f"Processing job: {job_id}")
    try:
        time.sleep(2)  # simulate work
        r.hset(f"job:{job_id}", "status", "completed")
        logger.info(f"Completed job: {job_id}")
    except redis.exceptions.ConnectionError as e:
        logger.error(f"Redis failed writing result for job {job_id}: {e}")
        # Re-queue the job so it isn't lost
        try:
            r.lpush("job", job_id)
            logger.warning(f"Re-queued job {job_id} after Redis failure.")
        except Exception as requeue_err:
            logger.error(f"Could not re-queue job {job_id}: {requeue_err}")
    except Exception as e:
        logger.error(f"Unexpected error processing job {job_id}: {e}")


# ─────────────────────────────────────────────
# Main loop — runs until shutdown is requested
# ─────────────────────────────────────────────
logger.info(f"Worker started. Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")

while not shutdown_requested:
    try:
        # BRPOP blocks for up to 5 seconds waiting for a job.
        # The short timeout means we re-check shutdown_requested
        # every 5 seconds even if the queue is empty.
        job = r.brpop("job", timeout=5)
        if job:
            _, job_id = job
            process_job(job_id)
    except redis.exceptions.ConnectionError as e:
        logger.error(f"Lost connection to Redis: {e}. Retrying in 5s...")
        time.sleep(5)
    except Exception as e:
        logger.error(f"Unexpected loop error: {e}")
        time.sleep(1)

logger.info("Worker shut down cleanly.")
sys.exit(0)
