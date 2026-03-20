"""CLI for ghappy - GitHub API proxy client."""

import os
import re
import subprocess
import sys
import time

import click
import httpx


def _get_env(name: str) -> str:
    """Get a required environment variable or exit with an error."""
    value = os.environ.get(name)
    if not value:
        click.echo(f"Error: {name} environment variable is required", err=True)
        sys.exit(1)
    return value


def _detect_repo() -> str:
    """Detect owner/repo from git remote URL.

    Supports:
    - https://github.com/owner/repo.git
    - git@github.com:owner/repo.git
    - http://local_proxy@host:port/git/owner/repo (Claude Code for Web proxy)
    """
    try:
        url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        click.echo("Error: Could not detect git remote URL", err=True)
        sys.exit(1)

    # Claude Code for Web proxy: http://local_proxy@host:port/git/OWNER/REPO
    match = re.search(r"/git/([^/]+/[^/]+?)(?:\.git)?$", url)
    if match:
        return match.group(1)

    # GitHub HTTPS: https://github.com/owner/repo.git
    match = re.search(r"github\.com/([^/]+/[^/]+?)(?:\.git)?$", url)
    if match:
        return match.group(1)

    # GitHub SSH: git@github.com:owner/repo.git
    match = re.search(r"github\.com:([^/]+/[^/]+?)(?:\.git)?$", url)
    if match:
        return match.group(1)

    click.echo(f"Error: Could not parse owner/repo from remote URL: {url}", err=True)
    sys.exit(1)


def _detect_branch() -> str:
    """Detect current git branch."""
    try:
        return subprocess.check_output(
            ["git", "branch", "--show-current"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        click.echo("Error: Could not detect current git branch", err=True)
        sys.exit(1)


def _api_get(path: str, params: dict | None = None) -> dict:
    """Make an authenticated GET request to the ghappy API server."""
    base_url = _get_env("GHAPPY_API_URL").rstrip("/")
    api_key = _get_env("GHAPPY_API_KEY")

    resp = httpx.get(
        f"{base_url}{path}",
        params=params,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60,
    )

    if resp.status_code == 401:
        click.echo("Error: Invalid API key", err=True)
        sys.exit(1)
    elif resp.status_code == 403:
        click.echo("Error: API key not authorized for this repository", err=True)
        sys.exit(1)
    elif resp.status_code >= 400:
        detail = resp.json().get("detail", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        click.echo(f"Error: API returned {resp.status_code}: {detail}", err=True)
        sys.exit(1)

    return resp.json()


def _status_symbol(status: str, conclusion: str | None) -> tuple[str, str]:
    """Return (symbol, display_status) for a check run, matching gh CLI style."""
    if status != "completed":
        return ("*", status)
    if conclusion == "success":
        return ("+", "pass")
    elif conclusion == "failure":
        return ("X", "fail")
    elif conclusion == "cancelled":
        return ("-", "cancelled")
    elif conclusion == "skipped":
        return ("-", "skipped")
    else:
        return ("-", conclusion or "unknown")


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """A CLI proxy for accessing GitHub PR checks and CI logs."""


@cli.command("watch-pr-checks")
@click.option("--interval", default=10, help="Refresh interval in seconds.", show_default=True)
def watch_pr_checks(interval: int):
    """Watch PR check runs until they complete."""
    repo = _detect_repo()
    branch = _detect_branch()
    owner, repo_name = repo.split("/", 1)

    click.echo(f"Watching checks for {repo} @ {branch}...")
    click.echo()

    while True:
        data = _api_get(f"/repos/{owner}/{repo_name}/check-runs", params={"ref": branch})
        runs = data.get("check_runs", [])

        if not runs:
            click.echo("No check runs found.")
            return

        # Calculate column widths
        name_width = max(len(r["name"]) for r in runs)
        name_width = max(name_width, 4)  # min width for "NAME"

        # Build output
        lines = []
        any_pending = False
        any_failed = False

        for run in sorted(runs, key=lambda r: r["name"]):
            symbol, status_text = _status_symbol(run["status"], run["conclusion"])
            run_id = str(run["id"])

            if run["status"] != "completed":
                any_pending = True
            if run.get("conclusion") == "failure":
                any_failed = True

            lines.append((symbol, run["name"], status_text, run_id))

        # Determine column widths
        status_width = max(len(line[2]) for line in lines)
        status_width = max(status_width, 6)  # min width for "STATUS"

        # Print header
        header = f"{'NAME':<{name_width}}\t{'STATUS':<{status_width}}\tRUN ID"
        click.echo(header)

        # Print rows
        for symbol, name, status_text, run_id in lines:
            row = f"{name:<{name_width}}\t{symbol} {status_text:<{status_width}}\t{run_id}"
            click.echo(row)

        if not any_pending:
            click.echo()
            if any_failed:
                click.echo("Some checks were not successful")
                sys.exit(1)
            else:
                click.echo("All checks were successful")
            return

        time.sleep(interval)
        # Clear previous output (move cursor up)
        # +2 for header and blank line between refreshes
        lines_to_clear = len(lines) + 1
        click.echo(f"\033[{lines_to_clear}A\033[J", nl=False)


@cli.command("view-run-failure")
@click.argument("run_id", type=int)
def view_run_failure(run_id: int):
    """View failed job logs for a workflow run."""
    repo = _detect_repo()
    owner, repo_name = repo.split("/", 1)

    data = _api_get(f"/repos/{owner}/{repo_name}/runs/{run_id}/failed-logs")
    logs = data.get("logs", [])

    if not logs:
        click.echo(data.get("message", "No failed jobs found."))
        return

    for job_log in logs:
        job_name = job_log["job_name"]
        failed_steps = set(job_log.get("failed_steps", []))
        log_text = job_log.get("log", "")

        if not log_text:
            for step_name in failed_steps:
                click.echo(f"{job_name}\t{step_name}\t(no logs available)")
            continue

        # Parse log lines and filter to failed steps
        # GitHub Actions log format: YYYY-MM-DDTHH:MM:SS.nnnnnnnZ <message>
        # The log is divided into sections by step, with group markers
        current_step = None
        for line in log_text.splitlines():
            # Detect step group markers: ##[group]Run <step>
            group_match = re.match(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z\s+##\[group\](.*)", line)
            if group_match:
                current_step = group_match.group(1).strip()
                continue

            # Skip non-failed steps if we know the failed ones
            if failed_steps and current_step and current_step not in failed_steps:
                continue

            # Print log lines for failed steps with job/step prefix
            timestamp_match = re.match(r"(\d{4}-\d{2}-\d{2}T[\d:.]+Z)\s+(.*)", line)
            if timestamp_match:
                timestamp = timestamp_match.group(1)
                message = timestamp_match.group(2)
                # Skip group markers in output
                if message.startswith("##["):
                    continue
                step_label = current_step or "UNKNOWN STEP"
                click.echo(f"{job_name}\t{step_label}\t{timestamp} {message}")


if __name__ == "__main__":
    cli()
