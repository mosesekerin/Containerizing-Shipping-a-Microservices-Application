const express = require('express');
const axios = require('axios');
const path = require('path');
const app = express();

// ─────────────────────────────────────────────
// FIX 3: Read the API URL from an environment
// variable instead of hardcoding 'localhost'.
//
// In Docker, each service lives in its own
// container. 'localhost' inside the frontend
// container points back to the frontend itself,
// not the API container.
//
// The correct address is the service name
// defined in docker-compose.yml (e.g. 'api').
// Docker's internal DNS resolves that name to
// the right container automatically.
//
// Fallback to localhost for plain local dev
// (running without Docker).
// ─────────────────────────────────────────────
const API_URL = process.env.API_URL || "http://localhost:8000";

app.use(express.json());
app.use(express.static(path.join(__dirname, 'views')));

app.post('/submit', async (req, res) => {
  try {
    const response = await axios.post(`${API_URL}/jobs`);
    res.json(response.data);
  } catch (err) {
    // FIX 4 (frontend side): Log the real error server-side
    // but return a safe, clean message to the client.
    console.error(`[ERROR] POST /jobs failed: ${err.message}`);
    res.status(502).json({ error: "Could not reach job service. Please try again." });
  }
});

app.get('/status/:id', async (req, res) => {
  try {
    const response = await axios.get(`${API_URL}/jobs/${req.params.id}`);
    res.json(response.data);
  } catch (err) {
    console.error(`[ERROR] GET /jobs/${req.params.id} failed: ${err.message}`);
    res.status(502).json({ error: "Could not retrieve job status." });
  }
});

// ─────────────────────────────────────────────
// Health check endpoint for Docker/load balancer
// ─────────────────────────────────────────────
app.get('/health', (req, res) => {
  res.json({ status: 'ok' });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`[INFO] Frontend running on port ${PORT}`);
  console.log(`[INFO] Proxying API requests to: ${API_URL}`);
});
