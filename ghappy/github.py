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
        resp = await client.get(url, headers=_headers(github_token), params=params)
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


async def get_pr_number_for_branch(
    github_token: str, owner: str, repo: str, branch: str
) -> int | None:
    """Find the open pull request number for a branch.

    Returns the PR number, or None if no open PR exists.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls"
    params = {"head": f"{owner}:{branch}", "state": "open", "per_page": 1}

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(url, headers=_headers(github_token), params=params)
        resp.raise_for_status()
        prs = resp.json()

    if not prs:
        return None
    return prs[0]["number"]


_REVIEW_THREADS_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          comments(first: 1) {
            nodes {
              author { login }
              body
              path
              line
              startLine
              url
              pullRequestReview {
                databaseId
                createdAt
                author { login }
              }
            }
          }
        }
      }
    }
  }
}
"""


async def get_unresolved_copilot_comments(
    github_token: str, owner: str, repo: str, pr_number: int
) -> list[dict]:
    """Get unresolved review comments from the latest Copilot review.

    Uses the GraphQL API to access review thread resolution status.
    Returns a list of comment dicts with: path, line, start_line, body, url.
    """
    graphql_url = f"{GITHUB_API}/graphql"
    variables: dict = {"owner": owner, "repo": repo, "pr": pr_number, "cursor": None}

    all_threads: list[dict] = []
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for _ in range(MAX_PAGES):
            resp = await client.post(
                graphql_url,
                headers=_headers(github_token),
                json={"query": _REVIEW_THREADS_QUERY, "variables": variables},
            )
            resp.raise_for_status()
            data = resp.json()

            if "errors" in data:
                raise RuntimeError(f"GraphQL error: {data['errors']}")

            threads_data = data["data"]["repository"]["pullRequest"]["reviewThreads"]
            all_threads.extend(threads_data["nodes"])
            if not threads_data["pageInfo"]["hasNextPage"]:
                break
            variables["cursor"] = threads_data["pageInfo"]["endCursor"]

    # Identify the latest Copilot review by database ID and created time.
    copilot_reviews: dict[int, str] = {}  # review database_id -> createdAt
    for thread in all_threads:
        comments = thread["comments"]["nodes"]
        if not comments:
            continue
        comment = comments[0]
        review = comment.get("pullRequestReview")
        if not review:
            continue
        author = review.get("author")
        if author and author["login"].startswith("copilot"):
            rid = review["databaseId"]
            copilot_reviews[rid] = review["createdAt"]

    if not copilot_reviews:
        return []

    latest_review_id = max(copilot_reviews, key=lambda rid: copilot_reviews[rid])

    # Collect unresolved comments from the latest Copilot review.
    result = []
    for thread in all_threads:
        if thread["isResolved"]:
            continue
        comments = thread["comments"]["nodes"]
        if not comments:
            continue
        comment = comments[0]
        review = comment.get("pullRequestReview")
        if not review or review["databaseId"] != latest_review_id:
            continue
        result.append(
            {
                "path": comment.get("path"),
                "line": comment.get("line"),
                "start_line": comment.get("startLine"),
                "body": comment.get("body", ""),
                "url": comment.get("url", ""),
            }
        )

    return result


async def get_artifacts_for_branch(
    github_token: str, owner: str, repo: str, branch: str
) -> list[dict]:
    """Get artifacts from the latest workflow runs on a branch.

    Finds the most recent commit's workflow runs and returns all artifacts
    across those runs. Returns a list of artifact dicts with: id, name,
    size_in_bytes, created_at, expires_at, workflow_run_id.
    """
    # Fetch workflow runs for the branch.
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/runs"
    params: dict[str, str | int] = {"branch": branch, "per_page": 100}

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(url, headers=_headers(github_token), params=params)
        resp.raise_for_status()
        data = resp.json()
        workflow_runs = data.get("workflow_runs", [])

    if not workflow_runs:
        return []

    # Keep only runs for the latest commit.
    latest_sha = workflow_runs[0]["head_sha"]
    current_runs = [r for r in workflow_runs if r["head_sha"] == latest_sha]

    # Fetch artifacts for each workflow run.
    all_artifacts: list[dict] = []
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for wf_run in current_runs:
            run_id = wf_run["id"]
            art_url = (
                f"{GITHUB_API}/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts"
            )
            resp = await client.get(
                art_url, headers=_headers(github_token), params={"per_page": 100}
            )
            resp.raise_for_status()
            artifacts = resp.json().get("artifacts", [])
            for art in artifacts:
                all_artifacts.append(
                    {
                        "id": art["id"],
                        "name": art["name"],
                        "size_in_bytes": art["size_in_bytes"],
                        "created_at": art.get("created_at"),
                        "expires_at": art.get("expires_at"),
                        "workflow_run_id": run_id,
                    }
                )

    return all_artifacts


async def download_artifact(
    github_token: str, owner: str, repo: str, artifact_id: int
) -> bytes:
    """Download an artifact as a zip archive.

    Returns the raw zip bytes.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip"

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=REQUEST_TIMEOUT
    ) as client:
        async with client.stream("GET", url, headers=_headers(github_token)) as resp:
            resp.raise_for_status()
            chunks = []
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)

    return b"".join(chunks)


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
