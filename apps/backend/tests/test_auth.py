"""
Tests for API key authentication.
These run WITHOUT any LLM calls — agent and security pipeline are mocked.
"""

from unittest.mock import MagicMock, patch

import pytest
from backend.config import get_settings
from fastapi.testclient import TestClient
from pydantic import SecretStr

VALID_KEY = "test-secret-key-abc123"


def _mock_settings():
    s = MagicMock()
    s.portal_pal_api_key = SecretStr(VALID_KEY)
    s.rate_limit = "100/minute"
    s.cache_ttl_seconds = 300
    s.app_env = "test"
    s.langsmith_tracing = False
    s.primary_model = "gpt-test"
    s.is_production = False
    return s


@pytest.fixture()
def client():
    """
    TestClient with all external dependencies patched out.
    Agent, security pipeline, cache, and metrics are mocked so no LLM or
    startup side-effects run.
    """
    get_settings.cache_clear()
    mock_settings = _mock_settings()

    with (
        patch("backend.main.get_settings", return_value=mock_settings),
        patch("backend.config.get_settings", return_value=mock_settings),
        patch("backend.main.ProductionAgent") as MockAgent,
        patch("backend.main.SecurityPipeline") as MockSecurity,
        patch("backend.main.ResponseCache") as MockCache,
        patch("backend.main.MetricsCollector") as MockMetrics,
    ):
        MockSecurity.return_value.check_input.return_value = (True, "safe message", [])
        MockSecurity.return_value.check_output.return_value = ("response text", [])
        MockAgent.return_value.invoke.return_value = {
            "response": "response text",
            "model_used": "gpt-test",
        }
        MockCache.return_value.get.return_value = None
        MockCache.return_value.set.return_value = None
        MockCache.return_value.stats = {
            "hits": 0,
            "misses": 0,
            "hit_rate": 0.0,
            "cached_entries": 0,
        }
        MockMetrics.return_value.record_request.return_value = None
        MockMetrics.return_value.summary = {
            "total_requests": 0,
            "total_errors": 0,
            "error_rate": "0.00%",
            "avg_latency_ms": 0.0,
            "cache_hit_rate": "0.00%",
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }

        from backend.main import app

        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    get_settings.cache_clear()


class TestChatAuth:
    def test_valid_key_returns_200(self, client):
        resp = client.post(
            "/chat",
            json={"message": "hello", "thread_id": "t1"},
            headers={"X-API-Key": VALID_KEY},
        )
        assert resp.status_code == 200

    def test_missing_key_returns_401(self, client):
        resp = client.post("/chat", json={"message": "hello", "thread_id": "t1"})
        assert resp.status_code == 401

    def test_wrong_key_returns_401(self, client):
        resp = client.post(
            "/chat",
            json={"message": "hello", "thread_id": "t1"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_empty_key_returns_401(self, client):
        resp = client.post(
            "/chat",
            json={"message": "hello", "thread_id": "t1"},
            headers={"X-API-Key": ""},
        )
        assert resp.status_code == 401


class TestMetricsAuth:
    def test_valid_key_returns_200(self, client):
        resp = client.get("/metrics", headers={"X-API-Key": VALID_KEY})
        assert resp.status_code == 200

    def test_missing_key_returns_401(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 401

    def test_wrong_key_returns_401(self, client):
        resp = client.get("/metrics", headers={"X-API-Key": "bad"})
        assert resp.status_code == 401


class TestCacheStatsAuth:
    def test_valid_key_returns_200(self, client):
        resp = client.get("/cache/stats", headers={"X-API-Key": VALID_KEY})
        assert resp.status_code == 200

    def test_missing_key_returns_401(self, client):
        resp = client.get("/cache/stats")
        assert resp.status_code == 401

    def test_wrong_key_returns_401(self, client):
        resp = client.get("/cache/stats", headers={"X-API-Key": "bad"})
        assert resp.status_code == 401


class TestHealthNoAuth:
    def test_health_open_no_key_needed(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
