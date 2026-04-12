# frozen_string_literal: false
"""Tests for homebrew/aquarco.rb cask definition.

Validates structural correctness of the Homebrew cask file by parsing the
Ruby source as text. These tests ensure the cask postflight block is
correctly ordered and configured.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CASK_FILE = Path(__file__).resolve().parent.parent / "aquarco.rb"


@pytest.fixture()
def cask_source() -> str:
    """Read the cask file once per test."""
    return CASK_FILE.read_text()


@pytest.fixture()
def postflight_block(cask_source: str) -> str:
    """Extract the postflight do...end block."""
    match = re.search(
        r"postflight\s+do\s*\n(.*?)^\s*end",
        cask_source,
        re.MULTILINE | re.DOTALL,
    )
    assert match, "postflight block not found in cask file"
    return match.group(1)


# ── Cask-level structural tests ─────────────────────────────────────────


class TestCaskStructure:
    """Basic structural assertions on the cask definition."""

    def test_cask_file_exists(self) -> None:
        assert CASK_FILE.is_file(), f"Expected cask file at {CASK_FILE}"

    def test_cask_name_is_aquarco(self, cask_source: str) -> None:
        assert re.search(r'^cask\s+"aquarco"\s+do', cask_source, re.MULTILINE)

    def test_binary_points_to_pyinstaller_entrypoint(self, cask_source: str) -> None:
        assert 'binary "aquarco/aquarco"' in cask_source

    def test_depends_on_virtualbox(self, cask_source: str) -> None:
        assert 'depends_on cask: "virtualbox"' in cask_source

    def test_depends_on_vagrant(self, cask_source: str) -> None:
        assert 'depends_on cask: "vagrant"' in cask_source


# ── Postflight tests ────────────────────────────────────────────────────


class TestPostflight:
    """The postflight block must strip quarantine *then* warm up dyld."""

    def test_quarantine_strip_present(self, postflight_block: str) -> None:
        """Quarantine must be stripped so unsigned binary isn't blocked."""
        assert "com.apple.quarantine" in postflight_block

    def test_dyld_warmup_present(self, postflight_block: str) -> None:
        """dyld warm-up invocation must exist in postflight."""
        assert re.search(r'system_command.*aquarco.*--help', postflight_block)

    def test_warmup_suppresses_stdout(self, postflight_block: str) -> None:
        """Warm-up must not pollute the user's install output."""
        # Extract everything from the warm-up system_command to end of block
        warmup_idx = postflight_block.find("--help")
        assert warmup_idx != -1, "warm-up not found"
        warmup_text = postflight_block[warmup_idx:]
        assert "print_stdout: false" in warmup_text

    def test_warmup_suppresses_stderr(self, postflight_block: str) -> None:
        """Warm-up must not leak errors to the install output."""
        warmup_idx = postflight_block.find("--help")
        assert warmup_idx != -1, "warm-up not found"
        warmup_text = postflight_block[warmup_idx:]
        assert "print_stderr: false" in warmup_text

    def test_quarantine_strip_before_warmup(self, postflight_block: str) -> None:
        """Quarantine must be stripped *before* warm-up, otherwise
        Gatekeeper blocks the unsigned binary invocation."""
        quarantine_pos = postflight_block.find("com.apple.quarantine")
        warmup_pos = postflight_block.find("--help")
        assert quarantine_pos != -1, "quarantine strip not found"
        assert warmup_pos != -1, "dyld warm-up not found"
        assert quarantine_pos < warmup_pos, (
            "quarantine strip must appear before dyld warm-up"
        )

    def test_warmup_uses_staged_path(self, postflight_block: str) -> None:
        """Warm-up must reference the staged_path, not a hardcoded path."""
        assert re.search(r'staged_path.*aquarco/aquarco', postflight_block)

    def test_warmup_passes_help_flag(self, postflight_block: str) -> None:
        """--help is a safe, side-effect-free flag for warm-up."""
        assert re.search(r'args:\s*\["--help"\]', postflight_block)
