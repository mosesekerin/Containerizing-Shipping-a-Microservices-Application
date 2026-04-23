import redis
from redis.retry import Retry
from redis.backoff import ExponentialBackoff
import time
import os
import signal
import logging
import sys
import json

# =================================================================
# STRUCTURED LOGGING — same format as API for unified log search
# =================================================================


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "service": "worker",
            "message": record.getMessage(),
        }
        if hasattr(record, 'job_id'):
            log_entry['job_id'] = record.job_id
        if hasattr(record, 'attempt'):
            log_entry['attempt'] = record.attempt
        if record.exc_info:
            log_entry['exception'] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(__name__)

# =================================================================
# CONFIGURATION
# =================================================================
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", None)

# How many times to retry a failed job before giving up
MAX_JOB_RETRIES = int(os.environ.get("MAX_JOB_RETRIES", 3))

# =================================================================
# REDIS CONNECTION WITH RETRY + POOL
# Same pattern as the API — consistency matters.
# =================================================================
retry_policy = Retry(ExponentialBackoff(), retries=3)

r = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD,
    decode_responses=True,
    retry=retry_policy,
    retry_on_error=[
        redis.exceptions.ConnectionError,
        redis.exceptions.TimeoutError,
    ],
    socket_timeout=5,
    socket_connect_timeout=5,
)


# =================================================================
# GRACEFUL SHUTDOWN
# =================================================================
shutdown_requested = False


def handle_shutdown(signum, frame):
    global shutdown_requested
    logger.info("Shutdown signal received. Finishing current job then exiting.")
    shutdown_requested = True


signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)


# =================================================================
# STARTUP VERIFICATION WITH EXPONENTIAL BACKOFF
#
# The worker must confirm Redis is reachable before entering
# the main loop. If Redis isn't ready, the worker waits with
# increasing delays rather than hammering it.
#
# Why exponential backoff instead of a flat retry?
# Flat: retry every 5 seconds forever → floods Redis with
#       connection attempts during an outage.
# Exponential: 2s, 4s, 8s, 16s, 32s → backs off gracefully,
#       gives Redis space to recover.
# =================================================================
def wait_for_redis():
    attempt = 0
    while not shutdown_requested:
        try:
            r.ping()
            logger.info(
                "Redis connection established. Worker ready.",
                extra={"host": REDIS_HOST, "port": REDIS_PORT}
            )
            return
        except redis.exceptions.ConnectionError:
            attempt += 1
            wait = min(2 ** attempt, 60)  # cap at 60 seconds
            logger.warning(
                "Redis not reachable (attempt %d). Retrying in %ds...",
                attempt, wait
            )
            time.sleep(wait)

    logger.info("Shutdown requested during startup. Exiting.")
    sys.exit(0)


# =================================================================
# DEAD LETTER QUEUE
#
# A "dead letter queue" (DLQ) is a holding area for jobs that
# have failed too many times to be retried again.
#
# Without a DLQ:
#   A bad job (one that always fails) stays in the main queue
#   forever. The worker picks it up, fails, re-queues it,
#   picks it up again, fails again — infinite loop consuming
#   resources and blocking other jobs.
#
# With a DLQ:
#   After MAX_JOB_RETRIES failures, the job is moved to a
#   separate "job:dead" queue. It stops blocking the main queue.
#   An operator can inspect it later and decide what to do.
#   The system keeps moving.
# =================================================================
def send_to_dead_letter(job_id: str, reason: str):
    try:
        r.hset(f"job:{job_id}", mapping={
            "status": "failed",
            "reason": reason,
        })
        r.lpush("job:dead", job_id)
        logger.error(
            "Job sent to dead letter queue after %d attempts: %s",
            MAX_JOB_RETRIES, reason,
            extra={"job_id": job_id}
        )
    except Exception as e:
        logger.error(
            "Could not send job to DLQ: %s", e,
            extra={"job_id": job_id}
        )


