# frozen_string_literal: false — this is Python, not Ruby :)
"""Tests for the Homebrew cask definition (homebrew/aquarco.rb).

Validates structural correctness of the cask file, with emphasis on the
postflight block that strips Gatekeeper quarantine on install.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CASK_PATH = Path(__file__).resolve().parent.parent / "aquarco.rb"


@pytest.fixture
def cask_content() -> str:
    """Read the cask file content."""
    return CASK_PATH.read_text()


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------

class TestCaskStructure:
    """Verify required fields and blocks in the cask definition."""

    def test_cask_file_exists(self):
        assert CASK_PATH.exists(), f"Cask file not found at {CASK_PATH}"

    def test_cask_block_present(self, cask_content: str):
        assert re.search(r'cask\s+"aquarco"\s+do', cask_content), (
            "Missing top-level cask block"
        )

    def test_version_declared(self, cask_content: str):
        assert re.search(r'^\s*version\s+"[^"]+"', cask_content, re.MULTILINE), (
            "Missing version declaration"
        )

    def test_sha256_declared(self, cask_content: str):
        assert re.search(r'^\s*sha256\s+"[^"]+"', cask_content, re.MULTILINE), (
            "Missing sha256 declaration"
        )

    def test_url_declared(self, cask_content: str):
        assert re.search(r'^\s*url\s+"https?://', cask_content, re.MULTILINE), (
            "Missing url declaration"
        )

    def test_binary_declared(self, cask_content: str):
        assert re.search(r'^\s*binary\s+"aquarco"', cask_content, re.MULTILINE), (
            "Missing binary declaration"
        )

    def test_depends_on_virtualbox(self, cask_content: str):
        assert re.search(
            r'depends_on\s+cask:\s+"virtualbox"', cask_content
        ), "Missing VirtualBox dependency"

    def test_depends_on_vagrant(self, cask_content: str):
        assert re.search(
            r'depends_on\s+cask:\s+"vagrant"', cask_content
        ), "Missing Vagrant dependency"


# ---------------------------------------------------------------------------
# Postflight / Gatekeeper quarantine tests (the change under review)
# ---------------------------------------------------------------------------

class TestPostflightQuarantine:
    """Verify the postflight block strips macOS Gatekeeper quarantine."""

    def test_postflight_block_exists(self, cask_content: str):
        assert re.search(r'^\s*postflight\s+do\b', cask_content, re.MULTILINE), (
            "Missing postflight block — macOS will quarantine the unsigned binary"
        )

    def test_xattr_command_used(self, cask_content: str):
        """The postflight should call /usr/bin/xattr."""
        assert re.search(
            r'system_command\s+"/usr/bin/xattr"', cask_content
        ), "postflight must invoke /usr/bin/xattr via system_command"

    def test_quarantine_attribute_stripped(self, cask_content: str):
        """The quarantine extended attribute must be explicitly named."""
        assert "com.apple.quarantine" in cask_content, (
            "postflight must strip the com.apple.quarantine xattr"
        )

    def test_xattr_uses_recursive_delete(self, cask_content: str):
        """Flags should include -d (delete) and -r (recursive)."""
        # -dr or -rd both acceptable; we look for both flags present
        match = re.search(
            r'args:\s*\[([^\]]+)\]', cask_content
        )
        assert match, "Could not find args array for xattr command"
        args_text = match.group(1)
        assert re.search(r'"-d[r]?"', args_text) or re.search(r'"-rd"', args_text), (
            "xattr args must include -dr (recursive delete)"
        )

    def test_xattr_targets_staged_path(self, cask_content: str):
        """The xattr command must target staged_path (Homebrew install dir)."""
        assert re.search(
            r'staged_path\.to_s', cask_content
        ), "xattr must target staged_path.to_s"

    def test_postflight_has_comment(self, cask_content: str):
        """The postflight block should have a comment explaining the purpose."""
        # Find the postflight block and check for a comment within it
        postflight_match = re.search(
            r'postflight\s+do\s*\n(.*?)end',
            cask_content,
            re.DOTALL,
        )
        assert postflight_match, "Could not isolate postflight block"
        block_body = postflight_match.group(1)
        assert "#" in block_body, (
            "postflight block should include a comment explaining the quarantine strip"
        )
