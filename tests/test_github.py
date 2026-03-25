"""Tests for ghappy.github module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ghappy.github import (
    MAX_LOG_BYTES,
    _headers,
    download_artifact,
    get_artifacts_for_branch,
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


class TestGetCheckRuns:
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

            result = await get_check_runs("token", "owner", "repo", "main")

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

            result = await get_check_runs("token", "owner", "repo", sha)

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

            result = await get_check_runs("token", "owner", "repo", "main")

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

            result = await get_check_runs("token", "owner", "repo", "main")

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

            result = await get_check_runs("token", "owner", "repo", "main")

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


class TestGetArtifactsForBranch:
    @pytest.mark.asyncio
    async def test_returns_artifacts(self):
        """Artifacts are collected from workflow runs for the latest commit."""
        runs_response = MagicMock()
        runs_response.raise_for_status = MagicMock()
        runs_response.json.return_value = {
            "workflow_runs": [{"id": 100, "head_sha": "abc123"}]
        }

        artifacts_response = MagicMock()
        artifacts_response.raise_for_status = MagicMock()
        artifacts_response.json.return_value = {
            "artifacts": [
                {
                    "id": 1,
                    "name": "build-output",
                    "size_in_bytes": 1024,
                    "created_at": "2024-01-01T00:00:00Z",
                    "expires_at": "2024-02-01T00:00:00Z",
                }
            ]
        }

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=[runs_response, artifacts_response])

            result = await get_artifacts_for_branch("token", "owner", "repo", "main")

        assert len(result) == 1
        assert result[0]["name"] == "build-output"
        assert result[0]["id"] == 1
        assert result[0]["size_in_bytes"] == 1024
        assert result[0]["workflow_run_id"] == 100

    @pytest.mark.asyncio
    async def test_no_workflow_runs(self):
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

            result = await get_artifacts_for_branch("token", "owner", "repo", "main")

        assert result == []

    @pytest.mark.asyncio
    async def test_filters_to_latest_commit(self):
        """Only artifacts from runs with the latest head_sha are returned."""
        runs_response = MagicMock()
        runs_response.raise_for_status = MagicMock()
        runs_response.json.return_value = {
            "workflow_runs": [
                {"id": 200, "head_sha": "newer"},
                {"id": 100, "head_sha": "older"},
            ]
        }

        artifacts_response = MagicMock()
        artifacts_response.raise_for_status = MagicMock()
        artifacts_response.json.return_value = {
            "artifacts": [
                {
                    "id": 5,
                    "name": "test-results",
                    "size_in_bytes": 512,
                    "created_at": "2024-01-01T00:00:00Z",
                    "expires_at": "2024-02-01T00:00:00Z",
                }
            ]
        }

        with patch("ghappy.github.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=[runs_response, artifacts_response])

            result = await get_artifacts_for_branch("token", "owner", "repo", "main")

        assert len(result) == 1
        # Only run 200 (newer) should have artifacts fetched — verify via the
        # artifact URL requested.
        art_call = mock_client.get.call_args_list[1]
        assert "/runs/200/artifacts" in art_call.args[0]


class TestDownloadArtifact:
    @pytest.mark.asyncio
    async def test_downloads_zip(self):
        zip_bytes = b"PK\x03\x04fake-zip-content"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_bytes = lambda: _async_iter([zip_bytes])

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

            result = await download_artifact("token", "owner", "repo", 42)

        assert result == zip_bytes


async def _async_iter(items):
    for item in items:
        yield item
