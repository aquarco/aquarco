"""Tests for version string consistency and PEP 440 compliance.

Validates that the version string used in pyproject.toml, __init__.py,
the Homebrew formula, and docker/versions.env are all consistent and
follow PEP 440 formatting rules.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


def _find_repo_root() -> Path:
    """Find the repository root using git or path traversal."""
    try:
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return Path(root)
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback: walk up from this file until we find cli/pyproject.toml
        p = Path(__file__).resolve().parent
        for _ in range(10):
            if (p / "cli" / "pyproject.toml").exists():
                return p
            p = p.parent
        raise RuntimeError("Cannot find repo root")


REPO_ROOT = _find_repo_root()
CLI_ROOT = REPO_ROOT / "cli"


def _read_text(path: Path) -> str:
    """Read a file relative to the repo root."""
    return path.read_text(encoding="utf-8")


def _extract_pyproject_version() -> str:
    """Extract version from cli/pyproject.toml."""
    content = _read_text(CLI_ROOT / "pyproject.toml")
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    assert match, "Could not find version in pyproject.toml"
    return match.group(1)


def _extract_init_version() -> str:
    """Extract __version__ from cli/src/aquarco_cli/__init__.py."""
    content = _read_text(CLI_ROOT / "src" / "aquarco_cli" / "__init__.py")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', content, re.MULTILINE)
    assert match, "Could not find __version__ in __init__.py"
    return match.group(1)


def _extract_homebrew_version() -> str | None:
    """Extract the version declaration from homebrew/aquarco.rb."""
    path = REPO_ROOT / "homebrew" / "aquarco.rb"
    if not path.exists():
        return None
    content = _read_text(path)
    match = re.search(r'^\s*version\s+"([^"]+)"', content, re.MULTILINE)
    assert match, "Could not find version in homebrew/aquarco.rb"
    return match.group(1)


def _extract_homebrew_test_version() -> str | None:
    """Extract the version string asserted in the Homebrew test block."""
    path = REPO_ROOT / "homebrew" / "aquarco.rb"
    if not path.exists():
        return None
    content = _read_text(path)
    match = re.search(r'assert_match\s+"([^"]+)".*shell_output.*--version', content)
    assert match, "Could not find version assertion in homebrew test block"
    return match.group(1)


def _extract_homebrew_url_tag() -> str | None:
    """Extract the git tag from the Homebrew url field."""
    path = REPO_ROOT / "homebrew" / "aquarco.rb"
    if not path.exists():
        return None
    content = _read_text(path)
    match = re.search(r'url\s+"[^"]*refs/tags/v([^"]+)\.tar\.gz"', content)
    assert match, "Could not find tag in homebrew url"
    return match.group(1)


def _extract_docker_versions() -> dict[str, str]:
    """Extract AQUARCO_*_VERSION values from docker/versions.env."""
    path = REPO_ROOT / "docker" / "versions.env"
    result = {}
    content = _read_text(path)
    for match in re.finditer(
        r"^(AQUARCO_(?:API|WEB|MIGRATIONS)_VERSION)=(.+)$", content, re.MULTILINE
    ):
        result[match.group(1)] = match.group(2).strip()
    return result


# ---------------------------------------------------------------------------
# PEP 440 compliance
# ---------------------------------------------------------------------------

# PEP 440 pattern (simplified but covers common forms including rc/alpha/beta)
PEP440_RE = re.compile(
    r"^([1-9]\d*|0)"          # epoch-free major
    r"(\.\d+)*"               # minor, patch, etc.
    r"(a|b|rc)\d+"            # pre-release segment
    r"(\.post\d+)?"           # optional post
    r"(\.dev\d+)?$"           # optional dev
    r"|"
    r"^([1-9]\d*|0)"          # OR plain release
    r"(\.\d+)*"
    r"(\.post\d+)?"
    r"(\.dev\d+)?$"
)


class TestPEP440Compliance:
    """Ensure version strings conform to PEP 440."""

    def test_pyproject_version_is_pep440(self):
        version = _extract_pyproject_version()
        assert PEP440_RE.match(version), (
            f"pyproject.toml version '{version}' is not PEP 440 compliant"
        )

    def test_init_version_is_pep440(self):
        version = _extract_init_version()
        assert PEP440_RE.match(version), (
            f"__init__.py __version__ '{version}' is not PEP 440 compliant"
        )

    def test_version_does_not_use_old_rc_dash_format(self):
        """Regression: old format was 'rc-X.Y.Z' which is not PEP 440."""
        version = _extract_pyproject_version()
        assert not version.startswith("rc-"), (
            f"Version '{version}' uses the old rc-X.Y.Z format"
        )

    def test_version_uses_pep440_prerelease_suffix(self):
        """If version contains 'rc', it must be a suffix like X.Y.ZrcN."""
        version = _extract_pyproject_version()
        if "rc" in version:
            assert re.match(r"^\d+\.\d+\.\d+rc\d+$", version), (
                f"Pre-release version '{version}' should follow X.Y.ZrcN pattern"
            )


# ---------------------------------------------------------------------------
# Cross-file consistency
# ---------------------------------------------------------------------------


class TestVersionConsistency:
    """All version strings across the project must be identical."""

    def test_pyproject_matches_init(self):
        assert _extract_pyproject_version() == _extract_init_version(), (
            "pyproject.toml and __init__.py versions must match"
        )

    @pytest.mark.skipif(
        not (REPO_ROOT / "homebrew" / "aquarco.rb").exists(),
        reason="Homebrew formula not present",
    )
    def test_pyproject_matches_homebrew_version(self):
        assert _extract_pyproject_version() == _extract_homebrew_version(), (
            "pyproject.toml and homebrew formula versions must match"
        )

    @pytest.mark.skipif(
        not (REPO_ROOT / "homebrew" / "aquarco.rb").exists(),
        reason="Homebrew formula not present",
    )
    def test_homebrew_test_matches_version(self):
        assert _extract_homebrew_version() == _extract_homebrew_test_version(), (
            "Homebrew formula version and test assertion must match"
        )

    @pytest.mark.skipif(
        not (REPO_ROOT / "homebrew" / "aquarco.rb").exists(),
        reason="Homebrew formula not present",
    )
    def test_homebrew_url_tag_matches_version(self):
        """The download URL tag must match the declared version."""
        assert _extract_homebrew_version() == _extract_homebrew_url_tag(), (
            "Homebrew url tag and version declaration must match"
        )


# ---------------------------------------------------------------------------
# Docker versions.env
# ---------------------------------------------------------------------------


class TestDockerVersionsEnv:
    """Validate docker/versions.env consistency."""

    def test_all_app_versions_are_identical(self):
        """API, WEB, and MIGRATIONS versions must be the same."""
        versions = _extract_docker_versions()
        assert len(versions) == 3, (
            f"Expected 3 AQUARCO_*_VERSION entries, got {len(versions)}: {versions}"
        )
        unique = set(versions.values())
        assert len(unique) == 1, (
            f"Docker image versions are inconsistent: {versions}"
        )

    def test_docker_versions_do_not_use_old_format(self):
        """Regression: old format was 'rc-X.Y.Z'."""
        for key, value in _extract_docker_versions().items():
            assert not value.startswith("rc-"), (
                f"{key}={value} uses the old rc-X.Y.Z format"
            )

    def test_docker_versions_are_not_latest(self):
        """versions.env should never use 'latest'."""
        for key, value in _extract_docker_versions().items():
            assert value != "latest", (
                f"{key} must not be 'latest' — pin an explicit version"
            )

    def test_no_empty_version_values(self):
        """Every version entry must have a non-empty value."""
        for key, value in _extract_docker_versions().items():
            assert value, f"{key} has an empty value"


# ---------------------------------------------------------------------------
# Homebrew formula structural checks
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (REPO_ROOT / "homebrew" / "aquarco.rb").exists(),
    reason="Homebrew formula not present",
)
class TestHomebrewFormula:
    """Validate structural aspects of the Homebrew formula."""

    def test_comment_references_current_version(self):
        """The comment describing tagged images should reference the current version."""
        version = _extract_homebrew_version()
        content = _read_text(REPO_ROOT / "homebrew" / "aquarco.rb")
        # Find the comment line about tagged images
        match = re.search(r"#.*tagged\s+(\S+)\s+from", content)
        if match:
            comment_version = match.group(1)
            assert comment_version == version, (
                f"Comment references '{comment_version}' but formula version is '{version}'"
            )

    def test_url_points_to_github_archive(self):
        content = _read_text(REPO_ROOT / "homebrew" / "aquarco.rb")
        assert "github.com" in content, "Homebrew url should reference GitHub"
        assert "archive/refs/tags/" in content, "URL should use GitHub archive format"

    def test_sha256_field_exists(self):
        content = _read_text(REPO_ROOT / "homebrew" / "aquarco.rb")
        assert re.search(r'sha256\s+"', content), "Formula must have a sha256 field"