# =================================================================
# JOB PROCESSING WITH RETRY TRACKING
#
# Each job gets a retry counter stored in Redis alongside its
# status. Every time processing fails, the counter increments.
# At MAX_JOB_RETRIES, the job goes to the dead letter queue.
#
# retry_count is stored IN Redis (not in memory) so it survives
# worker restarts. If the worker crashes after 2 retries and
# restarts, it correctly reads retry_count=2 and knows the
# job has already been attempted twice.
# =================================================================
def process_job(job_id: str):
    logger.info("Processing job.", extra={"job_id": job_id})

    # Read current retry count from Redis (0 if first attempt)
    retry_count = int(r.hget(f"job:{job_id}", "retry_count") or 0)

    if retry_count >= MAX_JOB_RETRIES:
        send_to_dead_letter(job_id, f"Exceeded {MAX_JOB_RETRIES} retries")
        return

    try:
        # ── DO THE ACTUAL WORK ───────────────────────────────────
        # In a real system this might be: resize an image, send an
        # email, call an external API, run a database migration.
        # Here we simulate 2 seconds of work.
        time.sleep(2)

        # ── WRITE RESULT ─────────────────────────────────────────
        r.hset(f"job:{job_id}", mapping={
            "status": "completed",
            "retry_count": retry_count,
        })
        logger.info("Job completed.", extra={"job_id": job_id})

    except redis.exceptions.ConnectionError:
        # Redis is unavailable — we can't even write the result.
        # Re-queue the job so it isn't lost. It will be picked up
        # when Redis recovers.
        logger.error(
            "Redis unavailable writing result. Re-queuing.",
            extra={"job_id": job_id}
        )
        try:
            r.lpush("job", job_id)
            logger.warning("Job re-queued.", extra={"job_id": job_id})
        except Exception as requeue_err:
            logger.error(
                "Could not re-queue job: %s", requeue_err,
                extra={"job_id": job_id}
            )

    except Exception as e:
        # Something unexpected failed during processing.
        # Increment the retry counter and re-queue.
        new_retry_count = retry_count + 1
        logger.error(
            "Job processing failed (attempt %d/%d): %s",
            new_retry_count, MAX_JOB_RETRIES, e,
            extra={"job_id": job_id, "attempt": new_retry_count}
        )
        try:
            r.hset(f"job:{job_id}", mapping={
                "status": "retrying",
                "retry_count": new_retry_count,
            })
            r.lpush("job", job_id)
        except Exception as requeue_err:
            logger.error(
                "Could not re-queue after failure: %s", requeue_err,
                extra={"job_id": job_id}
            )


# =================================================================
# MAIN LOOP
# =================================================================
logger.info(
    "Worker starting. Connecting to Redis at %s:%d",
    REDIS_HOST, REDIS_PORT
)

wait_for_redis()

while not shutdown_requested:
    try:
        # BRPOP blocks for up to 5 seconds.
        # The short timeout means we re-check shutdown_requested
        # every 5 seconds even when the queue is empty —
        # so the worker responds to SIGTERM within 5 seconds.
        result = r.brpop("job", timeout=5)

        if result:
            _, job_id = result
            process_job(job_id)

    except redis.exceptions.ConnectionError as e:
        # ==========================================================
        # REDIS WENT DOWN DURING OPERATION
        #
        # This is the critical recovery path.
        # The worker lost its Redis connection mid-operation.
        # It does NOT crash. It logs the error, waits with
        # exponential backoff, and tries to reconnect.
        #
        # From the user's perspective: jobs stop being processed
        # for a short time, then resume automatically.
        # No human intervention needed.
        # ==========================================================
        logger.error("Lost Redis connection: %s. Reconnecting...", e)
        wait_for_redis()

    except Exception as e:
        logger.error("Unexpected loop error: %s. Continuing in 1s...", e)
        time.sleep(1)


logger.info("Worker shut down cleanly.")
sys.exit(0)
