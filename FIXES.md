# FIXES.md
## Bug Fix Register — hng14-stage2-devops

This document records every bug found in the original codebase,
its root cause, the exact fix applied, and why it matters in a
real production system.

---

## Summary Table

| # | Severity | File | Issue | Status |
|---|----------|------|-------|--------|
| 1 | 🔴 Critical | `api/main.py` | Redis host hardcoded to `localhost` | Fixed |
| 2 | 🔴 Critical | `worker/worker.py` | Redis host hardcoded to `localhost` | Fixed |
| 3 | 🔴 Critical | `frontend/app.js` | API URL hardcoded to `localhost` | Fixed |
| 4 | 🔴 Critical | `api/.env` + repo root | `.env` committed, no `.gitignore` | Fixed |
| 5 | 🔴 Critical | `api/main.py` | Redis password loaded but never used | Fixed |
| 6 | 🟠 High | `worker/worker.py` | No graceful shutdown — jobs lost on SIGTERM | Fixed |
| 7 | 🟠 High | `api/main.py` | No error handling — raw tracebacks exposed | Fixed |
| 8 | 🟠 High | `worker/worker.py` | No error handling — worker crashes on Redis blip | Fixed |
| 9 | 🟡 Medium | all `requirements.txt`, `package.json` | Unpinned dependencies | Fixed |

---

---

## Bug 1

**Severity:** 🔴 Critical
**File:** `api/main.py`
**Line:** 8

### Original Code
```python
r = redis.Redis(host="localhost", port=6379)
```

### Root Cause
`localhost` (also written as `127.0.0.1`) means "this machine's own
loopback address." When the API runs inside a Docker container,
`localhost` points back to the API container itself — not to the
Redis container. Redis is not running inside the API container.
The connection fails immediately on the first job request.

### What the failure looks like
```
redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379
```

### Fix Applied
```python
# api/main.py
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT)
```

In `docker-compose.yml`, the Redis service is named `redis`.
Docker's internal DNS automatically resolves that name to the
correct container IP. The API container sets `REDIS_HOST=redis`
via environment variable — no hardcoding, no guessing.

### Why It Matters in Production
Every microservice deployment (Docker, Kubernetes, cloud) uses
hostname-based service discovery. Hardcoded addresses break
the moment you move off a single machine. This is one of the
most common "works on my laptop, fails in prod" bugs.

---

## Bug 2

**Severity:** 🔴 Critical
**File:** `worker/worker.py`
**Line:** 6

### Original Code
```python
r = redis.Redis(host="localhost", port=6379)
```

### Root Cause
Identical to Bug 1. The worker runs in its own container.
`localhost` points to the worker container, not to Redis.
The worker silently fails to connect and never processes any jobs.
No error is raised at startup because the Redis client library
uses lazy connection — it only actually tries to connect when
you make the first call (BRPOP).

### Fix Applied
```python
# worker/worker.py
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT)
```

### Why It Matters in Production
The worker is a background process. It doesn't serve HTTP traffic.
If it fails silently, there is no visible error — jobs simply pile
up in the queue and never get processed. This is the worst kind
of failure: silent and invisible until a user complains.

---

## Bug 3

**Severity:** 🔴 Critical
**File:** `frontend/app.js`
**Line:** 6

### Original Code
```javascript
const API_URL = "http://localhost:8000";
```

### Root Cause
Same class of problem as Bugs 1 and 2. When the frontend Express
server runs in its own container and tries to call
`http://localhost:8000`, it's calling itself — there is no
FastAPI server inside the frontend container.

Every call to `/submit` and `/status/:id` throws a
`ECONNREFUSED` error, which the catch block silently swallows
and returns a generic "something went wrong" to the browser.

### Fix Applied
```javascript
// frontend/app.js
const API_URL = process.env.API_URL || "http://localhost:8000";
```

The `docker-compose.yml` sets `API_URL=http://api:8000` for the
frontend container. `api` resolves to the FastAPI container via
Docker's internal DNS.

The fallback `http://localhost:8000` is preserved so developers
can still run the frontend locally without Docker by not setting
the env var.

### Why It Matters in Production
The frontend is the user-facing entry point. If it can't reach
the API, the entire application is broken for every user — but
the broken part is invisible from the outside (the page loads
fine). You'd only know from logs or user complaints.

---

## Bug 4

**Severity:** 🔴 Critical
**Files:** `api/.env` (committed), repo root (no `.gitignore`)

### Original Code
```
# api/.env  ← this file exists in the git history
REDIS_PASSWORD=supersecretpassword123
APP_ENV=production
```

### Root Cause
There is no `.gitignore` file in the repository. The developer
created a `.env` file containing a real password and committed
it. It was then pushed to a **public** GitHub repository.

This means `supersecretpassword123` is permanently in the git
history and visible to anyone on the internet. Even if the file
is deleted in a future commit, the password remains readable in
the git log.

### Fix Applied

1. Added `.gitignore` to the repo root containing:
```
.env
*.env
.env.*
```

