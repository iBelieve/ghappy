"""Server configuration loading from YAML."""

import hmac
import logging
import stat
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger("ghappy")


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
            if hmac.compare_digest(entry.key, api_key) and repo in entry.repos:
                return entry.github_token
        return None

    def validate_api_key(self, api_key: str) -> bool:
        """Check if an API key exists in the config."""
        return any(hmac.compare_digest(entry.key, api_key) for entry in self.api_keys)


def _check_file_permissions(path: Path) -> None:
    """Warn if the config file is readable by group or others."""
    try:
        mode = path.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            logger.warning(
                "Config file %s is readable by group/others (mode %o). "
                "Consider restricting with: chmod 600 %s",
                path,
                stat.S_IMODE(mode),
                path,
            )
    except OSError:
        pass


def load_config(path: str | Path) -> Config:
    """Load config from a YAML file."""
    path = Path(path)
    _check_file_permissions(path)
    with open(path) as f:
        data = yaml.safe_load(f)

    api_keys = []
    for entry in data.get("api_keys", []):
        api_keys.append(
            ApiKeyEntry(
                key=entry["key"],
                github_token=entry["github_token"],
                repos=entry.get("repos", []),
            )
        )

    return Config(api_keys=api_keys)
