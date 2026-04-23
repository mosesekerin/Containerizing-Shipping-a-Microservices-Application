# Containerizing & Shipping a Microservices Application

A fully containerized, production-grade job-processing system built with **FastAPI**, **Redis**, **Express**, and **Docker Compose**. The system accepts jobs through a web frontend, queues them via Redis, and processes them asynchronously in a background worker — all wired together with health-gated startup ordering and zero hardcoded values.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Service Interaction Flow](#service-interaction-flow)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Setup from Scratch](#setup-from-scratch)
- [Environment Variables](#environment-variables)
- [Running the System](#running-the-system)
- [Verifying the System Works](#verifying-the-system-works)
- [CI/CD Pipeline](#cicd-pipeline)
- [Operational Reference](#operational-reference)
- [Security Notes](#security-notes)

---

## Architecture Overview

The application is composed of four independent services, each running in its own Docker container and communicating exclusively over a private internal network. No service shares a process or filesystem with another.

```
┌─────────────────────────────────────────────────────────────────┐
│                        app-network (bridge)                     │
│                       subnet: 172.28.0.0/16                     │
│                                                                 │
│  ┌──────────────┐     ┌──────────────┐     ┌────────────────┐  │
│  │   frontend   │────▶│     api      │────▶│     redis      │  │
│  │  (Express)   │     │  (FastAPI)   │     │  (Queue+Store) │  │
│  │  port 3000   │     │  port 8000   │     │  port 6379     │  │
│  └──────────────┘     └──────────────┘     └────────────────┘  │
│         ▲                                          ▲            │
│         │ (user traffic)               ┌──────────┘            │
│         │                              │                        │
│  ┌──────┴──────────────────────────────┴──┐                    │
│  │              worker (Python)            │                    │
│  │         (no inbound ports)              │                    │
│  └─────────────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────────┘

       HOST MACHINE
       port 3000 ──▶ frontend
       port 8000 ──▶ api  (dev/debug only)
```

### Services at a Glance

| Service    | Technology     | Role                                               | Exposed Port  |
|------------|----------------|----------------------------------------------------|---------------|
| `redis`    | Redis (custom) | Message queue and job state store                  | None (internal only) |
| `api`      | FastAPI/Python | Accepts job submissions, reports job status        | `8000` (host) |
| `worker`   | Python         | Polls Redis queue, processes jobs asynchronously   | None          |
| `frontend` | Express/Node   | Serves the UI and proxies requests to the API      | `3000` (host) |

### Key Design Decisions

**Health-gated startup order.** Services start in a strict chain enforced by Docker healthchecks, not just container readiness:

```
redis (healthy) → api (healthy) → frontend + worker (simultaneously)
```

This prevents race conditions where a service tries to connect before its dependency is actually ready.

**No hardcoded addresses.** Every inter-service hostname is resolved by Docker's internal DNS using the service name (e.g. `redis`, `api`). All configuration flows through environment variables — nothing is baked into the images.

**Isolated network.** Redis has no `ports:` mapping. It is completely unreachable from the host machine or the internet — only accessible to containers on `app-network`.

**Persistent state.** Redis data is stored in a named Docker volume (`redis-data`), so queued jobs survive container restarts and redeployments.

**Graceful shutdown.** The worker catches `SIGTERM` and `SIGINT`, finishes any in-progress job, then exits cleanly. No jobs are lost during rolling restarts.

---

## Service Interaction Flow

### Submitting a Job

```
User (browser)
    │
    │  POST /submit  { "data": "..." }
    ▼
frontend (Express, port 3000)
    │
    │  POST http://api:8000/jobs
    ▼
api (FastAPI, port 8000)
    │  1. Generates a UUID job_id
    │  2. LPUSH job_id → Redis queue "job"
    │  3. HSET job:{job_id} status=queued
    │
    │  Returns { "job_id": "abc-123" }
    ▼
frontend
    │
    │  Returns { "job_id": "abc-123" } to browser
    ▼
User (browser)
```

### Processing a Job

```
worker (Python, background loop)
    │
    │  BRPOP "job" (blocks until a job arrives, timeout=5s)
    ▼
Redis
    │  Returns job_id
    ▼
worker
    │  1. Processes the job (simulate work, 2s)
    │  2. HSET job:{job_id} status=completed
    │
    └─ Loop continues, waiting for next job
```

### Checking Job Status

```
User (browser)
    │
    │  GET /status/{job_id}
    ▼
frontend (Express)
    │
    │  GET http://api:8000/jobs/{job_id}
    ▼
api (FastAPI)
    │
    │  HGET job:{job_id} status
    ▼
Redis
    │  Returns "queued" | "completed" | (not found)
    ▼
api → frontend → browser
```

### Health Check Chain

Docker evaluates healthchecks on a configured interval. The startup sequence is:

```
1. redis container starts
   └─ Healthcheck: redis-cli -a $REDIS_PASSWORD ping
      └─ Must return PONG before api is allowed to start

2. api container starts
   └─ Healthcheck: curl -f http://localhost:8000/health
      └─ Must return HTTP 200 before frontend/worker are allowed to start

3. frontend + worker start simultaneously
   └─ Both depend on api: service_healthy
```

---

## Project Structure

```
.
├── api/                        # FastAPI service
│   ├── Dockerfile
│   ├── main.py                 # Job submission + status endpoints + /health
│   └── requirements.txt        # Pinned Python dependencies
│
├── frontend/                   # Express proxy + static UI
│   ├── Dockerfile
│   ├── app.js                  # Proxy routes to API, serves static files
│   └── package.json            # Pinned Node dependencies
│
├── redis/                      # Custom Redis image
│   ├── Dockerfile
│   └── redis.conf              # Production Redis configuration
│
├── worker/                     # Background job processor
│   ├── Dockerfile
│   ├── worker.py               # BRPOP loop with graceful shutdown
│   └── requirements.txt        # Pinned Python dependencies
│
├── docker-compose.yml          # Full orchestration definition
├── .env.example                # Safe template — copy to .env and fill in values
├── .gitignore                  # Excludes .env files from git
├── FIXES.md                    # Bug fix register documenting all issues resolved
└── README.md                   # This file
```

---

## Prerequisites

Ensure the following are installed on your machine before continuing:

| Tool | Minimum Version | Check |
|------|----------------|-------|
| Docker | 24.x | `docker --version` |
| Docker Compose | v2 (included with Docker Desktop) | `docker compose version` |
| Git | Any recent version | `git --version` |

> **Note:** Docker Compose v2 uses `docker compose` (no hyphen). If your system only has v1 (`docker-compose`), update Docker or install the v2 plugin.

---

## Setup from Scratch

### 1. Clone the repository

```bash
git clone https://github.com/mosesekerin/Containerizing-Shipping-a-Microservices-Application.git
cd Containerizing-Shipping-a-Microservices-Application
```

### 2. Create your environment file

The application requires a `.env` file at the repo root. A template is provided:

```bash
cp .env.example .env
```

Open `.env` in your editor and fill in the values:

```bash
# .env
REDIS_PASSWORD=your_strong_password_here   # Change this — use a real random password
REDIS_PORT=6379
API_PORT=8000
FRONTEND_PORT=3000
APP_ENV=production
```

> **Important:** Never commit `.env` to git. It is listed in `.gitignore` and must stay there.

To generate a strong random password:
```bash
openssl rand -base64 32
```

### 3. Add the API healthcheck endpoint (required)

The `frontend` and `worker` services depend on the `api` being **healthy**, which requires a `healthcheck` block in `docker-compose.yml` and a `/health` route in `api/main.py`.

Ensure `api/main.py` contains:

```python
@app.get("/health")
def health():
    try:
        r.ping()
        return {"status": "ok", "redis": "connected"}
    except redis.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Redis unavailable")
```

And `docker-compose.yml` has this block under the `api` service:

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  interval: 10s
  timeout: 5s
  start_period: 30s
  retries: 5
```

If `curl` is not in your API image, use `wget`:
```yaml
test: ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://localhost:8000/health"]
```

### 4. Build and start the system

```bash
docker compose up --build
```

On first run, Docker will pull base images and build all four service images. This takes 2–5 minutes depending on your connection. Subsequent starts are much faster.

To run in detached (background) mode:

```bash
docker compose up --build -d
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_PASSWORD` | *(required)* | Password for Redis authentication. Must match across all services. |
| `REDIS_PORT` | `6379` | Redis port. Rarely needs changing. |
| `API_PORT` | `8000` | Host port the API is exposed on. |
| `FRONTEND_PORT` | `3000` | Host port the frontend is exposed on. |
| `APP_ENV` | `production` | Application environment flag. |

All variables are read from the `.env` file by Docker Compose automatically. Services receive them via the `environment:` block in `docker-compose.yml`.

---

## Running the System

### Start all services

```bash
docker compose up -d
```

### Stop all services

```bash
docker compose down
```

### Stop and remove all data (including the Redis volume)

```bash
docker compose down -v
```

> **Warning:** `-v` permanently deletes the `redis-data` volume. All queued and completed job records are lost.

### Rebuild after code changes

```bash
docker compose up --build -d
```

### Restart a single service

```bash
docker compose restart api
```

### View logs

```bash
# All services
docker compose logs -f

# A specific service
docker compose logs -f worker
docker compose logs -f api
```

### Open a shell inside a container

```bash
docker exec -it api bash
docker exec -it worker bash
docker exec -it redis redis-cli -a $REDIS_PASSWORD
```

---

## Verifying the System Works

### 1. Check all containers are running and healthy

```bash
docker compose ps
```

Expected output — all services should show `healthy` or `running`:

```
NAME        IMAGE     STATUS              PORTS
redis       redis     Up (healthy)        
api         api       Up (healthy)        0.0.0.0:8000->8000/tcp
worker      worker    Up                  
frontend    frontend  Up                  0.0.0.0:3000->3000/tcp
```

### 2. Open the frontend

Navigate to [http://localhost:3000](http://localhost:3000) in your browser.

### 3. Check the API health endpoint directly

```bash
curl http://localhost:8000/health
# Expected: {"status":"ok","redis":"connected"}
```

### 4. Submit a job via the API

```bash
curl -X POST http://localhost:8000/jobs
# Expected: {"job_id":"some-uuid-here"}
```

### 5. Check the job status

```bash
curl http://localhost:8000/jobs/{job_id}
# Expected: {"status":"queued"} or {"status":"completed"}
```

### 6. Inspect Redis directly

```bash
docker exec -it redis redis-cli -a $REDIS_PASSWORD

# List all job keys
KEYS job:*

# Check a specific job's status
HGETALL job:{job_id}

# Check queue depth
LLEN job
```

---

## CI/CD Pipeline

The GitHub Actions pipeline runs on every push and pull request to `main`. It executes five stages in sequence:

| Stage | What it does |
|-------|-------------|
| **Lint** | Runs code style checks (Python: `flake8`, JS: `eslint`) |
| **Test** | Runs unit tests for the API and worker |
| **Build** | Builds all Docker images to verify Dockerfiles are valid |
| **Security** | Scans images for known CVEs |
| **Integration** | Spins up the full stack with Docker Compose and tests end-to-end behaviour |

### Integration test requirements

For the integration stage to pass, the `api` service **must** have a `healthcheck` defined in `docker-compose.yml` (see [Setup Step 3](#3-add-the-api-healthcheck-endpoint-required)). Without it, services that depend on `api: condition: service_healthy` will timeout and fail with:

```
dependency failed to start: container api is unhealthy
```

---

## Operational Reference

### Resource limits

Each service has CPU and memory limits defined in `docker-compose.yml` to prevent any single container from starving the others:

| Service  | CPU Limit | Memory Limit |
|----------|-----------|--------------|
| redis    | 0.50      | 256 MB       |
| api      | 0.75      | 256 MB       |
| worker   | 0.50      | 128 MB       |
| frontend | 0.50      | 128 MB       |

### Restart policy

All services use `restart: on-failure:5` — they automatically restart on error, but stop after 5 consecutive failures to avoid an infinite crash loop.

### Log rotation

All services use Docker's `json-file` log driver with rotation configured to keep at most 3 files of 10 MB each, preventing disk exhaustion on long-running deployments.

---

## Security Notes

- **Redis is not exposed to the host.** The `redis` service has no `ports:` mapping. It is only reachable by containers on `app-network`.
- **Passwords are never hardcoded.** All secrets are injected at runtime via environment variables from `.env`, which is excluded from git.
- **`.env` must never be committed.** If it ever is, treat the credentials as compromised and rotate them immediately. See `FIXES.md` for the git history purge procedure.
- **Dependencies are pinned.** All `requirements.txt` and `package.json` entries use exact versions to ensure reproducible, auditable builds.

For a full record of all bugs found and fixed in the original codebase, see [`FIXES.md`](./FIXES.md).
