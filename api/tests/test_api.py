"""
test_api.py — API test suite

Strategy: patch api.main.r with fakeredis BEFORE and DURING
all TestClient requests. The patch must be active for the entire
duration of client use, including the context manager block.
"""
import fakeredis
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from contextlib import asynccontextmanager
import redis as redis_lib
import sys


def _noop_lifespan(app_ref):
    @asynccontextmanager
    async def _inner(app):
        yield
    return _inner


def make_test_app(fr):
    """
    Return the FastAPI app with:
    - module-level `r` replaced with `fr` (fakeredis)
    - lifespan startup check bypassed (no real Redis needed)
    """
    # Ensure clean import
    for mod in list(sys.modules.keys()):
        if "api.main" in mod:
            del sys.modules[mod]
    import api.main as m
    m.r = fr
    m.app.router.lifespan_context = _noop_lifespan(m.app)
    return m.app, m


# =================================================================
# TESTS — HEALTH ENDPOINT
# =================================================================
class TestHealthEndpoint:

    def test_health_returns_200_when_redis_up(self):
        fr = fakeredis.FakeRedis(
            server=fakeredis.FakeServer(), decode_responses=True
        )
        app, m = make_test_app(fr)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["redis"] == "connected"

    def test_health_returns_503_when_redis_down(self):
        fr = fakeredis.FakeRedis(
            server=fakeredis.FakeServer(), decode_responses=True
        )
        app, m = make_test_app(fr)
        broken = MagicMock()
        broken.ping.side_effect = redis_lib.exceptions.ConnectionError("down")
        m.r = broken
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/health")
        assert response.status_code == 503


# =================================================================
# TESTS — JOB CREATION
# =================================================================
class TestCreateJob:

    def test_create_job_returns_job_id(self):
        fr = fakeredis.FakeRedis(
            server=fakeredis.FakeServer(), decode_responses=True
        )
        app, _ = make_test_app(fr)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/jobs")
        assert response.status_code == 200
        assert "job_id" in response.json()
        import uuid
        uuid.UUID(response.json()["job_id"])

    def test_create_job_writes_to_redis_queue(self):
        fr = fakeredis.FakeRedis(
            server=fakeredis.FakeServer(), decode_responses=True
        )
        app, _ = make_test_app(fr)
        with TestClient(app, raise_server_exceptions=False) as client:
            job_id = client.post("/jobs").json()["job_id"]
        assert job_id in fr.lrange("job", 0, -1)

    def test_create_job_sets_queued_status(self):
        fr = fakeredis.FakeRedis(
            server=fakeredis.FakeServer(), decode_responses=True
        )
        app, _ = make_test_app(fr)
        with TestClient(app, raise_server_exceptions=False) as client:
            job_id = client.post("/jobs").json()["job_id"]
        assert fr.hget(f"job:{job_id}", "status") == "queued"

    def test_create_job_returns_503_when_redis_down(self):
        fr = fakeredis.FakeRedis(
            server=fakeredis.FakeServer(), decode_responses=True
        )
        app, m = make_test_app(fr)
        broken = MagicMock()
        broken.ping.return_value = True
        broken.lpush.side_effect = redis_lib.exceptions.ConnectionError("down")
        m.r = broken
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/jobs")
        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"].lower()

    def test_multiple_jobs_get_unique_ids(self):
        fr = fakeredis.FakeRedis(
            server=fakeredis.FakeServer(), decode_responses=True
        )
        app, _ = make_test_app(fr)
        with TestClient(app, raise_server_exceptions=False) as client:
            ids = {client.post("/jobs").json()["job_id"] for _ in range(10)}
        assert len(ids) == 10


# =================================================================
# TESTS — JOB STATUS
# =================================================================
class TestGetJob:

    def test_get_queued_job_returns_status(self):
        fr = fakeredis.FakeRedis(
            server=fakeredis.FakeServer(), decode_responses=True
        )
        app, _ = make_test_app(fr)
        with TestClient(app, raise_server_exceptions=False) as client:
            job_id = client.post("/jobs").json()["job_id"]
            response = client.get(f"/jobs/{job_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "queued"

    def test_get_completed_job_returns_completed(self):
        fr = fakeredis.FakeRedis(
            server=fakeredis.FakeServer(), decode_responses=True
        )
        app, _ = make_test_app(fr)
        with TestClient(app, raise_server_exceptions=False) as client:
            job_id = client.post("/jobs").json()["job_id"]
            fr.hset(f"job:{job_id}", "status", "completed")
            response = client.get(f"/jobs/{job_id}")
        assert response.json()["status"] == "completed"

    def test_get_nonexistent_job_returns_404(self):
        fr = fakeredis.FakeRedis(
            server=fakeredis.FakeServer(), decode_responses=True
        )
        app, _ = make_test_app(fr)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/jobs/no-such-job-exists-here")
        assert response.status_code == 404

    def test_get_job_returns_503_when_redis_down(self):
        fr = fakeredis.FakeRedis(
            server=fakeredis.FakeServer(), decode_responses=True
        )
        app, m = make_test_app(fr)
        broken = MagicMock()
        broken.ping.return_value = True
        broken.hget.side_effect = redis_lib.exceptions.ConnectionError("down")
        m.r = broken
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/jobs/any-id")
        assert response.status_code == 503


# =================================================================
# TESTS — FULL LIFECYCLE
# =================================================================
class TestFullLifecycle:

    def test_full_job_lifecycle(self):
        fr = fakeredis.FakeRedis(
            server=fakeredis.FakeServer(), decode_responses=True
        )
        app, _ = make_test_app(fr)
        with TestClient(app, raise_server_exceptions=False) as client:
            job_id = client.post("/jobs").json()["job_id"]
            assert client.get(f"/jobs/{job_id}").json()["status"] == "queued"
            fr.hset(f"job:{job_id}", "status", "completed")
            assert client.get(f"/jobs/{job_id}").json()["status"] == "completed"

    def test_multiple_concurrent_jobs_isolated(self):
        fr = fakeredis.FakeRedis(
            server=fakeredis.FakeServer(), decode_responses=True
        )
        app, _ = make_test_app(fr)
        with TestClient(app, raise_server_exceptions=False) as client:
            ids = [client.post("/jobs").json()["job_id"] for _ in range(5)]
            for jid in ids:
                assert client.get(f"/jobs/{jid}").json()["status"] == "queued"
            for jid in reversed(ids):
                fr.hset(f"job:{jid}", "status", "completed")
            for jid in ids:
                assert client.get(f"/jobs/{jid}").json()["status"] == "completed"
