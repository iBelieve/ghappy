"""Tests for ghappy.github module."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ghappy.github import (
    MAX_LOG_BYTES,
    _get_action_check_runs,
    _get_non_action_check_runs,
    _headers,
    get_check_runs,
    get_job_log,
    get_pr_number_for_branch,
    get_run_jobs,
    get_unresolved_copilot_comments,
)


class TestHeaders:
    def test_headers_format(self):
        h = _headers("ghp_test123")
        assert h["Authorization"] == "Bearer ghp_test123"
        assert h["Accept"] == "application/vnd.github+json"
        assert h["X-GitHub-Api-Version"] == "2022-11-28"


class TestGetActionCheckRuns:
    @pytest.mark.asyncio
    async def test_basic_check_runs(self):
        """Workflow runs API returns runs, jobs are fetched for each."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "workflow_runs": [
                {"id": 100, "head_sha": "abc123"},
            ]
        }

        mock_jobs = [
            {
                "name": "lint",
                "status": "completed",
                "conclusion": "success",
                "id": 1,
                "html_url": "https://github.com/owner/repo/actions/runs/100/job/1",
                "started_at": "2024-01-01T00:00:00Z",
                "completed_at": "2024-01-01T00:01:00Z",
                "steps": [],
            }
        ]

        with (
            patch("ghappy.github.httpx.AsyncClient") as mock_client_cls,
            patch(
                "ghappy.github.get_run_jobs",
                new_callable=AsyncMock,
                return_value=mock_jobs,
            ),
        ):
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await _get_action_check_runs("token", "owner", "repo", "main")

        assert len(result) == 1
        assert result[0]["name"] == "lint"
        assert result[0]["status"] == "completed"
        assert result[0]["conclusion"] == "success"
        assert result[0]["workflow_run_id"] == 100

    @pytest.mark.asyncio
    async def test_uses_head_sha_for_full_sha_ref(self):
        """When ref is a 40-char hex SHA, use head_sha query parameter."""
        sha = "a" * 40
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"workflow_runs": []}

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await _get_action_check_runs("token", "owner", "repo", sha)

        # Verify head_sha was used in the API call
        call_kwargs = mock_client.get.call_args
        assert call_kwargs.kwargs["params"]["head_sha"] == sha
        assert result == []

    @pytest.mark.asyncio
    async def test_uses_branch_for_non_sha_ref(self):
        """When ref is a branch name, use branch query parameter."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"workflow_runs": []}

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await _get_action_check_runs("token", "owner", "repo", "main")

        call_kwargs = mock_client.get.call_args
        assert call_kwargs.kwargs["params"]["branch"] == "main"
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_to_latest_commit(self):
        """Only jobs from the most recent head_sha are returned."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "workflow_runs": [
                {"id": 200, "head_sha": "newer"},
                {"id": 100, "head_sha": "older"},
            ]
        }

        mock_jobs = [
            {
                "name": "test",
                "status": "completed",
                "conclusion": "success",
                "id": 1,
                "html_url": "",
                "started_at": None,
                "completed_at": None,
                "steps": [],
            }
        ]

        with (
            patch("ghappy.github.httpx.AsyncClient") as mock_client_cls,
            patch(
                "ghappy.github.get_run_jobs",
                new_callable=AsyncMock,
                return_value=mock_jobs,
            ) as mock_get_jobs,
        ):
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await _get_action_check_runs("token", "owner", "repo", "main")

        # Only the newer run should have its jobs fetched
        mock_get_jobs.assert_called_once_with("token", "owner", "repo", 200)
        assert len(result) == 1
        assert result[0]["workflow_run_id"] == 200

    @pytest.mark.asyncio
    async def test_deduplicates_jobs_by_name(self):
        """When multiple workflow runs have the same job name, keep the newest."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "workflow_runs": [
                {"id": 300, "head_sha": "abc"},
                {"id": 200, "head_sha": "abc"},
            ]
        }

        def fake_get_run_jobs(_token, _owner, _repo, run_id):
            if run_id == 300:
                return [
                    {
                        "name": "Deploy to Preview Environment",
                        "status": "completed",
                        "conclusion": "skipped",
                        "id": 10,
                        "html_url": "",
                        "started_at": None,
                        "completed_at": None,
                        "steps": [],
                    }
                ]
            return [
                {
                    "name": "Deploy to Preview Environment",
                    "status": "completed",
                    "conclusion": "success",
                    "id": 5,
                    "html_url": "",
                    "started_at": None,
                    "completed_at": None,
                    "steps": [],
                }
            ]

        with (
            patch("ghappy.github.httpx.AsyncClient") as mock_client_cls,
            patch(
                "ghappy.github.get_run_jobs",
                new_callable=AsyncMock,
                side_effect=fake_get_run_jobs,
            ),
        ):
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await _get_action_check_runs("token", "owner", "repo", "main")

        # Should have only one entry, from the newer run (id=300)
        assert len(result) == 1
        assert result[0]["workflow_run_id"] == 300
        assert result[0]["conclusion"] == "skipped"

    @pytest.mark.asyncio
    async def test_empty_workflow_runs(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"workflow_runs": []}

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await _get_action_check_runs("token", "owner", "repo", "main")

        assert result == []


class TestGetNonActionCheckRuns:
    @pytest.mark.asyncio
    async def test_returns_non_action_checks(self):
        """Check runs from non-Actions apps are returned."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "check_runs": [
                {
                    "name": "Test Results",
                    "status": "completed",
                    "conclusion": "success",
                    "id": 500,
                    "app": {"slug": "test-reporter"},
                    "details_url": "https://example.com/results",
                    "started_at": "2024-01-01T00:00:00Z",
                    "completed_at": "2024-01-01T00:01:00Z",
                },
            ]
        }

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await _get_non_action_check_runs(
                "token", "owner", "repo", "main"
            )

        assert len(result) == 1
        assert result[0]["name"] == "Test Results"
        assert result[0]["workflow_run_id"] is None
        assert result[0]["id"] == 500

    @pytest.mark.asyncio
    async def test_filters_out_github_actions_checks(self):
        """Check runs from github-actions app are excluded."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "check_runs": [
                {
                    "name": "lint",
                    "status": "completed",
                    "conclusion": "success",
                    "id": 1,
                    "app": {"slug": "github-actions"},
                    "details_url": "",
                    "started_at": None,
                    "completed_at": None,
                },
                {
                    "name": "Test Results",
                    "status": "completed",
                    "conclusion": "success",
                    "id": 500,
                    "app": {"slug": "test-reporter"},
                    "details_url": "",
                    "started_at": None,
                    "completed_at": None,
                },
            ]
        }

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await _get_non_action_check_runs(
                "token", "owner", "repo", "main"
            )

        assert len(result) == 1
        assert result[0]["name"] == "Test Results"

    @pytest.mark.asyncio
    async def test_graceful_on_403(self):
        """Returns empty list when Checks API returns 403 (no permission)."""
        mock_request = httpx.Request("GET", "https://api.github.com/test")
        mock_resp = httpx.Response(
            403, json={"message": "Forbidden"}, request=mock_request
        )
        exc = httpx.HTTPStatusError(
            "Forbidden", request=mock_request, response=mock_resp
        )

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=exc)

            result = await _get_non_action_check_runs(
                "token", "owner", "repo", "main"
            )

        assert result == []

    @pytest.mark.asyncio
    async def test_handles_missing_app_field(self):
        """Check runs without an app field are included."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "check_runs": [
                {
                    "name": "External Check",
                    "status": "completed",
                    "conclusion": "success",
                    "id": 600,
                    "details_url": "",
                    "started_at": None,
                    "completed_at": None,
                },
            ]
        }

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await _get_non_action_check_runs(
                "token", "owner", "repo", "main"
            )

        assert len(result) == 1
        assert result[0]["name"] == "External Check"


