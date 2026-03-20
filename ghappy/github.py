"""GitHub API client for server-side use."""

import httpx

GITHUB_API = "https://api.github.com"


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

    Returns a list of check run dicts with: name, status, conclusion, id,
    started_at, completed_at, details_url.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits/{ref}/check-runs"
    all_runs = []
    page = 1

    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                url,
                headers=_headers(github_token),
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            data = resp.json()
            runs = data.get("check_runs", [])
            all_runs.extend(runs)
            if len(runs) < 100:
                break
            page += 1

    return [
        {
            "name": run["name"],
            "status": run["status"],
            "conclusion": run["conclusion"],
            "id": run["id"],
            "details_url": run.get("details_url"),
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
        }
        for run in all_runs
    ]


async def get_run_jobs(
    github_token: str, owner: str, repo: str, run_id: int
) -> list[dict]:
    """Get jobs for a workflow run.

    Returns a list of job dicts with: id, name, status, conclusion, steps.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
    all_jobs = []
    page = 1

    async with httpx.AsyncClient() as client:
        while True:
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


async def get_job_log(
    github_token: str, owner: str, repo: str, job_id: int
) -> str:
    """Download logs for a specific job.

    Returns the plain text log content.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"

    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(url, headers=_headers(github_token))
        resp.raise_for_status()
        return resp.text
