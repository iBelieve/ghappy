# ghappy

A simple CLI and API proxy for accessing GitHub PR checks and CI logs from within Claude Code for Web.

## Server

The server proxies GitHub API requests, authenticating clients via API keys mapped to repos.

### Configuration

Create a `config.yaml`:

```yaml
api_keys:
  - key: "your-api-key"
    github_token: "ghp_xxx"
    repos:
      - "owner/repo1"
      - "owner/repo2"
  - key: "another-key"
    github_token: "ghp_yyy"
    repos:
      - "owner/repo3"
```

Each API key is scoped to specific repos and uses its own GitHub token.

### Running

```bash
uvx ghappy-server
```

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `GHAPPY_CONFIG` | `config.yaml` | Path to config file |
| `GHAPPY_HOST` | `0.0.0.0` | Host to bind to |
| `GHAPPY_PORT` | `8000` | Port to listen on |

## CLI

### Setup

Set these environment variables (e.g. in your Claude Code for Web session):

```bash
export GHAPPY_API_URL="https://your-ghappy-server.example.com"
export GHAPPY_API_KEY="your-api-key"
```

The CLI auto-detects the repo and branch from your git remote.

### Commands

**Watch PR checks:**

```bash
uvx ghappy watch-pr-checks
```

Polls for check run status and prints a line whenever a check's status changes:

```
Watching checks for owner/repo @ my-branch...
* Build and Test	in_progress	(run 12345678)
* Lint	queued	(run 12345679)
+ Lint	pass	(run 12345679)
+ Build and Test	pass	(run 12345678)

All checks were successful
```

**View failed run logs:**

```bash
uvx ghappy view-run-failure 12345678
```

Shows failed job logs in `gh run view --log-failed` style:

```
build	Run tests	2024-01-15T10:30:00Z Error: test_foo failed
build	Run tests	2024-01-15T10:30:01Z   AssertionError: expected 1 got 2
```
