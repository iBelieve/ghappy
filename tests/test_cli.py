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

    def test_error_annotations_preserved(self):
        runner = CliRunner()
        log_text = (
            "2024-01-01T00:00:00.000Z ##[group]Run tests\n"
            "2024-01-01T00:00:01.000Z ##[error]Process completed with exit code 1.\n"
            "2024-01-01T00:00:02.000Z ##[endgroup]\n"
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
        assert "##[error]Process completed with exit code 1." in result.output

    def test_step_name_mismatch_still_shows_logs(self):
        """When API step names don't match ##[group] markers, logs should still appear."""
        runner = CliRunner()
        log_text = (
            "2024-01-01T00:00:00.000Z ##[group]Run git push dokku HEAD:refs/heads/master --force\n"
            "2024-01-01T00:00:01.000Z remote: Deploying app...\n"
            "2024-01-01T00:00:02.000Z ##[error]Process completed with exit code 1.\n"
        )
        data = {
            "logs": [
                {
                    "job_name": "deploy",
                    # API step name differs from the ##[group] marker text
                    "failed_steps": ["Deploy preview"],
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
        assert "remote: Deploying app..." in result.output
        assert "##[error]Process completed with exit code 1." in result.output

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


class TestCopilotReview:
    def test_with_comments(self):
        runner = CliRunner()
        data = {
            "comments": [
                {
                    "path": "src/main.py",
                    "line": 10,
                    "start_line": None,
                    "body": "Fix this bug",
                    "url": "https://github.com/owner/repo/pull/42#comment-1",
                }
            ],
            "pr_number": 42,
        }
        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch("ghappy.cli._detect_branch", return_value="feat"),
            patch("ghappy.cli._api_get", return_value=data),
        ):
            result = runner.invoke(cli, ["copilot-review"])

        assert result.exit_code == 0
        assert "PR #42" in result.output
        assert "src/main.py:10" in result.output
        assert "Fix this bug" in result.output

    def test_with_multiline_comment(self):
        runner = CliRunner()
        data = {
            "comments": [
                {
                    "path": "src/main.py",
                    "line": 15,
                    "start_line": 10,
                    "body": "Refactor this block",
                    "url": "https://github.com/owner/repo/pull/42#comment-2",
                }
            ],
            "pr_number": 42,
        }
        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch("ghappy.cli._detect_branch", return_value="feat"),
            patch("ghappy.cli._api_get", return_value=data),
        ):
            result = runner.invoke(cli, ["copilot-review"])

        assert result.exit_code == 0
        assert "src/main.py:10-15" in result.output

    def test_no_open_pr(self):
        runner = CliRunner()
        data = {
            "comments": [],
            "message": "No open PR found for branch feat",
        }
        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch("ghappy.cli._detect_branch", return_value="feat"),
            patch("ghappy.cli._api_get", return_value=data),
        ):
            result = runner.invoke(cli, ["copilot-review"])

        assert result.exit_code == 0
        assert "No open PR found" in result.output

    def test_no_unresolved_comments(self):
        runner = CliRunner()
        data = {"comments": [], "pr_number": 42}
        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch("ghappy.cli._detect_branch", return_value="feat"),
            patch("ghappy.cli._api_get", return_value=data),
        ):
            result = runner.invoke(cli, ["copilot-review"])

        assert result.exit_code == 0
        assert "No unresolved Copilot review comments on PR #42" in result.output


class TestListPrArtifacts:
    def test_with_artifacts(self):
        runner = CliRunner()
        data = {
            "artifacts": [
                {"id": 1, "name": "build-output", "size_in_bytes": 1048576},
                {"id": 2, "name": "test-results", "size_in_bytes": 512},
            ]
        }
        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch("ghappy.cli._detect_branch", return_value="main"),
            patch("ghappy.cli._api_get", return_value=data),
        ):
            result = runner.invoke(cli, ["list-pr-artifacts"])

        assert result.exit_code == 0
        assert "build-output" in result.output
        assert "test-results" in result.output
        assert "1.0 MB" in result.output

    def test_no_artifacts(self):
        runner = CliRunner()
        data = {"artifacts": []}
        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch("ghappy.cli._detect_branch", return_value="main"),
            patch("ghappy.cli._api_get", return_value=data),
        ):
            result = runner.invoke(cli, ["list-pr-artifacts"])

        assert result.exit_code == 0
        assert "No artifacts found" in result.output


class TestDownloadPrArtifact:
    def test_downloads_by_id(self, tmp_path):
        runner = CliRunner()
        zip_content = b"PK\x03\x04fake-zip"
        out_file = str(tmp_path / "build-output.zip")

        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch("ghappy.cli._api_get_bytes", return_value=zip_content),
        ):
            result = runner.invoke(cli, ["download-pr-artifact", "42", "-o", out_file])

        assert result.exit_code == 0
        assert "Saved to" in result.output
        with open(out_file, "rb") as f:
            assert f.read() == zip_content

    def test_default_output_filename(self, tmp_path, monkeypatch):
        runner = CliRunner()
        zip_content = b"PK\x03\x04data"
        monkeypatch.chdir(tmp_path)

        with (
            patch("ghappy.cli._detect_repo", return_value="owner/repo"),
            patch("ghappy.cli._api_get_bytes", return_value=zip_content),
        ):
            result = runner.invoke(cli, ["download-pr-artifact", "42"])

        assert result.exit_code == 0
        assert "Saved to artifact-42.zip" in result.output
