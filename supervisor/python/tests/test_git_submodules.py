"""Tests for .gitmodules configuration and submodule integrity.

Validates that git submodules are correctly configured with HTTPS URLs,
expected paths, and proper format.
"""

from __future__ import annotations

import configparser
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GITMODULES_PATH = REPO_ROOT / ".gitmodules"


class TestGitmodulesFile:
    """Validate the .gitmodules file exists and is well-formed."""

    def test_gitmodules_file_exists(self) -> None:
        """The .gitmodules file must exist at the repo root."""
        assert GITMODULES_PATH.is_file(), f".gitmodules not found at {GITMODULES_PATH}"

    def test_gitmodules_is_valid_ini(self) -> None:
        """The .gitmodules file must be valid INI/config format."""
        parser = configparser.ConfigParser()
        parser.read(str(GITMODULES_PATH))
        assert len(parser.sections()) > 0, ".gitmodules has no sections"

    def test_gitmodules_has_wiki_submodule(self) -> None:
        """The wiki submodule must be defined."""
        parser = configparser.ConfigParser()
        parser.read(str(GITMODULES_PATH))
        assert 'submodule "wiki"' in parser.sections(), (
            f"Expected 'submodule \"wiki\"' section, found: {parser.sections()}"
        )

    def test_wiki_submodule_path(self) -> None:
        """The wiki submodule path must be 'wiki'."""
        parser = configparser.ConfigParser()
        parser.read(str(GITMODULES_PATH))
        section = 'submodule "wiki"'
        assert parser.has_option(section, "path"), "wiki submodule missing 'path'"
        assert parser.get(section, "path") == "wiki", (
            f"Expected path 'wiki', got '{parser.get(section, 'path')}'"
        )

    def test_wiki_submodule_uses_https_url(self) -> None:
        """The wiki submodule URL must use HTTPS (not SSH) for portability."""
        parser = configparser.ConfigParser()
        parser.read(str(GITMODULES_PATH))
        section = 'submodule "wiki"'
        url = parser.get(section, "url")
        assert url.startswith("https://"), (
            f"Submodule URL should use HTTPS for portability, got: {url}"
        )

    def test_wiki_submodule_url_points_to_aquarco_wiki(self) -> None:
        """The wiki submodule URL must point to the aquarco wiki repo."""
        parser = configparser.ConfigParser()
        parser.read(str(GITMODULES_PATH))
        section = 'submodule "wiki"'
        url = parser.get(section, "url")
        assert "aquarco/aquarco.wiki.git" in url, (
            f"Expected URL to contain 'aquarco/aquarco.wiki.git', got: {url}"
        )

    def test_all_submodule_urls_are_https(self) -> None:
        """Every submodule in .gitmodules must use HTTPS URLs (security policy)."""
        parser = configparser.ConfigParser()
        parser.read(str(GITMODULES_PATH))
        for section in parser.sections():
            if section.startswith("submodule "):
                url = parser.get(section, "url", fallback="")
                assert url.startswith("https://"), (
                    f"{section}: URL must use HTTPS, got: {url}"
                )


class TestGitSubmoduleIntegrity:
    """Validate git submodule registration via git commands."""

    def test_git_submodule_status_runs_without_error(self) -> None:
        """git submodule status must succeed (no corrupt submodule state)."""
        result = subprocess.run(
            ["git", "submodule", "status"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"git submodule status failed: {result.stderr}"
        )

    def test_wiki_submodule_is_registered(self) -> None:
        """The wiki submodule must appear in git submodule output."""
        result = subprocess.run(
            ["git", "submodule", "status"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "wiki" in result.stdout, (
            f"'wiki' not found in submodule status output: {result.stdout}"
        )

    def test_wiki_submodule_has_pinned_commit(self) -> None:
        """The wiki submodule must be pinned to a specific commit SHA."""
        result = subprocess.run(
            ["git", "submodule", "status"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Format: " <sha> wiki (<ref>)" or "-<sha> wiki" (not initialized)
        for line in result.stdout.strip().splitlines():
            if "wiki" in line:
                # Strip leading +/- and extract SHA
                sha_part = line.lstrip(" +-")
                sha = sha_part.split()[0]
                assert len(sha) >= 7, (
                    f"Expected a commit SHA for wiki submodule, got: {line}"
                )
                assert all(c in "0123456789abcdef" for c in sha), (
                    f"Invalid SHA characters in: {sha}"
                )
                return
        pytest.fail("wiki submodule not found in git submodule status output")
