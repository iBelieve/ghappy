"""Tests for ghappy.github module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ghappy.github import (
    MAX_LOG_BYTES,
    _headers,
    get_check_runs,
    get_job_log,
    get_run_jobs,
)


class TestHeaders:
    def test_headers_format(self):
        h = _headers("ghp_test123")
        assert h["Authorization"] == "Bearer ghp_test123"
        assert h["Accept"] == "application/vnd.github+json"
        assert h["X-GitHub-Api-Version"] == "2022-11-28"


class TestGetCheckRuns:
    @pytest.mark.asyncio
    async def test_basic_check_runs(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "check_runs": [
                {
                    "name": "lint",
                    "status": "completed",
                    "conclusion": "success",
                    "id": 1,
                    "details_url": "https://github.com/owner/repo/actions/runs/100/job/1",
                    "started_at": "2024-01-01T00:00:00Z",
                    "completed_at": "2024-01-01T00:01:00Z",
                }
            ]
        }

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await get_check_runs("token", "owner", "repo", "main")

        assert len(result) == 1
        assert result[0]["name"] == "lint"
        assert result[0]["status"] == "completed"
        assert result[0]["conclusion"] == "success"
        assert result[0]["workflow_run_id"] == 100

    @pytest.mark.asyncio
    async def test_no_workflow_run_id_in_details_url(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "check_runs": [
                {
                    "name": "external-check",
                    "status": "completed",
                    "conclusion": "success",
                    "id": 42,
                    "details_url": "https://example.com/check/42",
                    "started_at": None,
                    "completed_at": None,
                }
            ]
        }

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await get_check_runs("token", "owner", "repo", "main")

        assert result[0]["workflow_run_id"] is None

    @pytest.mark.asyncio
    async def test_pagination(self):
        page1_response = MagicMock()
        page1_response.raise_for_status = MagicMock()
        page1_response.json.return_value = {
            "check_runs": [
                {
                    "name": f"check-{i}",
                    "status": "completed",
                    "conclusion": "success",
                    "id": i,
                    "details_url": "",
                }
                for i in range(100)
            ]
        }

        page2_response = MagicMock()
        page2_response.raise_for_status = MagicMock()
        page2_response.json.return_value = {
            "check_runs": [
                {
                    "name": "check-100",
                    "status": "completed",
                    "conclusion": "success",
                    "id": 100,
                    "details_url": "",
                }
            ]
        }

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=[page1_response, page2_response])

            result = await get_check_runs("token", "owner", "repo", "main")

        assert len(result) == 101

    @pytest.mark.asyncio
    async def test_empty_check_runs(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"check_runs": []}

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await get_check_runs("token", "owner", "repo", "main")

        assert result == []


class TestGetRunJobs:
    @pytest.mark.asyncio
    async def test_basic_jobs(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "jobs": [
                {
                    "id": 1,
                    "name": "build",
                    "status": "completed",
                    "conclusion": "failure",
                    "steps": [
                        {
                            "name": "Run tests",
                            "status": "completed",
                            "conclusion": "failure",
                            "number": 3,
                        }
                    ],
                }
            ]
        }

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await get_run_jobs("token", "owner", "repo", 123)

        assert len(result) == 1
        assert result[0]["name"] == "build"
        assert result[0]["conclusion"] == "failure"
        assert len(result[0]["steps"]) == 1
        assert result[0]["steps"][0]["name"] == "Run tests"

    @pytest.mark.asyncio
    async def test_job_with_no_steps(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "jobs": [
                {
                    "id": 1,
                    "name": "build",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await get_run_jobs("token", "owner", "repo", 123)

        assert result[0]["steps"] == []


class TestGetJobLog:
    @pytest.mark.asyncio
    async def test_basic_log(self):
        log_content = b"2024-01-01T00:00:00Z Step output line 1\n"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_bytes = lambda: _async_iter([log_content])

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.stream = MagicMock(return_value=mock_stream_ctx)

            result = await get_job_log("token", "owner", "repo", 1)

        assert "Step output line 1" in result

    @pytest.mark.asyncio
    async def test_log_truncation(self):
        # Create content larger than MAX_LOG_BYTES
        chunk = b"x" * (MAX_LOG_BYTES + 1000)

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_bytes = lambda: _async_iter([chunk])

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.stream = MagicMock(return_value=mock_stream_ctx)

            result = await get_job_log("token", "owner", "repo", 1)

        assert len(result) == MAX_LOG_BYTES


async def _async_iter(items):
    for item in items:
        yield item