class TestGetCheckRuns:
    @pytest.mark.asyncio
    async def test_merges_action_and_non_action_checks(self):
        """get_check_runs combines both sources, action checks take priority."""
        action_checks = [
            {
                "name": "lint",
                "status": "completed",
                "conclusion": "success",
                "id": 1,
                "workflow_run_id": 100,
                "details_url": "",
                "started_at": None,
                "completed_at": None,
            }
        ]
        non_action_checks = [
            {
                "name": "Test Results",
                "status": "completed",
                "conclusion": "success",
                "id": 500,
                "workflow_run_id": None,
                "details_url": "",
                "started_at": None,
                "completed_at": None,
            }
        ]

        with (
            patch(
                "ghappy.github._get_action_check_runs",
                new_callable=AsyncMock,
                return_value=action_checks,
            ),
            patch(
                "ghappy.github._get_non_action_check_runs",
                new_callable=AsyncMock,
                return_value=non_action_checks,
            ),
        ):
            result = await get_check_runs("token", "owner", "repo", "main")

        names = {r["name"] for r in result}
        assert names == {"lint", "Test Results"}
        lint = next(r for r in result if r["name"] == "lint")
        assert lint["workflow_run_id"] == 100
        test_results = next(r for r in result if r["name"] == "Test Results")
        assert test_results["workflow_run_id"] is None

    @pytest.mark.asyncio
    async def test_action_checks_take_priority_on_name_collision(self):
        """When both APIs return a check with the same name, action wins."""
        action_checks = [
            {
                "name": "build",
                "status": "completed",
                "conclusion": "success",
                "id": 1,
                "workflow_run_id": 100,
                "details_url": "",
                "started_at": None,
                "completed_at": None,
            }
        ]
        non_action_checks = [
            {
                "name": "build",
                "status": "completed",
                "conclusion": "failure",
                "id": 999,
                "workflow_run_id": None,
                "details_url": "",
                "started_at": None,
                "completed_at": None,
            }
        ]

        with (
            patch(
                "ghappy.github._get_action_check_runs",
                new_callable=AsyncMock,
                return_value=action_checks,
            ),
            patch(
                "ghappy.github._get_non_action_check_runs",
                new_callable=AsyncMock,
                return_value=non_action_checks,
            ),
        ):
            result = await get_check_runs("token", "owner", "repo", "main")

        assert len(result) == 1
        assert result[0]["workflow_run_id"] == 100
        assert result[0]["conclusion"] == "success"

    @pytest.mark.asyncio
    async def test_non_action_checks_only(self):
        """When there are no action checks, non-action checks are returned."""
        non_action_checks = [
            {
                "name": "Test Results",
                "status": "completed",
                "conclusion": "success",
                "id": 500,
                "workflow_run_id": None,
                "details_url": "",
                "started_at": None,
                "completed_at": None,
            }
        ]

        with (
            patch(
                "ghappy.github._get_action_check_runs",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "ghappy.github._get_non_action_check_runs",
                new_callable=AsyncMock,
                return_value=non_action_checks,
            ),
        ):
            result = await get_check_runs("token", "owner", "repo", "main")

        assert len(result) == 1
        assert result[0]["name"] == "Test Results"

    @pytest.mark.asyncio
    async def test_empty_from_both_sources(self):
        with (
            patch(
                "ghappy.github._get_action_check_runs",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "ghappy.github._get_non_action_check_runs",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
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
                    "html_url": "https://github.com/owner/repo/actions/runs/123/job/1",
                    "started_at": "2024-01-01T00:00:00Z",
                    "completed_at": "2024-01-01T00:01:00Z",
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
        assert (
            result[0]["html_url"]
            == "https://github.com/owner/repo/actions/runs/123/job/1"
        )
        assert result[0]["started_at"] == "2024-01-01T00:00:00Z"
        assert result[0]["completed_at"] == "2024-01-01T00:01:00Z"
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


class TestGetPrNumberForBranch:
    @pytest.mark.asyncio
    async def test_finds_open_pr(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [{"number": 42}]

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await get_pr_number_for_branch("token", "owner", "repo", "feat")

        assert result == 42
        call_kwargs = mock_client.get.call_args
        assert call_kwargs.kwargs["params"]["head"] == "owner:feat"

    @pytest.mark.asyncio
    async def test_no_open_pr(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = []

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await get_pr_number_for_branch("token", "owner", "repo", "feat")

        assert result is None


def _graphql_response(threads, has_next_page=False, end_cursor=None):
    """Build a mock GraphQL response for reviewThreads."""
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {
                            "hasNextPage": has_next_page,
                            "endCursor": end_cursor,
                        },
                        "nodes": threads,
                    }
                }
            }
        }
    }


