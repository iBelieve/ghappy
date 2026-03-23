"""FastAPI server that proxies GitHub API requests."""

import logging
import os
import re

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import github
from .config import Config, load_config

logger = logging.getLogger("ghappy")

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
    match = re.match(r"Bearer\s+([\w\-]{1,256})", auth)
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


def _github_http_error(exc: httpx.HTTPStatusError) -> HTTPException:
    """Convert a GitHub HTTP error into an appropriate HTTPException."""
    status = exc.response.status_code
    if 400 <= status < 500:
        try:
            body = exc.response.json()
            message = body.get("message", exc.response.reason_phrase)
        except Exception:
            message = exc.response.reason_phrase
        detail: dict | str = f"GitHub: {message}"
        permissions = exc.response.headers.get("X-Accepted-GitHub-Permissions")
        if permissions:
            detail = {
                "detail": f"GitHub: {message}",
                "accepted_permissions": permissions,
            }
        return HTTPException(status_code=status, detail=detail)
    return HTTPException(status_code=502, detail="GitHub API error")


def _get_client_ip(request: Request) -> str:
    """Get the real client IP, respecting proxy headers."""
    forwarded = (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    )
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


def _log_access(request: Request, owner: str, repo: str, endpoint: str) -> None:
    """Log authenticated access to an endpoint."""
    client = _get_client_ip(request)
    logger.info("%s %s/%s %s", endpoint, owner, repo, client)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return JSONResponse(content={"status": "ok"})


@app.get("/repos/{owner}/{repo}/check-runs")
async def check_runs(owner: str, repo: str, ref: str, request: Request):
    """Get check runs for a git reference."""
    github_token = _authenticate(request, owner, repo)
    _log_access(request, owner, repo, f"check-runs ref={ref}")
    try:
        runs = await github.get_check_runs(github_token, owner, repo, ref)
    except httpx.HTTPStatusError as exc:
        logger.exception(
            "GitHub API error fetching check runs for %s/%s ref=%s", owner, repo, ref
        )
        raise _github_http_error(exc)
    except Exception:
        logger.exception(
            "GitHub API error fetching check runs for %s/%s ref=%s", owner, repo, ref
        )
        raise HTTPException(status_code=502, detail="GitHub API error")
    return JSONResponse(content={"check_runs": runs})


@app.get("/repos/{owner}/{repo}/runs/{run_id}/failed-logs")
async def failed_logs(owner: str, repo: str, run_id: int, request: Request):
    """Get failed job logs for a workflow run."""
    github_token = _authenticate(request, owner, repo)
    _log_access(request, owner, repo, f"failed-logs run={run_id}")

    try:
        jobs = await github.get_run_jobs(github_token, owner, repo, run_id)
    except httpx.HTTPStatusError as exc:
        logger.exception(
            "GitHub API error fetching jobs for %s/%s run=%d", owner, repo, run_id
        )
        raise _github_http_error(exc)
    except Exception:
        logger.exception(
            "GitHub API error fetching jobs for %s/%s run=%d", owner, repo, run_id
        )
        raise HTTPException(status_code=502, detail="GitHub API error")

    failed_jobs = [j for j in jobs if j["conclusion"] == "failure"]
    if not failed_jobs:
        return JSONResponse(content={"logs": [], "message": "No failed jobs found"})

    logs = []
    for job in failed_jobs:
        failed_steps = [s for s in job["steps"] if s["conclusion"] == "failure"]
        try:
            log_text = await github.get_job_log(github_token, owner, repo, job["id"])
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "GitHub API error fetching log for %s/%s job=%d",
                owner,
                repo,
                job["id"],
            )
            raise _github_http_error(exc)
        except Exception:
            logger.exception(
                "GitHub API error fetching log for %s/%s job=%d",
                owner,
                repo,
                job["id"],
            )
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


@app.get("/repos/{owner}/{repo}/pr-comments")
async def pr_comments(owner: str, repo: str, branch: str, request: Request):
    """Get unresolved Copilot review comments for a PR."""
    github_token = _authenticate(request, owner, repo)
    _log_access(request, owner, repo, f"pr-comments branch={branch}")

    try:
        pr_number = await github.get_pr_number_for_branch(
            github_token, owner, repo, branch
        )
    except httpx.HTTPStatusError as exc:
        logger.exception(
            "GitHub API error finding PR for %s/%s branch=%s", owner, repo, branch
        )
        raise _github_http_error(exc)
    except Exception:
        logger.exception(
            "GitHub API error finding PR for %s/%s branch=%s", owner, repo, branch
        )
        raise HTTPException(status_code=502, detail="GitHub API error")

    if pr_number is None:
        return JSONResponse(
            content={"comments": [], "message": f"No open PR found for branch {branch}"}
        )

    try:
        comments = await github.get_unresolved_copilot_comments(
            github_token, owner, repo, pr_number
        )
    except httpx.HTTPStatusError as exc:
        logger.exception(
            "GitHub API error fetching PR comments for %s/%s PR #%d",
            owner,
            repo,
            pr_number,
        )
        raise _github_http_error(exc)
    except Exception:
        logger.exception(
            "GitHub API error fetching PR comments for %s/%s PR #%d",
            owner,
            repo,
            pr_number,
        )
        raise HTTPException(status_code=502, detail="GitHub API error")

    return JSONResponse(
        content={"comments": comments, "pr_number": pr_number}
    )


def main():
    """Entry point for ghappy-server."""
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    host = os.environ.get("GHAPPY_HOST", "0.0.0.0")
    port = int(os.environ.get("GHAPPY_PORT", "8000"))
    config_path = os.environ.get("GHAPPY_CONFIG", "config.yaml")

    # Load config eagerly to fail fast on bad config
    global _config
    _config = load_config(config_path)
    logger.info("Loaded config from %s", config_path)
    logger.info("Configured %d API key(s)", len(_config.api_keys))

    uvicorn.run(app, host=host, port=port, proxy_headers=True)


if __name__ == "__main__":
    main()
