"""Tests for the Homebrew formula structure and install ordering.

The formula at homebrew/aquarco.rb must follow a specific ordering in its
install method:

1. ``inreplace`` patches ``_build.py`` (build-type → production)
2. ``pip_install`` creates the virtualenv
3. ``(share/"aquarco").install Dir["*"]`` copies the source tree

If ``share`` install happens *before* ``inreplace`` or ``pip_install``, Homebrew
will have already moved files out of ``buildpath``, causing the subsequent
operations to silently operate on an empty directory.

See commit dc3a8fc ("fix: patch _build.py before moving files to share").
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FORMULA_PATH = REPO_ROOT / "homebrew" / "aquarco.rb"


@pytest.fixture()
def formula_text() -> str:
    return FORMULA_PATH.read_text()


@pytest.fixture()
def install_block(formula_text: str) -> str:
    """Extract the ``def install`` method body."""
    match = re.search(
        r"def install\n(.*?)(?=\n  def |\nend)",
        formula_text,
        re.DOTALL,
    )
    assert match, "Could not locate `def install` block in formula"
    return match.group(1)


# ------------------------------------------------------------------
# Structural sanity
# ------------------------------------------------------------------


class TestFormulaStructure:
    """Basic structural checks on the formula file."""

    def test_formula_file_exists(self):
        assert FORMULA_PATH.exists(), f"Formula not found at {FORMULA_PATH}"

    def test_class_inherits_formula(self, formula_text: str):
        assert "class Aquarco < Formula" in formula_text

    def test_includes_virtualenv_dsl(self, formula_text: str):
        assert "include Language::Python::Virtualenv" in formula_text

    def test_has_install_method(self, formula_text: str):
        assert "def install" in formula_text

    def test_has_test_block(self, formula_text: str):
        assert re.search(r"^\s+test do\b", formula_text, re.MULTILINE)

    def test_sha256_placeholder_present(self, formula_text: str):
        """CI stamps the real SHA — the source should contain the placeholder."""
        assert "PLACEHOLDER_SHA256" in formula_text


# ------------------------------------------------------------------
# Install step ordering — the core invariant fixed in this PR
# ------------------------------------------------------------------


class TestInstallOrdering:
    """Ensure the install method steps are in the correct order.

    The critical ordering is:
        inreplace  →  pip_install  →  share install  →  bin wrapper
    """

    @staticmethod
    def _line_of(block: str, needle: str) -> int:
        """Return the first line number (0-based) containing *needle*."""
        for idx, line in enumerate(block.splitlines()):
            if needle in line:
                return idx
        raise AssertionError(f"'{needle}' not found in install block")

    def test_inreplace_before_pip_install(self, install_block: str):
        inreplace_line = self._line_of(install_block, "inreplace")
        pip_line = self._line_of(install_block, "pip_install")
        assert inreplace_line < pip_line, (
            "inreplace must come before pip_install; "
            "otherwise _build.py is not patched before the wheel is built"
        )

    def test_pip_install_before_share_install(self, install_block: str):
        pip_line = self._line_of(install_block, "pip_install")
        share_line = self._line_of(install_block, '(share/"aquarco").install')
        assert pip_line < share_line, (
            "pip_install must come before share install; "
            "Homebrew moves buildpath contents during share install"
        )

    def test_inreplace_before_share_install(self, install_block: str):
        """Direct check for the exact bug fixed in this commit."""
        inreplace_line = self._line_of(install_block, "inreplace")
        share_line = self._line_of(install_block, '(share/"aquarco").install')
        assert inreplace_line < share_line, (
            "inreplace must come before share install; "
            "share install moves files out of buildpath so inreplace would "
            "operate on missing files"
        )

    def test_share_install_before_bin_wrapper(self, install_block: str):
        share_line = self._line_of(install_block, '(share/"aquarco").install')
        bin_line = self._line_of(install_block, '(bin/"aquarco").write')
        assert share_line < bin_line, (
            "share install must come before the bin wrapper; "
            "wrapper references share path"
        )


# ------------------------------------------------------------------
# Content correctness
# ------------------------------------------------------------------


class TestInstallContent:
    """Verify the formula contains the expected install operations."""

    def test_patches_build_type_to_production(self, install_block: str):
        assert 'BUILD_TYPE: str = "production"' in install_block

    def test_creates_python311_virtualenv(self, install_block: str):
        assert 'virtualenv_create(libexec, "python3.11")' in install_block

    def test_installs_cli_from_buildpath(self, install_block: str):
        assert 'pip_install buildpath/"cli"' in install_block

    def test_share_comment_explains_ordering(self, install_block: str):
        """The comment added in this commit must be present."""
        assert "Must happen after pip_install" in install_block

    def test_bin_wrapper_sets_vagrant_dir(self, install_block: str):
        assert "AQUARCO_VAGRANT_DIR" in install_block

    def test_bin_wrapper_sets_docker_mode(self, install_block: str):
        assert "AQUARCO_DOCKER_MODE" in install_block

    def test_bin_wrapper_is_executable(self, install_block: str):
        assert "chmod 0555" in install_block


# ------------------------------------------------------------------
# Test block validation
# ------------------------------------------------------------------


class TestFormulaTestBlock:
    """Verify the formula's built-in ``test do`` block."""

    @pytest.fixture()
    def test_block(self, formula_text: str) -> str:
        match = re.search(
            r"test do\n(.*?)(?=\n  end)",
            formula_text,
            re.DOTALL,
        )
        assert match, "Could not locate `test do` block"
        return match.group(1)

    def test_checks_version_output(self, test_block: str):
        assert "assert_match" in test_block
        assert "--version" in test_block

    def test_checks_update_guard(self, test_block: str):
        assert "not available" in test_block
