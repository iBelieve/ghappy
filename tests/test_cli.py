"""Tests for ghappy.cli module."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from ghappy.cli import _detect_repo, _display_status, cli


class TestDisplayStatus:
    def test_completed_success(self):
        assert _display_status("completed", "success") == "success"

    def test_completed_failure(self):
        assert _display_status("completed", "failure") == "failure"

    def test_completed_none_conclusion(self):
        assert _display_status("completed", None) == "unknown"

    def test_in_progress(self):
        assert _display_status("in_progress", None) == "in_progress"

    def test_queued(self):
        assert _display_status("queued", None) == "queued"


class TestDetectRepo:
    def test_https_url(self):
        with patch("ghappy.cli.subprocess.check_output") as mock:
            mock.return_value = "https://github.com/owner/repo.git\n"
            assert _detect_repo() == "owner/repo"

    def test_https_url_no_git_suffix(self):
        with patch("ghappy.cli.subprocess.check_output") as mock:
            mock.return_value = "https://github.com/owner/repo\n"
            assert _detect_repo() == "owner/repo"

    def test_ssh_url(self):
        with patch("ghappy.cli.subprocess.check_output") as mock:
            mock.return_value = "git@github.com:owner/repo.git\n"
            assert _detect_repo() == "owner/repo"

    def test_web_proxy_url(self):
        with patch("ghappy.cli.subprocess.check_output") as mock:
            mock.return_value = "http://local_proxy@host:3000/git/owner/repo\n"
            assert _detect_repo() == "owner/repo"

    def test_unparseable_url_exits(self):
        with patch("ghappy.cli.subprocess.check_output") as mock:
            mock.return_value = "https://gitlab.com/owner/repo.git\n"
            with pytest.raises(SystemExit):
                _detect_repo()

    def test_git_not_available_exits(self):
        with patch("ghappy.cli.subprocess.check_output", side_effect=FileNotFoundError):
            with pytest.raises(SystemExit):
                _detect_repo()


class TestWatchPrChecks:
    def test_all_successful(self):
        runner = CliRunner()
        check_runs = {
            "check_runs": [
                {
                    "name": "lint",
                    "status": "completed",
                    "conclusion": "success",
                    "id": 1,
                    "workflow_run_id": 100,
                }
            ]
        }
        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch("ghappy.cli._detect_branch", return_value="main"),
            patch("ghappy.cli._api_get", return_value=check_runs),
        ):
            result = runner.invoke(cli, ["watch-pr-checks"])

        assert result.exit_code == 0
        assert "All checks were successful" in result.output

    def test_failure_exits_nonzero(self):
        runner = CliRunner()
        check_runs = {
            "check_runs": [
                {
                    "name": "test",
                    "status": "completed",
                    "conclusion": "failure",
                    "id": 1,
                    "workflow_run_id": 100,
                }
            ]
        }
        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch("ghappy.cli._detect_branch", return_value="main"),
            patch("ghappy.cli._api_get", return_value=check_runs),
        ):
            result = runner.invoke(cli, ["watch-pr-checks"])

        assert result.exit_code == 1
        assert "Some checks were not successful" in result.output

    def test_no_check_runs_exits(self):
        runner = CliRunner()
        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch("ghappy.cli._detect_branch", return_value="main"),
            patch("ghappy.cli._api_get", return_value={"check_runs": []}),
        ):
            result = runner.invoke(cli, ["watch-pr-checks"])

        assert result.exit_code == 1
        assert "No check runs found" in result.output

    def test_polls_until_complete(self):
        runner = CliRunner()
        pending_runs = {
            "check_runs": [
                {
                    "name": "build",
                    "status": "in_progress",
                    "conclusion": None,
                    "id": 1,
                    "workflow_run_id": 100,
                }
            ]
        }
        complete_runs = {
            "check_runs": [
                {
                    "name": "build",
                    "status": "completed",
                    "conclusion": "success",
                    "id": 1,
                    "workflow_run_id": 100,
                }
            ]
        }
        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch("ghappy.cli._detect_branch", return_value="main"),
            patch("ghappy.cli._api_get", side_effect=[pending_runs, complete_runs]),
            patch("ghappy.cli.time.sleep"),
        ):
            result = runner.invoke(cli, ["watch-pr-checks"])

        assert result.exit_code == 0
        assert "in_progress" in result.output
        assert "All checks were successful" in result.output


class TestViewRunFailure:
    def test_no_failures(self):
        runner = CliRunner()
        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch(
                "ghappy.cli._api_get",
                return_value={"logs": [], "message": "No failed jobs found"},
            ),
        ):
            result = runner.invoke(cli, ["view-run-failure", "123"])

        assert result.exit_code == 0
        assert "No failed jobs found" in result.output

    def test_with_failure_logs(self):
        runner = CliRunner()
        log_text = (
            "2024-01-01T00:00:00.000Z ##[group]Run tests\n"
            "2024-01-01T00:00:01.000Z FAILED test_example.py::test_one\n"
        )
        data = {
            "logs": [
                {
                    "job_name": "test",
                    "failed_steps": ["Run tests"],
                    "log": log_text,
                }
            ]
        }
        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch("ghappy.cli._api_get", return_value=data),
        ):
            result = runner.invoke(cli, ["view-run-failure", "123"])

        assert result.exit_code == 0
        assert "FAILED test_example.py" in result.output
        assert "test\tRun tests\t" in result.output

    def test_no_logs_available(self):
        runner = CliRunner()
        data = {
            "logs": [
                {
                    "job_name": "build",
                    "failed_steps": ["Compile"],
                    "log": "",
                }
            ]
        }
        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch("ghappy.cli._api_get", return_value=data),
        ):
            result = runner.invoke(cli, ["view-run-failure", "456"])

        assert result.exit_code == 0
        assert "no logs available" in result.output
