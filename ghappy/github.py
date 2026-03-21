"""GitHub API client for server-side use."""

import re

import httpx

GITHUB_API = "https://api.github.com"
REQUEST_TIMEOUT = 30
MAX_PAGES = 10
MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 MB


def _headers(github_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def get_check_runs(
    github_token: str, owner: str, repo: str, ref: str
) -> list[dict]:
    """Get check runs for a git reference (branch, tag, or SHA).

    Uses the GitHub Actions workflow runs and jobs APIs instead of the
    checks API, since fine-grained PATs don't support the checks API.

    Returns a list of check run dicts with: name, status, conclusion, id,
    workflow_run_id, details_url, started_at, completed_at.
    """
    # Use head_sha for full SHAs, branch name otherwise.
    if re.fullmatch(r"[0-9a-fA-F]{40}", ref):
        params: dict[str, str | int] = {"head_sha": ref, "per_page": 100}
    else:
        params = {"branch": ref, "per_page": 100}

    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/runs"

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(
            url, headers=_headers(github_token), params=params
        )
        resp.raise_for_status()
        data = resp.json()
        workflow_runs = data.get("workflow_runs", [])

    if not workflow_runs:
        return []

    # The API returns runs newest first. Keep only runs for the latest commit.
    latest_sha = workflow_runs[0]["head_sha"]
    current_runs = [r for r in workflow_runs if r["head_sha"] == latest_sha]

    # Fetch jobs for each workflow run and map to check-run format.
    # Deduplicate by job name, keeping the entry from the newest run
    # (highest run_id). Multiple workflow runs for the same commit can
    # produce duplicate job names (e.g. re-runs or parallel triggers).
    seen: dict[str, dict] = {}
    for wf_run in current_runs:
        run_id = wf_run["id"]
        jobs = await get_run_jobs(github_token, owner, repo, run_id)
        for job in jobs:
            name = job["name"]
            if name not in seen or run_id > seen[name]["workflow_run_id"]:
                seen[name] = {
                    "name": name,
                    "status": job["status"],
                    "conclusion": job["conclusion"],
                    "id": job["id"],
                    "workflow_run_id": run_id,
                    "details_url": job.get("html_url", ""),
                    "started_at": job.get("started_at"),
                    "completed_at": job.get("completed_at"),
                }
    return list(seen.values())


async def get_run_jobs(
    github_token: str, owner: str, repo: str, run_id: int
) -> list[dict]:
    """Get jobs for a workflow run.

    Returns a list of job dicts with: id, name, status, conclusion, steps.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
    all_jobs = []
    page = 1

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        while page <= MAX_PAGES:
            resp = await client.get(
                url,
                headers=_headers(github_token),
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            data = resp.json()
            jobs = data.get("jobs", [])
            all_jobs.extend(jobs)
            if len(jobs) < 100:
                break
            page += 1

    return [
        {
            "id": job["id"],
            "name": job["name"],
            "status": job["status"],
            "conclusion": job["conclusion"],
            "html_url": job.get("html_url", ""),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
            "steps": [
                {
                    "name": step["name"],
                    "status": step["status"],
                    "conclusion": step["conclusion"],
                    "number": step["number"],
                }
                for step in job.get("steps", [])
            ],
        }
        for job in all_jobs
    ]


async def get_job_log(github_token: str, owner: str, repo: str, job_id: int) -> str:
    """Download logs for a specific job.

    Returns the plain text log content, truncated to MAX_LOG_BYTES.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=REQUEST_TIMEOUT
    ) as client:
        async with client.stream("GET", url, headers=_headers(github_token)) as resp:
            resp.raise_for_status()
            chunks = []
            total = 0
            async for chunk in resp.aiter_bytes():
                remaining = MAX_LOG_BYTES - total
                if remaining <= 0:
                    break
                chunks.append(chunk[:remaining])
                total += len(chunk[:remaining])

    return b"".join(chunks).decode("utf-8", errors="replace")
