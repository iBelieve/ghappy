"""Tests for ghappy.config module."""

import logging
from pathlib import Path

import pytest
import yaml

from ghappy.config import ApiKeyEntry, Config, _check_file_permissions, load_config


class TestConfig:
    """Tests for the Config dataclass."""

    def _make_config(self) -> Config:
        return Config(
            api_keys=[
                ApiKeyEntry(
                    key="key-alpha",
                    github_token="ghp_alpha",
                    repos=["owner/repo-a", "owner/repo-b"],
                ),
                ApiKeyEntry(
                    key="key-beta",
                    github_token="ghp_beta",
                    repos=["owner/repo-c"],
                ),
            ]
        )

    def test_get_github_token_valid(self):
        config = self._make_config()
        assert config.get_github_token("key-alpha", "owner/repo-a") == "ghp_alpha"
        assert config.get_github_token("key-beta", "owner/repo-c") == "ghp_beta"

    def test_get_github_token_wrong_repo(self):
        config = self._make_config()
        assert config.get_github_token("key-alpha", "owner/repo-c") is None

    def test_get_github_token_invalid_key(self):
        config = self._make_config()
        assert config.get_github_token("bad-key", "owner/repo-a") is None

    def test_get_github_token_empty_config(self):
        config = Config()
        assert config.get_github_token("any-key", "any/repo") is None

    def test_validate_api_key_valid(self):
        config = self._make_config()
        assert config.validate_api_key("key-alpha") is True
        assert config.validate_api_key("key-beta") is True

    def test_validate_api_key_invalid(self):
        config = self._make_config()
        assert config.validate_api_key("bad-key") is False

    def test_validate_api_key_empty_config(self):
        config = Config()
        assert config.validate_api_key("any-key") is False


class TestLoadConfig:
    """Tests for load_config."""

    def test_load_valid_config(self, tmp_path: Path):
        config_data = {
            "api_keys": [
                {
                    "key": "test-key",
                    "github_token": "ghp_test",
                    "repos": ["owner/repo"],
                }
            ]
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert len(config.api_keys) == 1
        assert config.api_keys[0].key == "test-key"
        assert config.api_keys[0].github_token == "ghp_test"
        assert config.api_keys[0].repos == ["owner/repo"]

    def test_load_config_missing_repos_defaults_to_empty(self, tmp_path: Path):
        config_data = {"api_keys": [{"key": "test-key", "github_token": "ghp_test"}]}
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert config.api_keys[0].repos == []

    def test_load_config_no_api_keys(self, tmp_path: Path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({}))

        config = load_config(config_file)
        assert config.api_keys == []

    def test_load_config_multiple_keys(self, tmp_path: Path):
        config_data = {
            "api_keys": [
                {"key": "k1", "github_token": "t1", "repos": ["a/b"]},
                {"key": "k2", "github_token": "t2", "repos": ["c/d", "e/f"]},
            ]
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert len(config.api_keys) == 2
        assert config.api_keys[1].repos == ["c/d", "e/f"]

    def test_load_config_file_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")


class TestCheckFilePermissions:
    """Tests for _check_file_permissions."""

    def test_warns_on_group_readable(self, tmp_path: Path, caplog):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("test: true")
        config_file.chmod(0o644)

        with caplog.at_level(logging.WARNING, logger="ghappy"):
            _check_file_permissions(config_file)
        assert "readable by group/others" in caplog.text

    def test_no_warning_on_restricted(self, tmp_path: Path, caplog):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("test: true")
        config_file.chmod(0o600)

        with caplog.at_level(logging.WARNING, logger="ghappy"):
            _check_file_permissions(config_file)
        assert "readable by group/others" not in caplog.text

    def test_no_error_on_missing_file(self):
        _check_file_permissions(Path("/nonexistent/path"))