2. Created `.env.example` — a safe template file with placeholder
values that is safe to commit:
```
REDIS_PASSWORD=your_strong_password_here
```

3. The real `.env` is never committed. It is created locally by
each developer from the `.env.example` template.

4. In production, secrets are injected by the deployment system
(Docker Compose, Kubernetes Secrets, or a secrets manager) —
never stored in files that touch the codebase.

### Why It Matters in Production
Leaked credentials are one of the top causes of data breaches.
Attackers actively scan GitHub for committed secrets using
automated tools. Once a secret is in git history, it must be
treated as permanently compromised — even after deletion.
The password must be rotated immediately.

---

## Bug 5

**Severity:** 🔴 Critical
**File:** `api/main.py`
**Line:** 4 and 8

### Original Code
```python
import os                                          # imported
r = redis.Redis(host="localhost", port=6379)       # os never used
```
```
# api/.env
REDIS_PASSWORD=supersecretpassword123             # defined but never read
```

### Root Cause
The `.env` file defines `REDIS_PASSWORD` but `main.py` never
reads it. `os` is imported but `os.environ.get("REDIS_PASSWORD")`
is never called. The Redis client is created with no `password`
argument, so authentication is never sent.

This creates two possible failure states:
- If Redis has a password configured: every connection attempt
  fails with `WRONGPASS` — the entire API is broken.
- If Redis has no password: the `.env` variable is security
  theatre — it looks like auth is configured but it isn't.

### Fix Applied
```python
# api/main.py
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", None)

r = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD,
    decode_responses=True
)
```

The same fix is applied to `worker/worker.py`.

`decode_responses=True` is also added here — this tells the Redis
client to automatically decode byte responses to strings,
removing the need for manual `.decode()` calls on every read.

### Why It Matters in Production
Redis without authentication is open to anyone who can reach it
on the network. In a cloud environment, a misconfigured firewall
could expose an unauthenticated Redis instance to the internet —
a well-known attack vector used to steal data and install
cryptominers.

---

## Bug 6

**Severity:** 🟠 High
**File:** `worker/worker.py`
**Lines:** 4, entire loop

### Original Code
```python
import signal   # imported but never used

while True:
    job = r.brpop("job", timeout=5)
    if job:
        _, job_id = job
        process_job(job_id.decode())
    # No shutdown handling — runs forever until killed
```

### Root Cause
`signal` is imported but never connected to any handler.
When Docker stops a container it sends `SIGTERM` to the main
process. If `SIGTERM` is not handled, Python's default behaviour
is to raise `SystemExit` immediately — interrupting whatever is
currently executing.

If the worker is mid-job when SIGTERM arrives:
1. `BRPOP` has already removed the job from the Redis queue
2. `process_job()` is interrupted before `HSET status=completed`
3. The job ID is now in neither the queue nor a completed state
4. It is permanently lost — stuck as `queued` with no worker
   ever picking it up again

### Fix Applied
```python
# worker/worker.py
shutdown_requested = False

def handle_shutdown(signum, frame):
    global shutdown_requested
    logger.info("Shutdown signal received. Finishing current job then exiting...")
    shutdown_requested = True

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

while not shutdown_requested:
    job = r.brpop("job", timeout=5)
    if job:
        _, job_id = job
        process_job(job_id)

logger.info("Worker shut down cleanly.")
sys.exit(0)
```

When SIGTERM arrives, `shutdown_requested` is set to `True`.
The current job completes normally. On the next loop iteration,
`while not shutdown_requested` evaluates to False and the
loop exits cleanly.

The `timeout=5` on BRPOP means even if the queue is empty,
the loop checks `shutdown_requested` every 5 seconds — so the
worker responds to a shutdown signal within 5 seconds maximum.

### Why It Matters in Production
Rolling deployments, auto-scaling, and container restarts all
involve sending SIGTERM to running containers. Without graceful
shutdown, every deployment risks losing in-flight work. In a
payment or order processing system, this means lost transactions.

---

## Bug 7

**Severity:** 🟠 High
**File:** `api/main.py`
**Lines:** 11–14, 17–20

### Original Code
```python
@app.post("/jobs")
def create_job():
    job_id = str(uuid.uuid4())
    r.lpush("job", job_id)             # unhandled ConnectionError
    r.hset(f"job:{job_id}", "status", "queued")  # unhandled
    return {"job_id": job_id}
```

### Root Cause
No `try/except` block wraps the Redis calls. If Redis is
unavailable (container restart, network blip, OOM kill), these
lines raise `redis.exceptions.ConnectionError`. FastAPI catches
unhandled exceptions and returns HTTP 500 with a full Python
traceback in the response body.

A traceback reveals: file paths, library names and versions,
internal logic, and line numbers. This is a free vulnerability
map for an attacker.

### Fix Applied
```python
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
```

The real error is logged server-side (visible to operators,
not users). The client receives a clean, safe JSON response
with an appropriate HTTP status code (503 = service unavailable).

A `/health` endpoint is also added:
```python
@app.get("/health")
def health():
    try:
        r.ping()
        return {"status": "ok", "redis": "connected"}
    except redis.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Redis unavailable")
```

