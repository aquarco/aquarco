"""Tests for the Homebrew cask (homebrew/aquarco.rb).

Validates that the cask follows Homebrew conventions for a prebuilt binary:
- Uses `cask` syntax (not Formula)
- Declares cask dependencies on VirtualBox and Vagrant
- Has required lifecycle hooks (postflight, uninstall_preflight, zap)
- Installs the binary correctly
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FORMULA_PATH = Path(__file__).resolve().parents[3] / "homebrew" / "aquarco.rb"


@pytest.fixture
def formula_text() -> str:
    """Load the Homebrew cask source."""
    return FORMULA_PATH.read_text()


class TestHomebrewCaskStructure:
    """Basic structural checks — file must be a Homebrew Cask, not a Formula."""

    def test_is_cask_not_formula(self, formula_text: str) -> None:
        """File must use `cask` DSL, not `class ... < Formula`."""
        assert re.search(r'^cask\s+"aquarco"\s+do\b', formula_text, re.MULTILINE)
        assert not re.search(r"class\s+Aquarco\s*<\s*Formula", formula_text)

    def test_has_version_stanza(self, formula_text: str) -> None:
        """Cask must declare a version."""
        assert re.search(r'^\s+version\s+', formula_text, re.MULTILINE)

    def test_has_sha256_stanza(self, formula_text: str) -> None:
        """Cask must declare a sha256 checksum."""
        assert re.search(r'^\s+sha256\s+', formula_text, re.MULTILINE)

    def test_has_url_stanza(self, formula_text: str) -> None:
        """Cask must declare a download URL."""
        assert re.search(r'^\s+url\s+', formula_text, re.MULTILINE)

    def test_has_binary_stanza(self, formula_text: str) -> None:
        """Cask must expose the binary via the `binary` stanza."""
        assert re.search(r'^\s+binary\s+', formula_text, re.MULTILINE)


class TestHomebrewCaskDependencies:
    """Cask must declare VirtualBox and Vagrant as cask dependencies."""

    def test_depends_on_virtualbox(self, formula_text: str) -> None:
        """Cask must depend on the VirtualBox cask."""
        assert 'depends_on cask: "virtualbox"' in formula_text

    def test_depends_on_vagrant(self, formula_text: str) -> None:
        """Cask must depend on the Vagrant cask."""
        assert 'depends_on cask: "vagrant"' in formula_text


class TestHomebrewCaskLifecycle:
    """Cask must define the required lifecycle hooks."""

    def test_has_postflight(self, formula_text: str) -> None:
        """Cask must have a postflight block (quarantine strip, warm-up)."""
        assert re.search(r'^\s+postflight\s+do\b', formula_text, re.MULTILINE)

    def test_postflight_strips_quarantine(self, formula_text: str) -> None:
        """postflight must strip Gatekeeper quarantine from the binary."""
        assert "com.apple.quarantine" in formula_text

    def test_has_uninstall_preflight(self, formula_text: str) -> None:
        """Cask must have an uninstall_preflight block to back up before removal."""
        assert re.search(r'^\s+uninstall_preflight\s+do\b', formula_text, re.MULTILINE)

    def test_has_zap_stanza(self, formula_text: str) -> None:
        """Cask must have a zap stanza for full data removal."""
        assert re.search(r'^\s+zap\s+', formula_text, re.MULTILINE)

    def test_zap_removes_aquarco_dir(self, formula_text: str) -> None:
        """zap must trash the ~/.aquarco user data directory."""
        assert '"~/.aquarco"' in formula_text or "'~/.aquarco'" in formula_text
