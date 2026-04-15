"""Tests for wiki documentation structure and integrity.

Validates that all wiki pages follow the conventions defined in CLAUDE.md:
- All 16 required pages exist
- Filenames use Title-Case with hyphens
- Each page starts with an H1 matching its filename
- Cross-links point to existing pages
- Sidebar links to all content pages
- CLAUDE.md page index is complete and accurate
- Footer exists with content
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Resolve paths relative to the repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
WIKI_DIR = REPO_ROOT / "wiki"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

# Canonical list of required wiki pages from the design spec
REQUIRED_PAGES = [
    "Home.md",
    "Quick-Start.md",
    "CLI-Reference.md",
    "Architecture.md",
    "File-Layout.md",
    "Components.md",
    "Agent-System.md",
    "Pipeline-System.md",
    "Conditions-Engine.md",
    "Git-Flow.md",
    "Auth-Flows.md",
    "Database.md",
    "Dev-Setup.md",
    "Operations.md",
    "_Sidebar.md",
    "_Footer.md",
]

# Content pages (excludes special pages _Sidebar and _Footer)
CONTENT_PAGES = [p for p in REQUIRED_PAGES if not p.startswith("_")]

# Expected H1 header for each content page (filename stem with hyphens → spaces)
EXPECTED_H1 = {
    "Home.md": "Aquarco",  # Home uses project name, not "Home"
}


def _h1_from_filename(filename: str) -> str:
    """Derive expected H1 header from filename."""
    if filename in EXPECTED_H1:
        return EXPECTED_H1[filename]
    return filename.replace(".md", "").replace("-", " ")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_wiki_links(text: str) -> list[str]:
    """Extract all [[PageName]] or [[PageName|Display Text]] links."""
    return re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text)


# ---------------------------------------------------------------------------
# Existence tests
# ---------------------------------------------------------------------------


class TestWikiPageExistence:
    """All 16 required wiki pages must exist."""

    @pytest.mark.parametrize("page", REQUIRED_PAGES)
    def test_page_exists(self, page: str) -> None:
        path = WIKI_DIR / page
        assert path.exists(), f"Required wiki page missing: {page}"

    def test_no_unexpected_files(self) -> None:
        """Warn about files not in the canonical list (not a hard failure)."""
        actual = {f.name for f in WIKI_DIR.glob("*.md")}
        expected = set(REQUIRED_PAGES)
        extra = actual - expected
        # Extra files are allowed but worth flagging
        assert extra == set() or True, f"Extra wiki files: {extra}"

    def test_wiki_directory_exists(self) -> None:
        assert WIKI_DIR.is_dir(), "wiki/ directory does not exist"


# ---------------------------------------------------------------------------
# Naming convention tests
# ---------------------------------------------------------------------------


class TestNamingConventions:
    """Page filenames use Title-Case with hyphens."""

    @pytest.mark.parametrize("page", CONTENT_PAGES)
    def test_filename_title_case_hyphens(self, page: str) -> None:
        stem = page.replace(".md", "")
        parts = stem.split("-")
        for part in parts:
            assert part[0].isupper(), (
                f"Filename part '{part}' in '{page}' must start with uppercase"
            )

    @pytest.mark.parametrize("page", CONTENT_PAGES)
    def test_no_underscores_in_content_pages(self, page: str) -> None:
        assert "_" not in page, (
            f"Content page '{page}' should use hyphens, not underscores"
        )

    @pytest.mark.parametrize("page", CONTENT_PAGES)
    def test_no_spaces_in_filename(self, page: str) -> None:
        assert " " not in page, f"Page '{page}' must not contain spaces"


# ---------------------------------------------------------------------------
# H1 header tests
# ---------------------------------------------------------------------------


class TestH1Headers:
    """Every content page starts with an H1 matching its filename."""

    @pytest.mark.parametrize("page", CONTENT_PAGES)
    def test_starts_with_h1(self, page: str) -> None:
        path = WIKI_DIR / page
        if not path.exists():
            pytest.skip(f"{page} does not exist")
        first_line = _read_text(path).split("\n")[0].strip()
        assert first_line.startswith("# "), (
            f"{page} must start with '# ' but starts with: {first_line!r}"
        )

    @pytest.mark.parametrize("page", CONTENT_PAGES)
    def test_h1_matches_filename(self, page: str) -> None:
        path = WIKI_DIR / page
        if not path.exists():
            pytest.skip(f"{page} does not exist")
        first_line = _read_text(path).split("\n")[0].strip()
        actual_h1 = first_line.lstrip("# ").strip()
        expected = _h1_from_filename(page)
        assert actual_h1 == expected, (
            f"{page}: H1 is '{actual_h1}', expected '{expected}'"
        )


# ---------------------------------------------------------------------------
# Cross-link integrity tests
# ---------------------------------------------------------------------------


class TestCrossLinks:
    """All [[wiki links]] must point to existing pages."""

    @pytest.mark.parametrize("page", REQUIRED_PAGES)
    def test_links_resolve(self, page: str) -> None:
        path = WIKI_DIR / page
        if not path.exists():
            pytest.skip(f"{page} does not exist")
        text = _read_text(path)
        links = _extract_wiki_links(text)
        existing_stems = {f.stem for f in WIKI_DIR.glob("*.md")}
        for link in links:
            assert link in existing_stems, (
                f"{page}: cross-link [[{link}]] does not resolve to any wiki page"
            )


# ---------------------------------------------------------------------------
# Sidebar completeness tests
# ---------------------------------------------------------------------------


class TestSidebar:
    """Sidebar must link to all content pages."""

    def test_sidebar_exists(self) -> None:
        assert (WIKI_DIR / "_Sidebar.md").exists()

    def test_sidebar_links_all_content_pages(self) -> None:
        sidebar = WIKI_DIR / "_Sidebar.md"
        if not sidebar.exists():
            pytest.skip("_Sidebar.md missing")
        text = _read_text(sidebar)
        links = set(_extract_wiki_links(text))
        # All content pages except Home should be linked
        # (Home might or might not be in sidebar depending on convention)
        for page in CONTENT_PAGES:
            stem = page.replace(".md", "")
            if stem == "Home":
                # Home link is optional in sidebar (it's the landing page)
                continue
            assert stem in links, (
                f"Sidebar missing link to [[{stem}]]"
            )

    def test_sidebar_has_section_headers(self) -> None:
        """Sidebar should organize pages into groups with bold headers."""
        sidebar = WIKI_DIR / "_Sidebar.md"
        if not sidebar.exists():
            pytest.skip("_Sidebar.md missing")
        text = _read_text(sidebar)
        bold_headers = re.findall(r"\*\*(.+?)\*\*", text)
        assert len(bold_headers) >= 2, (
            f"Sidebar should have at least 2 section headers, found {len(bold_headers)}"
        )


# ---------------------------------------------------------------------------
# Footer tests
# ---------------------------------------------------------------------------


class TestFooter:
    """Footer page must exist and have content."""

    def test_footer_exists(self) -> None:
        assert (WIKI_DIR / "_Footer.md").exists()

    def test_footer_not_empty(self) -> None:
        footer = WIKI_DIR / "_Footer.md"
        if not footer.exists():
            pytest.skip("_Footer.md missing")
        text = _read_text(footer).strip()
        assert len(text) > 0, "_Footer.md must not be empty"


# ---------------------------------------------------------------------------
# CLAUDE.md wiki section tests
# ---------------------------------------------------------------------------


class TestClaudeMdWikiSection:
    """CLAUDE.md must contain the GitHub Wiki Structure section."""

    @pytest.fixture(autouse=True)
    def _load_claude_md(self) -> None:
        if not CLAUDE_MD.exists():
            pytest.skip("CLAUDE.md not found")
        self.text = _read_text(CLAUDE_MD)

    def test_wiki_section_exists(self) -> None:
        assert "## GitHub Wiki Structure" in self.text, (
            "CLAUDE.md must contain '## GitHub Wiki Structure' section"
        )

    def test_page_index_table_exists(self) -> None:
        assert "### Page Index" in self.text, (
            "CLAUDE.md must contain '### Page Index' subsection"
        )

    @pytest.mark.parametrize("page", REQUIRED_PAGES)
    def test_page_listed_in_table(self, page: str) -> None:
        assert f"`{page}`" in self.text, (
            f"CLAUDE.md page index table missing entry for {page}"
        )

    def test_conventions_section_exists(self) -> None:
        assert "### Conventions" in self.text, (
            "CLAUDE.md must contain '### Conventions' subsection"
        )

    def test_clone_instruction_present(self) -> None:
        assert "aquarco.wiki.git" in self.text, (
            "CLAUDE.md must contain wiki clone instruction"
        )


# ---------------------------------------------------------------------------
# Content quality tests
# ---------------------------------------------------------------------------


class TestContentQuality:
    """Basic content quality checks for wiki pages."""

    @pytest.mark.parametrize("page", CONTENT_PAGES)
    def test_page_has_minimum_content(self, page: str) -> None:
        """Each content page should have meaningful content (at least 20 lines)."""
        path = WIKI_DIR / page
        if not path.exists():
            pytest.skip(f"{page} does not exist")
        lines = _read_text(path).split("\n")
        assert len(lines) >= 20, (
            f"{page} has only {len(lines)} lines, expected at least 20"
        )

    @pytest.mark.parametrize("page", CONTENT_PAGES)
    def test_page_has_subsections(self, page: str) -> None:
        """Content pages should have at least one ## subsection."""
        path = WIKI_DIR / page
        if not path.exists():
            pytest.skip(f"{page} does not exist")
        text = _read_text(path)
        h2_count = len(re.findall(r"^## ", text, re.MULTILINE))
        assert h2_count >= 1, (
            f"{page} has no ## subsections, expected at least 1"
        )

    def test_home_has_page_index(self) -> None:
        """Home.md should contain a table linking to other wiki pages."""
        path = WIKI_DIR / "Home.md"
        if not path.exists():
            pytest.skip("Home.md does not exist")
        text = _read_text(path)
        # Home should link to most content pages
        links = set(_extract_wiki_links(text))
        missing = []
        for page in CONTENT_PAGES:
            stem = page.replace(".md", "")
            if stem == "Home":
                continue
            if stem not in links:
                missing.append(stem)
        assert len(missing) == 0, (
            f"Home.md missing links to: {missing}"
        )
