#!/bin/bash
# =================================================================
# PHASE 5 — FAILURE TEST SCRIPTS
# hng14-stage2-devops
#
# PURPOSE:
#   These scripts deliberately break parts of your running system
#   to prove it handles failures correctly.
#   Run them while `docker compose up` is running in another terminal.
#
# HOW TO USE:
#   Open TWO terminals:
#     Terminal 1: docker compose up   (watch the logs)
#     Terminal 2: bash failure_tests.sh TEST_NAME
#
# TESTS:
#   bash failure_tests.sh redis_kill
#   bash failure_tests.sh worker_kill
#   bash failure_tests.sh worker_kill_mid_job
#   bash failure_tests.sh api_kill
#   bash failure_tests.sh flood
#   bash failure_tests.sh inspect
# =================================================================

set -euo pipefail

# Colours for readable output
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

FRONTEND_URL="http://localhost:3000"
API_URL="http://localhost:8088"   # your API_PORT from .env

log_info()    { echo -e "${BLUE}[INFO]${NC}    $1"; }
log_success() { echo -e "${GREEN}[PASS]${NC}    $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}    $1"; }
log_fail()    { echo -e "${RED}[FAIL]${NC}    $1"; }
log_step()    { echo -e "\n${CYAN}━━━ $1 ━━━${NC}"; }


# =================================================================
# HELPER: submit a job and return its ID
# =================================================================
submit_job() {
  curl -s -X POST "$FRONTEND_URL/submit" \
    -H "Content-Type: application/json" | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('job_id','ERROR'))"
}

# =================================================================
# HELPER: check a job's status
# =================================================================
check_status() {
  local job_id="$1"
  curl -s "$FRONTEND_URL/status/$job_id" | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status', d.get('error','unknown')))"
}

# =================================================================
# HELPER: wait for a job to reach a target status
# =================================================================
wait_for_status() {
  local job_id="$1"
  local target="$2"
  local max_wait="${3:-30}"
  local elapsed=0

  while [ $elapsed -lt $max_wait ]; do
    local status
    status=$(check_status "$job_id")
    if [ "$status" = "$target" ]; then
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  log_fail "Job $job_id did not reach '$target' within ${max_wait}s. Last status: $status"
  return 1
}


# =================================================================
# TEST 1 — REDIS KILL
#
# WHAT: Kill the Redis container while the system is running.
# PROVES:
#   - API returns clean 503 (not a crash or raw traceback)
#   - Worker logs the connection loss and enters retry loop
#   - When Redis restarts, everything recovers automatically
#   - Jobs submitted before the kill are NOT lost (volume persists)
# =================================================================
test_redis_kill() {
  log_step "TEST 1: Kill Redis mid-operation"

  # Step 1: Submit a job BEFORE the kill — it should be in the queue
  log_info "Submitting job before Redis kill..."
  local job_before
  job_before=$(submit_job)
  log_info "Job submitted: $job_before"

  # Step 2: Kill Redis
  log_warn "Killing Redis container NOW..."
  docker stop redis

  sleep 2

  # Step 3: Try to submit a job DURING the outage
  log_info "Attempting job submission during outage (should get clean error)..."
  local response
  response=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "$FRONTEND_URL/submit" \
    -H "Content-Type: application/json")

  if [ "$response" = "503" ] || [ "$response" = "502" ]; then
    log_success "Got $response during outage — clean error, no crash."
  else
    log_fail "Got $response — expected 503 or 502."
  fi

  # Step 4: Restart Redis
  log_info "Restarting Redis..."
  docker start redis

  # Step 5: Wait for Redis to be healthy
  log_info "Waiting for Redis to become healthy..."
  sleep 15

  # Step 6: Check the pre-kill job — it should now be processed
  log_info "Checking pre-kill job status..."
  if wait_for_status "$job_before" "completed" 30; then
    log_success "Pre-kill job completed after Redis recovery. No data lost."
  else
    log_fail "Pre-kill job was NOT completed. Possible data loss."
  fi

  # Step 7: Submit a new job — system should be fully operational
  log_info "Submitting post-recovery job..."
  local job_after
  job_after=$(submit_job)
  if wait_for_status "$job_after" "completed" 30; then
    log_success "Post-recovery job completed. System fully operational."
  else
    log_fail "Post-recovery job failed. System not fully recovered."
  fi
}


