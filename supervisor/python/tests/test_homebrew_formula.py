"""Tests for the Homebrew formula (homebrew/aquarco.rb).

Validates that the formula follows Homebrew conventions:
- No unsupported `depends_on cask:` directives
- A `caveats` block instructs users to install cask dependencies manually
- Required formula dependencies are present
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FORMULA_PATH = Path(__file__).resolve().parents[3] / "homebrew" / "aquarco.rb"


@pytest.fixture
def formula_text() -> str:
    """Load the Homebrew formula source."""
    return FORMULA_PATH.read_text()


class TestHomebrewFormulaNoCaskDeps:
    """Homebrew formulas cannot declare cask dependencies.

    The `depends_on cask:` syntax is not supported by Homebrew core and causes
    `brew audit` failures.  These tests ensure the unsupported directives were
    removed and stay removed.
    """

    def test_no_depends_on_cask_vagrant(self, formula_text: str) -> None:
        """Formula must not declare a cask dependency on Vagrant."""
        assert 'depends_on cask: "vagrant"' not in formula_text

    def test_no_depends_on_cask_virtualbox(self, formula_text: str) -> None:
        """Formula must not declare a cask dependency on VirtualBox."""
        assert 'depends_on cask: "virtualbox"' not in formula_text

    def test_no_depends_on_cask_any(self, formula_text: str) -> None:
        """Formula must not use `depends_on cask:` for any package."""
        matches = re.findall(r"depends_on\s+cask:", formula_text)
        assert matches == [], f"Found unsupported cask deps: {matches}"


class TestHomebrewFormulaCaveats:
    """The formula should include a caveats block that tells users to install
    VirtualBox and Vagrant manually via `brew install --cask`."""

    def test_caveats_method_exists(self, formula_text: str) -> None:
        """Formula must define a `def caveats` method."""
        assert re.search(r"^\s+def caveats\b", formula_text, re.MULTILINE)

    def test_caveats_mentions_virtualbox(self, formula_text: str) -> None:
        """Caveats must mention VirtualBox so users know to install it."""
        caveats_block = _extract_caveats(formula_text)
        assert "virtualbox" in caveats_block.lower()

    def test_caveats_mentions_vagrant(self, formula_text: str) -> None:
        """Caveats must mention Vagrant so users know to install it."""
        caveats_block = _extract_caveats(formula_text)
        assert "vagrant" in caveats_block.lower()

    def test_caveats_includes_install_commands(self, formula_text: str) -> None:
        """Caveats should include the actual brew install commands."""
        caveats_block = _extract_caveats(formula_text)
        assert "brew install --cask virtualbox" in caveats_block
        assert "brew install --cask vagrant" in caveats_block


class TestHomebrewFormulaDependencies:
    """Verify that legitimate formula-level dependencies are still declared."""

    def test_depends_on_python(self, formula_text: str) -> None:
        """Formula must depend on python@3.11."""
        assert 'depends_on "python@3.11"' in formula_text


class TestHomebrewFormulaStructure:
    """Basic structural checks on the formula."""

    def test_class_inherits_formula(self, formula_text: str) -> None:
        """Formula class must inherit from Formula."""
        assert re.search(r"class\s+Aquarco\s*<\s*Formula", formula_text)

    def test_test_block_exists(self, formula_text: str) -> None:
        """Formula must contain a `test do` block."""
        assert re.search(r"^\s+test do\b", formula_text, re.MULTILINE)

    def test_install_block_exists(self, formula_text: str) -> None:
        """Formula must contain a `def install` block."""
        assert re.search(r"^\s+def install\b", formula_text, re.MULTILINE)


def _extract_caveats(formula_text: str) -> str:
    """Extract the caveats heredoc content from the formula."""
    match = re.search(
        r"def caveats.*?<<~EOS(.*?)EOS", formula_text, re.DOTALL
    )
    assert match, "Could not find caveats heredoc in formula"
    return match.group(1)
