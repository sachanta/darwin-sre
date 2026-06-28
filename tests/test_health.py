"""Health endpoint tests — local + integration smoke against deployed URL."""
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from api.app import create_app
    return TestClient(create_app())


class TestHealthLocal:
    def test_health_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_status_ok(self, client):
        assert client.get("/health").json()["status"] == "ok"

    def test_health_service_name(self, client):
        assert client.get("/health").json()["service"] == "darwin-sre"

    def test_health_json_content_type(self, client):
        resp = client.get("/health")
        assert "application/json" in resp.headers["content-type"]


@pytest.mark.integration
class TestHealthDeployed:
    """Hits the live DO App Platform URL. Run after deploy with DARWIN_DEPLOY_URL set."""

    def test_deployed_health(self):
        import httpx
        url = os.environ.get("DARWIN_DEPLOY_URL", "").rstrip("/")
        if not url:
            pytest.skip("DARWIN_DEPLOY_URL not set")
        resp = httpx.get(f"{url}/health", timeout=10)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_deployed_runs_endpoint(self):
        import httpx
        url = os.environ.get("DARWIN_DEPLOY_URL", "").rstrip("/")
        if not url:
            pytest.skip("DARWIN_DEPLOY_URL not set")
        resp = httpx.get(f"{url}/runs", timeout=10)
        assert resp.status_code == 200
        assert "runs" in resp.json()

    def test_deployed_frontend_serves(self):
        import httpx
        url = os.environ.get("DARWIN_DEPLOY_URL", "").rstrip("/")
        if not url:
            pytest.skip("DARWIN_DEPLOY_URL not set")
        resp = httpx.get(url, timeout=10)
        assert resp.status_code == 200
        assert b"Darwin SRE" in resp.content
