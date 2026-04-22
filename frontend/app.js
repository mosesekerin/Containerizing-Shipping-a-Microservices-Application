const express = require('express');
const axios   = require('axios');
const path    = require('path');
const app     = express();

// =================================================================
// STRUCTURED LOGGING
// Same JSON format as API and worker — unified across all services.
// =================================================================
function log(level, message, extra = {}) {
  const entry = {
    timestamp: new Date().toISOString(),
    level,
    service: 'frontend',
    message,
    ...extra,
  };
  console.log(JSON.stringify(entry));
}

// =================================================================
// AXIOS INSTANCE WITH TIMEOUT
//
// The default axios client has NO timeout. If the API hangs
// (e.g. it's waiting for Redis and Redis is stuck), axios
// waits forever. Your frontend request hangs. Your user's browser
// eventually times out on its own — after 120 seconds or more.
//
// Setting a timeout of 8 seconds means: if the API hasn't
// responded in 8 seconds, axios gives up and the frontend
// returns a clean error to the user immediately.
//
// 8 seconds is generous for an internal network call that
// should take milliseconds. It covers momentary Redis blips
// but doesn't hang the user indefinitely.
// =================================================================
const API_URL = process.env.API_URL || 'http://localhost:8000';

const apiClient = axios.create({
  baseURL: API_URL,
  timeout: 8000,  // 8 seconds — after this, axios throws ECONNABORTED
  headers: {
    'Content-Type': 'application/json',
  },
});


// =================================================================
// RETRY HELPER WITH EXPONENTIAL BACKOFF
//
// Not all failures are permanent. A 503 from the API means
// "Redis is temporarily unavailable." Trying again in 1 second
// might succeed because Redis recovered in the meantime.
//
// A 404 is permanent — the job genuinely doesn't exist.
// Retrying a 404 wastes time. We only retry on 5xx errors
// and network failures (timeouts, connection refused).
//
// Exponential backoff: wait 1s, then 2s, then 4s between attempts.
// This gives the dependency time to recover without flooding it.
// =================================================================
async function withRetry(fn, maxAttempts = 3, label = 'request') {
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (err) {
      const status   = err.response?.status;
      const isRetry  = !status || status >= 500; // retry on network errors and 5xx
      const isLast   = attempt === maxAttempts;

      if (!isRetry || isLast) throw err;

      const waitMs = (2 ** (attempt - 1)) * 1000; // 1s, 2s, 4s
      log('WARN', `${label} failed (attempt ${attempt}/${maxAttempts}). Retrying in ${waitMs}ms.`, {
        status,
        error: err.message,
      });
      await new Promise(resolve => setTimeout(resolve, waitMs));
    }
  }
}


// =================================================================
// MIDDLEWARE
// =================================================================
app.use(express.json());
app.use(express.static(path.join(__dirname, 'views')));


// =================================================================
// ROUTES
// =================================================================
app.post('/submit', async (req, res) => {
  try {
    const response = await withRetry(
      () => apiClient.post('/jobs'),
      3,
      'POST /jobs'
    );
    log('INFO', 'Job submitted successfully.', { job_id: response.data.job_id });
    res.json(response.data);
  } catch (err) {
    const status  = err.response?.status || 502;
    const detail  = err.response?.data?.detail || err.message;
    log('ERROR', 'Failed to submit job after retries.', { status, detail });

    // Return a specific message depending on what went wrong
    if (err.code === 'ECONNABORTED') {
      return res.status(504).json({ error: 'Job service timed out. Please try again.' });
    }
    if (status === 503) {
      return res.status(503).json({ error: 'Job queue is temporarily unavailable. Please try again shortly.' });
    }
    res.status(502).json({ error: 'Could not reach job service. Please try again.' });
  }
});


app.get('/status/:id', async (req, res) => {
  const jobId = req.params.id;
  try {
    // Status checks are low-stakes — retry up to 3 times
    const response = await withRetry(
      () => apiClient.get(`/jobs/${jobId}`),
      3,
      `GET /jobs/${jobId}`
    );
    res.json(response.data);
  } catch (err) {
    const status = err.response?.status || 502;
    const detail = err.response?.data?.detail || err.message;
    log('ERROR', 'Failed to fetch job status.', { job_id: jobId, status, detail });

    if (status === 404) {
      return res.status(404).json({ error: 'Job not found.' });
    }
    if (err.code === 'ECONNABORTED') {
      return res.status(504).json({ error: 'Status check timed out.' });
    }
    res.status(502).json({ error: 'Could not retrieve job status.' });
  }
});


// =================================================================
// HEALTH CHECK
// =================================================================
app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'frontend' });
});


// =================================================================
// STARTUP
// =================================================================
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  log('INFO', `Frontend running on port ${PORT}.`);
  log('INFO', `Proxying API requests to: ${API_URL}`);
});