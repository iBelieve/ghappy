"""FastAPI server that proxies GitHub API requests."""

import os
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import Config, load_config
from . import github

app = FastAPI(title="ghappy", description="GitHub API proxy")
_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        config_path = os.environ.get("GHAPPY_CONFIG", "config.yaml")
        _config = load_config(config_path)
    return _config


def _authenticate(request: Request, owner: str, repo: str) -> str:
    """Validate the API key and return the GitHub token.

    Raises HTTPException if unauthorized.
    """
    auth = request.headers.get("Authorization", "")
    match = re.match(r"Bearer\s+(.+)", auth)
    if not match:
        raise HTTPException(
            status_code=401, detail="Missing or invalid Authorization header"
        )

    api_key = match.group(1)
    config = get_config()
    full_repo = f"{owner}/{repo}"
    github_token = config.get_github_token(api_key, full_repo)

    if github_token is None:
        raise HTTPException(
            status_code=403, detail="API key not authorized for this repository"
        )

    return github_token


@app.get("/repos/{owner}/{repo}/check-runs")
async def check_runs(owner: str, repo: str, ref: str, request: Request):
    """Get check runs for a git reference."""
    github_token = _authenticate(request, owner, repo)
    try:
        runs = await github.get_check_runs(github_token, owner, repo, ref)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {e}")
    return JSONResponse(content={"check_runs": runs})


@app.get("/repos/{owner}/{repo}/runs/{run_id}/failed-logs")
async def failed_logs(owner: str, repo: str, run_id: int, request: Request):
    """Get failed job logs for a workflow run."""
    github_token = _authenticate(request, owner, repo)

    try:
        jobs = await github.get_run_jobs(github_token, owner, repo, run_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {e}")

    failed_jobs = [j for j in jobs if j["conclusion"] == "failure"]
    if not failed_jobs:
        return JSONResponse(content={"logs": [], "message": "No failed jobs found"})

    logs = []
    for job in failed_jobs:
        failed_steps = [s for s in job["steps"] if s["conclusion"] == "failure"]
        try:
            log_text = await github.get_job_log(github_token, owner, repo, job["id"])
        except Exception:
            log_text = ""

        logs.append(
            {
                "job_name": job["name"],
                "job_id": job["id"],
                "failed_steps": [s["name"] for s in failed_steps],
                "log": log_text,
            }
        )

    return JSONResponse(content={"logs": logs})


def main():
    """Entry point for ghappy-server."""
    import uvicorn

    host = os.environ.get("GHAPPY_HOST", "0.0.0.0")
    port = int(os.environ.get("GHAPPY_PORT", "8000"))
    config_path = os.environ.get("GHAPPY_CONFIG", "config.yaml")

    # Load config eagerly to fail fast on bad config
    global _config
    _config = load_config(config_path)
    print(f"Loaded config from {config_path}")
    print(f"Configured {len(_config.api_keys)} API key(s)")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
