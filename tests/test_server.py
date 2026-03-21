"""Tests for ghappy.server module."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from ghappy.config import ApiKeyEntry, Config
from ghappy.server import _get_client_ip, _github_http_error, app

TEST_CONFIG = Config(
    api_keys=[
        ApiKeyEntry(
            key="test-api-key",
            github_token="ghp_test",
            repos=["owner/repo"],
        )
    ]
)


@pytest.fixture
def client():
    """Create a test client with a known config."""
    with patch("ghappy.server.get_config", return_value=TEST_CONFIG):
        with TestClient(app) as c:
            yield c


AUTH_HEADER = {"Authorization": "Bearer test-api-key"}


class TestHealthEndpoint:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestAuthentication:
    def test_missing_auth_header(self, client):
        resp = client.get("/repos/owner/repo/check-runs?ref=main")
        assert resp.status_code == 401

    def test_invalid_auth_format(self, client):
        resp = client.get(
            "/repos/owner/repo/check-runs?ref=main",
            headers={"Authorization": "Basic abc"},
        )
        assert resp.status_code == 401

    def test_invalid_api_key(self, client):
        resp = client.get(
            "/repos/owner/repo/check-runs?ref=main",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 403

    def test_unauthorized_repo(self, client):
        resp = client.get(
            "/repos/other/repo/check-runs?ref=main",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 403


class TestCheckRunsEndpoint:
    def test_success(self, client):
        mock_runs = [{"name": "lint", "status": "completed", "conclusion": "success"}]
        with patch(
            "ghappy.server.github.get_check_runs",
            new_callable=AsyncMock,
            return_value=mock_runs,
        ):
            resp = client.get(
                "/repos/owner/repo/check-runs?ref=main",
                headers=AUTH_HEADER,
            )
        assert resp.status_code == 200
        assert resp.json() == {"check_runs": mock_runs}

    def test_missing_ref_param(self, client):
        resp = client.get(
            "/repos/owner/repo/check-runs",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422  # FastAPI validation error

    def test_github_http_error(self, client):
        mock_request = httpx.Request("GET", "https://api.github.com/test")
        mock_response = httpx.Response(
            404,
            json={"message": "Not Found"},
            request=mock_request,
        )
        exc = httpx.HTTPStatusError(
            "Not Found", request=mock_request, response=mock_response
        )
        with patch(
            "ghappy.server.github.get_check_runs",
            new_callable=AsyncMock,
            side_effect=exc,
        ):
            resp = client.get(
                "/repos/owner/repo/check-runs?ref=main",
                headers=AUTH_HEADER,
            )
        assert resp.status_code == 404

    def test_generic_error(self, client):
        with patch(
            "ghappy.server.github.get_check_runs",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection failed"),
        ):
            resp = client.get(
                "/repos/owner/repo/check-runs?ref=main",
                headers=AUTH_HEADER,
            )
        assert resp.status_code == 502


class TestFailedLogsEndpoint:
    def test_no_failed_jobs(self, client):
        jobs = [
            {
                "id": 1,
                "name": "build",
                "status": "completed",
                "conclusion": "success",
                "steps": [],
            }
        ]
        with patch(
            "ghappy.server.github.get_run_jobs",
            new_callable=AsyncMock,
            return_value=jobs,
        ):
            resp = client.get(
                "/repos/owner/repo/runs/100/failed-logs",
                headers=AUTH_HEADER,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["logs"] == []
        assert "No failed jobs" in data["message"]

    def test_with_failed_jobs(self, client):
        jobs = [
            {
                "id": 1,
                "name": "test",
                "status": "completed",
                "conclusion": "failure",
                "steps": [
                    {
                        "name": "Run tests",
                        "status": "completed",
                        "conclusion": "failure",
                    }
                ],
            }
        ]
        with (
            patch(
                "ghappy.server.github.get_run_jobs",
                new_callable=AsyncMock,
                return_value=jobs,
            ),
            patch(
                "ghappy.server.github.get_job_log",
                new_callable=AsyncMock,
                return_value="log output here",
            ),
        ):
            resp = client.get(
                "/repos/owner/repo/runs/100/failed-logs",
                headers=AUTH_HEADER,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["logs"]) == 1
        assert data["logs"][0]["job_name"] == "test"
        assert data["logs"][0]["failed_steps"] == ["Run tests"]
        assert data["logs"][0]["log"] == "log output here"

    def test_with_cancelled_jobs(self, client):
        jobs = [
            {
                "id": 1,
                "name": "build",
                "status": "completed",
                "conclusion": "cancelled",
                "steps": [
                    {
                        "name": "Set up Docker",
                        "status": "completed",
                        "conclusion": "cancelled",
                    }
                ],
            }
        ]
        with (
            patch(
                "ghappy.server.github.get_run_jobs",
                new_callable=AsyncMock,
                return_value=jobs,
            ),
            patch(
                "ghappy.server.github.get_job_log",
                new_callable=AsyncMock,
                return_value="log output here",
            ),
        ):
            resp = client.get(
                "/repos/owner/repo/runs/100/failed-logs",
                headers=AUTH_HEADER,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["logs"]) == 1
        assert data["logs"][0]["job_name"] == "build"
        assert data["logs"][0]["failed_steps"] == ["Set up Docker"]
        assert data["logs"][0]["log"] == "log output here"

    def test_log_fetch_http_error_returns_empty_log(self, client):
        jobs = [
            {
                "id": 1,
                "name": "test",
                "status": "completed",
                "conclusion": "failure",
                "steps": [],
            }
        ]
        mock_request = httpx.Request("GET", "https://api.github.com/test")
        mock_response = httpx.Response(410, request=mock_request)
        exc = httpx.HTTPStatusError(
            "Gone", request=mock_request, response=mock_response
        )
        with (
            patch(
                "ghappy.server.github.get_run_jobs",
                new_callable=AsyncMock,
                return_value=jobs,
            ),
            patch(
                "ghappy.server.github.get_job_log",
                new_callable=AsyncMock,
                side_effect=exc,
            ),
        ):
            resp = client.get(
                "/repos/owner/repo/runs/100/failed-logs",
                headers=AUTH_HEADER,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["logs"][0]["log"] == ""

    def test_log_fetch_generic_error_returns_empty_log(self, client):
        jobs = [
            {
                "id": 1,
                "name": "test",
                "status": "completed",
                "conclusion": "failure",
                "steps": [],
            }
        ]
        with (
            patch(
                "ghappy.server.github.get_run_jobs",
                new_callable=AsyncMock,
                return_value=jobs,
            ),
            patch(
                "ghappy.server.github.get_job_log",
                new_callable=AsyncMock,
                side_effect=RuntimeError("oops"),
            ),
        ):
            resp = client.get(
                "/repos/owner/repo/runs/100/failed-logs",
                headers=AUTH_HEADER,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["logs"][0]["log"] == ""


class TestGithubHttpError:
    def test_4xx_error_with_message(self):
        mock_request = httpx.Request("GET", "https://api.github.com/test")
        mock_response = httpx.Response(
            403,
            json={"message": "Resource not accessible"},
            request=mock_request,
        )
        exc = httpx.HTTPStatusError(
            "Forbidden", request=mock_request, response=mock_response
        )
        result = _github_http_error(exc)
        assert result.status_code == 403
        assert "Resource not accessible" in str(result.detail)

    def test_4xx_with_permissions_header(self):
        mock_request = httpx.Request("GET", "https://api.github.com/test")
        mock_response = httpx.Response(
            403,
            json={"message": "Forbidden"},
            headers={"X-Accepted-GitHub-Permissions": "actions:read"},
            request=mock_request,
        )
        exc = httpx.HTTPStatusError(
            "Forbidden", request=mock_request, response=mock_response
        )
        result = _github_http_error(exc)
        assert result.status_code == 403
        assert isinstance(result.detail, dict)
        assert result.detail.get("accepted_permissions") == "actions:read"

    def test_5xx_error(self):
        mock_request = httpx.Request("GET", "https://api.github.com/test")
        mock_response = httpx.Response(500, request=mock_request)
        exc = httpx.HTTPStatusError(
            "Server Error", request=mock_request, response=mock_response
        )
        result = _github_http_error(exc)
        assert result.status_code == 502


class TestGetClientIp:
    def test_cf_connecting_ip(self):
        request = _make_request(headers={"CF-Connecting-IP": "1.2.3.4"})
        assert _get_client_ip(request) == "1.2.3.4"

    def test_x_forwarded_for(self):
        request = _make_request(headers={"X-Forwarded-For": "5.6.7.8, 10.0.0.1"})
        assert _get_client_ip(request) == "5.6.7.8"

    def test_fallback_to_client_host(self):
        request = _make_request(client_host="192.168.1.1")
        assert _get_client_ip(request) == "192.168.1.1"

    def test_no_client(self):
        request = _make_request(client_host=None)
        assert _get_client_ip(request) == "unknown"


def _make_request(headers=None, client_host: str | None = "127.0.0.1"):
    """Create a minimal mock Request."""
    from unittest.mock import MagicMock

    request = MagicMock()
    request.headers = headers or {}
    if client_host is not None:
        request.client = MagicMock()
        request.client.host = client_host
    else:
        request.client = None
    return request