### Why It Matters in Production
The difference between a 500 with a traceback and a 503 with
a clean message is the difference between a security incident
and a handled failure. Clean error responses also allow the
frontend to show the user a meaningful message rather than
crashing or displaying raw JSON garbage.

---

## Bug 8

**Severity:** 🟠 High
**File:** `worker/worker.py`
**Lines:** 8–11

### Original Code
```python
def process_job(job_id):
    print(f"Processing job {job_id}")
    time.sleep(2)
    r.hset(f"job:{job_id}", "status", "completed")  # unhandled
    print(f"Done: {job_id}")
```

### Root Cause
If `r.hset()` raises `ConnectionError` (Redis unavailable at
the moment of writing the result), the exception propagates up
to the main `while True` loop, which also has no handler.
The entire worker process crashes and exits.

No more jobs will ever be processed until the container is
manually restarted or Docker's restart policy brings it back up.
Even then, the job that failed is lost.

### Fix Applied
```python
def process_job(job_id):
    logger.info(f"Processing job: {job_id}")
    try:
        time.sleep(2)
        r.hset(f"job:{job_id}", "status", "completed")
        logger.info(f"Completed job: {job_id}")
    except redis.exceptions.ConnectionError as e:
        logger.error(f"Redis failed for job {job_id}: {e}")
        try:
            r.lpush("job", job_id)    # re-queue the job
            logger.warning(f"Re-queued job {job_id} after Redis failure.")
        except Exception as requeue_err:
            logger.error(f"Could not re-queue job {job_id}: {requeue_err}")
    except Exception as e:
        logger.error(f"Unexpected error processing job {job_id}: {e}")
```

The outer loop also catches `ConnectionError` to prevent the
worker from dying on a Redis blip:
```python
while not shutdown_requested:
    try:
        job = r.brpop("job", timeout=5)
        ...
    except redis.exceptions.ConnectionError as e:
        logger.error(f"Lost connection to Redis: {e}. Retrying in 5s...")
        time.sleep(5)
```

### Why It Matters in Production
Workers are long-running processes. A single unhandled exception
that kills the process means the entire background processing
pipeline stops — silently. With error handling, a Redis blip
causes a logged warning and a 5-second pause, then the worker
automatically resumes. No human intervention needed.

---

## Bug 9

**Severity:** 🟡 Medium
**Files:** `api/requirements.txt`, `worker/requirements.txt`,
           `frontend/package.json`

### Original Code
```
# api/requirements.txt
fastapi
uvicorn
redis

# worker/requirements.txt
redis

# frontend/package.json
"express": "^4.18.2",
"axios": "^1.4.0"
```

### Root Cause
Python `requirements.txt` entries without `==version` always
install the **latest** available version at build time.
JavaScript `^` ranges allow minor and patch updates
(e.g. `^4.18.2` permits `4.19.x`, `4.20.x`, etc.).

This means two builds on different days can install different
versions. A library update between your last successful deploy
and today's deploy can silently break the application.

### Fix Applied
```
# api/requirements.txt
fastapi==0.115.14
uvicorn==0.34.3
redis==7.4.0

# worker/requirements.txt
redis==7.4.0

# frontend/package.json
"express": "4.18.2",
"axios": "1.4.0"
```

All caret (`^`) and tilde (`~`) ranges are removed from
`package.json`. Exact versions are specified everywhere.

### Why It Matters in Production
Reproducible builds are a foundational principle of reliable
software delivery. "It worked in staging" should mean it will
work in production — but only if both environments install
identical code. Unpinned dependencies are a leading cause of
"nothing changed but it broke" incidents.

---

## Files Changed

| File | Change Type |
|------|-------------|
| `api/main.py` | Modified — env vars, error handling, health endpoint |
| `api/requirements.txt` | Modified — pinned versions |
| `worker/worker.py` | Modified — env vars, graceful shutdown, error handling |
| `worker/requirements.txt` | Modified — pinned version |
| `frontend/app.js` | Modified — env var for API URL, health endpoint |
| `frontend/package.json` | Modified — pinned versions |
| `.gitignore` | **Created** — prevents secrets being committed |
| `.env.example` | **Created** — safe template for environment variables |
| `api/.env` | **Must be removed from git history** — contains leaked secret |

---

## Immediate Actions Required

1. **Rotate the Redis password.** `supersecretpassword123` is
   permanently in the public git history. Any system using this
   password must be considered compromised. Generate a new strong
   password immediately.

2. **Remove `api/.env` from git history.**
   Deleting the file in a new commit is not enough — it remains
   readable in `git log`. Use:
   ```bash
   git filter-branch --force --index-filter \
     "git rm --cached --ignore-unmatch api/.env" \
     --prune-empty --tag-name-filter cat -- --all
   git push origin --force
   ```
   Or use the `git-filter-repo` tool (recommended over
   filter-branch for modern git).

3. **Add `.gitignore` before any future commits.**
   The `.gitignore` created in this fix set must be the very
   first thing committed going forward.