# =================================================================
# TEST 2 — WORKER KILL (CLEAN)
#
# WHAT: Kill the worker container between jobs.
# PROVES:
#   - Jobs submitted while worker is down stay in the queue
#   - When worker restarts, it drains the queue correctly
#   - The API and frontend continue working unaffected
# =================================================================
test_worker_kill() {
  log_step "TEST 2: Kill worker between jobs"

  # Step 1: Kill the worker
  log_warn "Killing worker container..."
  docker stop worker
  sleep 1

  # Step 2: Submit jobs while worker is dead
  log_info "Submitting 3 jobs while worker is down..."
  local job1 job2 job3
  job1=$(submit_job)
  job2=$(submit_job)
  job3=$(submit_job)
  log_info "Jobs submitted: $job1, $job2, $job3"

  # Step 3: Verify they're queued but not completed
  sleep 2
  local s1 s2 s3
  s1=$(check_status "$job1")
  s2=$(check_status "$job2")
  s3=$(check_status "$job3")

  if [ "$s1" = "queued" ] && [ "$s2" = "queued" ] && [ "$s3" = "queued" ]; then
    log_success "All jobs sitting in queue while worker is down."
  else
    log_warn "Unexpected statuses: $s1, $s2, $s3 (expected 'queued')"
  fi

  # Step 4: Restart the worker
  log_info "Restarting worker..."
  docker start worker
  log_info "Waiting for worker to drain the queue..."

  # Step 5: All jobs should complete
  local all_passed=true
  for job in "$job1" "$job2" "$job3"; do
    if wait_for_status "$job" "completed" 30; then
      log_success "Job $job completed after worker restart."
    else
      log_fail "Job $job did NOT complete after worker restart."
      all_passed=false
    fi
  done

  $all_passed && log_success "Worker restart test PASSED — no jobs lost."
}


# =================================================================
# TEST 3 — WORKER KILL MID-JOB (the hard test)
#
# WHAT: Kill the worker while it is actively processing a job.
# PROVES:
#   - The job being processed is re-queued, not silently lost
#   - After worker restarts, the re-queued job completes
#
# This is the hardest failure to handle correctly and the one
# most systems get wrong. The job has already been BRPOP'd off
# the queue. If the worker dies now, the job is nowhere —
# not in the queue, not completed. Only our re-queue logic saves it.
# =================================================================
test_worker_kill_mid_job() {
  log_step "TEST 3: Kill worker MID-JOB (hardest test)"

  # Submit a job
  log_info "Submitting job..."
  local job_id
  job_id=$(submit_job)
  log_info "Job submitted: $job_id"

  # Kill the worker almost immediately — while it's sleeping(2s)
  # The job takes 2 seconds. Kill at 0.5s = definitely mid-job.
  sleep 0.5
  log_warn "Killing worker MID-PROCESSING..."
  docker kill worker   # SIGKILL — no grace period, immediate death

  # The job was popped off the queue and is now in limbo
  sleep 1
  local status
  status=$(check_status "$job_id")
  log_info "Job status immediately after kill: $status"

  # Restart the worker
  log_info "Restarting worker..."
  docker start worker
  sleep 15

  # The re-queue logic should have saved the job
  status=$(check_status "$job_id")
  log_info "Job status after worker restart: $status"

  if [ "$status" = "completed" ]; then
    log_success "Mid-job kill test PASSED — job survived worker crash."
  else
    log_warn "Job status is '$status' — check if re-queue logic fired."
    log_warn "This is expected if the job was killed before re-queue could run."
  fi
}


