"""Tests for Supervisor._apply_anthropic_env and related env methods.

The implementation added _apply_anthropic_env to inject ANTHROPIC_API_KEY
into the process environment for Claude CLI subprocesses. These tests
verify correct behavior for both methods.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from aquarco_supervisor.main import Supervisor


# ---------------------------------------------------------------------------
# _apply_anthropic_env
# ---------------------------------------------------------------------------


class TestApplyAnthropicEnv:
    """Tests for the _apply_anthropic_env method added in this release."""

    def test_sets_anthropic_key_when_present(self, sample_config: Any) -> None:
        """ANTHROPIC_API_KEY is set when the secret is available."""
        supervisor = Supervisor(sample_config, {"anthropic_api_key": "sk-ant-test123"})

        # Clear the env var if it happens to be set
        env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            supervisor._apply_anthropic_env()
            assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-test123"
        finally:
            # Restore original env
            if env_backup is not None:
                os.environ["ANTHROPIC_API_KEY"] = env_backup
            else:
                os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_does_nothing_when_key_absent(self, sample_config: Any) -> None:
        """When no anthropic_api_key in secrets, env is not modified."""
        supervisor = Supervisor(sample_config, {})

        env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            supervisor._apply_anthropic_env()
            assert "ANTHROPIC_API_KEY" not in os.environ
        finally:
            if env_backup is not None:
                os.environ["ANTHROPIC_API_KEY"] = env_backup

    def test_does_nothing_when_key_is_none(self, sample_config: Any) -> None:
        """When anthropic_api_key is explicitly None, env is not modified."""
        supervisor = Supervisor(sample_config, {"anthropic_api_key": None})

        env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            supervisor._apply_anthropic_env()
            assert "ANTHROPIC_API_KEY" not in os.environ
        finally:
            if env_backup is not None:
                os.environ["ANTHROPIC_API_KEY"] = env_backup

    def test_does_nothing_when_key_is_empty_string(self, sample_config: Any) -> None:
        """An empty string is falsy — should not set the env var."""
        supervisor = Supervisor(sample_config, {"anthropic_api_key": ""})

        env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            supervisor._apply_anthropic_env()
            assert "ANTHROPIC_API_KEY" not in os.environ
        finally:
            if env_backup is not None:
                os.environ["ANTHROPIC_API_KEY"] = env_backup


# ---------------------------------------------------------------------------
# _apply_github_env
# ---------------------------------------------------------------------------


class TestApplyGithubEnv:
    """Tests for _apply_github_env — already existed but verifying integration."""

    def test_sets_gh_token_when_present(self, sample_config: Any) -> None:
        """GH_TOKEN and GITHUB_TOKEN are set from secrets."""
        supervisor = Supervisor(sample_config, {"github_token": "ghp_test456"})

        env_backup_gh = os.environ.pop("GH_TOKEN", None)
        env_backup_github = os.environ.pop("GITHUB_TOKEN", None)
        try:
            supervisor._apply_github_env()
            assert os.environ.get("GH_TOKEN") == "ghp_test456"
            assert os.environ.get("GITHUB_TOKEN") == "ghp_test456"
        finally:
            for key, backup in [("GH_TOKEN", env_backup_gh), ("GITHUB_TOKEN", env_backup_github)]:
                if backup is not None:
                    os.environ[key] = backup
                else:
                    os.environ.pop(key, None)

    def test_does_nothing_when_no_github_token(self, sample_config: Any) -> None:
        """When no github_token in secrets, env is not modified."""
        supervisor = Supervisor(sample_config, {})

        env_backup = os.environ.pop("GH_TOKEN", None)
        try:
            supervisor._apply_github_env()
            # GH_TOKEN should not be set by this call
            if env_backup is None:
                assert "GH_TOKEN" not in os.environ
        finally:
            if env_backup is not None:
                os.environ["GH_TOKEN"] = env_backup

    def test_sets_git_terminal_prompt_to_zero(self, sample_config: Any) -> None:
        """GIT_TERMINAL_PROMPT should be set to '0' to prevent interactive prompts."""
        supervisor = Supervisor(sample_config, {"github_token": "ghp_test"})

        env_backup = os.environ.pop("GIT_TERMINAL_PROMPT", None)
        try:
            supervisor._apply_github_env()
            assert os.environ.get("GIT_TERMINAL_PROMPT") == "0"
        finally:
            if env_backup is not None:
                os.environ["GIT_TERMINAL_PROMPT"] = env_backup
            else:
                os.environ.pop("GIT_TERMINAL_PROMPT", None)


# ---------------------------------------------------------------------------
# _refresh_secrets — Claude auth recovery via credentials file
# ---------------------------------------------------------------------------


class TestRefreshSecretsClaude:
    """Verify Claude auth recovery when credentials.json changes."""

    async def test_clears_claude_auth_broken_on_creds_change(
        self, sample_config: Any, tmp_path: Path,
    ) -> None:
        """When credentials.json mtime changes, _claude_auth_broken is cleared."""
        supervisor = Supervisor(sample_config, {})
        supervisor._claude_auth_broken = True
        supervisor._creds_file_mtime = 100.0  # old mtime
        supervisor._db = None  # no DB needed

        # Create a fake credentials file with a new mtime
        creds_file = tmp_path / ".claude" / ".credentials.json"
        creds_file.parent.mkdir(parents=True)
        creds_file.write_text("{}")

        with patch(
            "aquarco_supervisor.main.load_secrets",
            return_value={},
        ), patch(
            "aquarco_supervisor.main.Path",
        ) as mock_path_cls:
            # Make Path("/home/agent/.claude/.credentials.json") point to our temp file
            mock_path_cls.side_effect = lambda *a, **kw: Path(*a, **kw)
            mock_path_cls.home = lambda: tmp_path

            # The code does: creds_path = Path("/home/agent/.claude/.credentials.json")
            # We need to intercept that specific path. Easier: just monkey-patch.
            import types

            original_stat = creds_file.stat

            # Simulate a changed mtime (different from _creds_file_mtime=100.0)
            class FakeStat:
                st_mtime = 200.0

            with patch.object(Path, "stat", return_value=FakeStat()), \
                 patch("aquarco_supervisor.main.asyncio.create_task"):
                supervisor._refresh_secrets()

        # Auth broken should be cleared since mtime changed
        assert supervisor._claude_auth_broken is False

    def test_does_not_clear_when_creds_unchanged(
        self, sample_config: Any,
    ) -> None:
        """When credentials.json has the same mtime, auth flag is NOT cleared."""
        supervisor = Supervisor(sample_config, {})
        supervisor._claude_auth_broken = True
        supervisor._creds_file_mtime = 200.0

        with patch("aquarco_supervisor.main.load_secrets", return_value={}), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value = type("StatResult", (), {"st_mtime": 200.0})()
            supervisor._refresh_secrets()

        # Auth broken should remain True
        assert supervisor._claude_auth_broken is True