def _thread(
    *,
    resolved=False,
    author="copilot",
    review_id=1,
    review_created="2024-01-01T00:00:00Z",
    body="fix this",
    path="src/main.py",
    line=10,
):
    """Build a review thread node for testing."""
    return {
        "isResolved": resolved,
        "comments": {
            "nodes": [
                {
                    "author": {"login": author},
                    "body": body,
                    "path": path,
                    "line": line,
                    "startLine": None,
                    "url": f"https://github.com/owner/repo/pull/1#comment-{review_id}",
                    "pullRequestReview": {
                        "databaseId": review_id,
                        "createdAt": review_created,
                        "author": {"login": author},
                    },
                }
            ]
        },
    }


class TestGetUnresolvedCopilotComments:
    @pytest.mark.asyncio
    async def test_returns_unresolved_copilot_comments(self):
        threads = [
            _thread(resolved=False, review_id=1, body="fix this"),
            _thread(resolved=True, review_id=1, body="already fixed"),
        ]
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _graphql_response(threads)

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)

            result = await get_unresolved_copilot_comments("token", "owner", "repo", 1)

        assert len(result) == 1
        assert result[0]["body"] == "fix this"
        assert result[0]["path"] == "src/main.py"

    @pytest.mark.asyncio
    async def test_filters_to_latest_copilot_review(self):
        threads = [
            _thread(
                resolved=False,
                review_id=1,
                review_created="2024-01-01T00:00:00Z",
                body="old",
            ),
            _thread(
                resolved=False,
                review_id=2,
                review_created="2024-01-02T00:00:00Z",
                body="new",
            ),
        ]
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _graphql_response(threads)

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)

            result = await get_unresolved_copilot_comments("token", "owner", "repo", 1)

        assert len(result) == 1
        assert result[0]["body"] == "new"

    @pytest.mark.asyncio
    async def test_no_copilot_reviews(self):
        threads = [
            _thread(resolved=False, author="human-reviewer", review_id=1),
        ]
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _graphql_response(threads)

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)

            result = await get_unresolved_copilot_comments("token", "owner", "repo", 1)

        assert result == []

    @pytest.mark.asyncio
    async def test_empty_threads(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _graphql_response([])

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)

            result = await get_unresolved_copilot_comments("token", "owner", "repo", 1)

        assert result == []

    @pytest.mark.asyncio
    async def test_graphql_error_raises(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"errors": [{"message": "bad query"}]}

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)

            with pytest.raises(RuntimeError, match="GraphQL error"):
                await get_unresolved_copilot_comments("token", "owner", "repo", 1)


async def _async_iter(items):
    for item in items:
        yield item
