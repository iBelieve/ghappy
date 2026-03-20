"""Server configuration loading from YAML."""

import yaml
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ApiKeyEntry:
    key: str
    github_token: str
    repos: list[str] = field(default_factory=list)


@dataclass
class Config:
    api_keys: list[ApiKeyEntry] = field(default_factory=list)

    def get_github_token(self, api_key: str, repo: str) -> str | None:
        """Look up the GitHub token for a given API key and repo.

        Returns the GitHub token if the API key is valid and has access to the repo,
        or None if not found/unauthorized.
        """
        for entry in self.api_keys:
            if entry.key == api_key and repo in entry.repos:
                return entry.github_token
        return None

    def validate_api_key(self, api_key: str) -> bool:
        """Check if an API key exists in the config."""
        return any(entry.key == api_key for entry in self.api_keys)


def load_config(path: str | Path) -> Config:
    """Load config from a YAML file."""
    path = Path(path)
    with open(path) as f:
        data = yaml.safe_load(f)

    api_keys = []
    for entry in data.get("api_keys", []):
        api_keys.append(ApiKeyEntry(
            key=entry["key"],
            github_token=entry["github_token"],
            repos=entry.get("repos", []),
        ))

    return Config(api_keys=api_keys)
