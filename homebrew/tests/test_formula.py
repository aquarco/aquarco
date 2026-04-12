"""Tests for the Homebrew formula (aquarco.rb).

Validates structural properties of the formula file: dependency declarations,
class structure, and absence of removed sections.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FORMULA_PATH = Path(__file__).resolve().parent.parent / "aquarco.rb"


@pytest.fixture()
def formula_text() -> str:
    """Load the formula source once per test."""
    return FORMULA_PATH.read_text()


class TestCaskDependencies:
    """The formula must declare VirtualBox and Vagrant as cask dependencies."""

    def test_depends_on_virtualbox_cask(self, formula_text: str) -> None:
        assert 'depends_on cask: "virtualbox"' in formula_text

    def test_depends_on_vagrant_cask(self, formula_text: str) -> None:
        assert 'depends_on cask: "vagrant"' in formula_text

    def test_virtualbox_declared_before_vagrant(self, formula_text: str) -> None:
        """VirtualBox should be declared before Vagrant (dependency order)."""
        vbox_pos = formula_text.index('depends_on cask: "virtualbox"')
        vagrant_pos = formula_text.index('depends_on cask: "vagrant"')
        assert vbox_pos < vagrant_pos, (
            "VirtualBox cask dependency should appear before Vagrant"
        )

    def test_python_dependency_still_present(self, formula_text: str) -> None:
        assert 'depends_on "python@3.11"' in formula_text


class TestCaveatsRemoved:
    """The caveats method was replaced by cask dependencies and must be gone."""

    def test_no_caveats_method(self, formula_text: str) -> None:
        assert "def caveats" not in formula_text

    def test_no_manual_install_instructions(self, formula_text: str) -> None:
        """No leftover manual install instructions for VirtualBox/Vagrant."""
        assert "brew install --cask virtualbox" not in formula_text
        assert "brew install --cask vagrant" not in formula_text


class TestFormulaStructure:
    """Basic structural checks on the formula."""

    def test_class_inherits_formula(self, formula_text: str) -> None:
        assert "class Aquarco < Formula" in formula_text

    def test_has_test_block(self, formula_text: str) -> None:
        assert "test do" in formula_text

    def test_has_install_method(self, formula_text: str) -> None:
        assert "def install" in formula_text

    def test_version_declared(self, formula_text: str) -> None:
        assert re.search(r'version\s+"[^"]+"', formula_text)

    def test_frozen_string_literal(self, formula_text: str) -> None:
        assert formula_text.startswith("# frozen_string_literal: true")