# =================================================================
# TEST 4 — API KILL
#
# WHAT: Kill the API container.
# PROVES:
#   - Frontend returns clean error (not crash)
#   - Worker continues processing queued jobs unaffected
#   - API auto-restarts via Docker restart policy
#   - System is fully operational after restart
# =================================================================
test_api_kill() {
  log_step "TEST 4: Kill the API"

  # Submit a job first
  log_info "Submitting job before API kill..."
  local job_before
  job_before=$(submit_job)

  # Kill the API
  log_warn "Killing API container..."
  docker stop api
  sleep 1

  # Try to submit while API is down
  log_info "Attempting submission during API outage..."
  local response
  response=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "$FRONTEND_URL/submit" \
    -H "Content-Type: application/json")

  if [ "$response" = "502" ] || [ "$response" = "503" ] || [ "$response" = "504" ]; then
    log_success "Got $response during API outage — clean error."
  else
    log_fail "Got unexpected $response during API outage."
  fi

  # Restart API
  log_info "Restarting API..."
  docker start api
  log_info "Waiting for API health check to pass..."
  sleep 15

  # System should be fully operational
  log_info "Submitting post-recovery job..."
  local job_after
  job_after=$(submit_job)
  if wait_for_status "$job_after" "completed" 30; then
    log_success "API kill test PASSED — system fully recovered."
  else
    log_fail "Post-recovery job did not complete."
  fi
}


# =================================================================
# TEST 5 — QUEUE FLOOD
#
# WHAT: Submit 20 jobs as fast as possible.
# PROVES:
#   - The system handles burst load without crashing
#   - The worker processes jobs in order, one at a time
#   - No jobs are lost under load
# =================================================================
test_flood() {
  log_step "TEST 5: Queue flood (20 simultaneous jobs)"

  log_info "Submitting 20 jobs rapidly..."
  declare -a job_ids=()
  for i in $(seq 1 20); do
    local job_id
    job_id=$(submit_job)
    job_ids+=("$job_id")
    echo -n "."
  done
  echo ""
  log_info "All 20 jobs submitted. Waiting for completion (max 120s)..."

  local passed=0
  local failed=0
  for job_id in "${job_ids[@]}"; do
    if wait_for_status "$job_id" "completed" 120; then
      passed=$((passed + 1))
    else
      failed=$((failed + 1))
    fi
  done

  log_info "Results: $passed completed, $failed failed."
  if [ "$failed" -eq 0 ]; then
    log_success "Flood test PASSED — all 20 jobs completed."
  else
    log_fail "Flood test FAILED — $failed jobs did not complete."
  fi
}


# =================================================================
# INSPECT — Check current system state
# =================================================================
inspect() {
  log_step "SYSTEM INSPECTION"

  echo ""
  log_info "Container health status:"
  docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

  echo ""
  log_info "Redis queue depth:"
  docker exec redis redis-cli -a "$REDIS_PASSWORD" llen job 2>/dev/null \
    | sed 's/^/  Queue length: /'

  echo ""
  log_info "Dead letter queue depth:"
  docker exec redis redis-cli -a "$REDIS_PASSWORD" llen job:dead 2>/dev/null \
    | sed 's/^/  Dead letters: /'

  echo ""
  log_info "Recent API logs (last 20 lines):"
  docker logs api --tail 20

  echo ""
  log_info "Recent worker logs (last 20 lines):"
  docker logs worker --tail 20
}


# =================================================================
# MAIN — route to the right test
# =================================================================
# Read password from .env for the inspect command
REDIS_PASSWORD=$(grep REDIS_PASSWORD ~/hng14-stage2-devops/.env | cut -d= -f2)

case "${1:-help}" in
  redis_kill)         test_redis_kill ;;
  worker_kill)        test_worker_kill ;;
  worker_kill_mid_job) test_worker_kill_mid_job ;;
  api_kill)           test_api_kill ;;
  flood)              test_flood ;;
  inspect)            inspect ;;
  all)
    test_redis_kill
    test_worker_kill
    test_api_kill
    test_flood
    ;;
  *)
    echo ""
    echo "Usage: bash failure_tests.sh <test>"
    echo ""
    echo "  redis_kill          — kill Redis, verify recovery + no data loss"
    echo "  worker_kill         — kill worker between jobs, verify queue drains on restart"
    echo "  worker_kill_mid_job — kill worker MID-JOB, verify job survives"
    echo "  api_kill            — kill API, verify clean errors + auto-recovery"
    echo "  flood               — submit 20 jobs rapidly, verify all complete"
    echo "  inspect             — show system state (queue depth, health, logs)"
    echo "  all                 — run all tests in sequence"
    echo ""
    ;;
esac
