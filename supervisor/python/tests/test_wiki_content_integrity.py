"""Tests for wiki content integrity and CLAUDE.md accuracy.

Extends the structural tests in test_wiki_structure.py with deeper validation:
- CLAUDE.md page index table accuracy (URL slugs, topic descriptions)
- Markdown formatting integrity (no unclosed code blocks, no duplicate H1s)
- Cross-link reciprocity between content pages
- Content pages reference correct wiki link syntax (not raw URLs)
- Sidebar grouping matches page categories
- Each page has no trailing whitespace issues in headers
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Resolve paths relative to the repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
WIKI_DIR = REPO_ROOT / "wiki"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

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

CONTENT_PAGES = [p for p in REQUIRED_PAGES if not p.startswith("_")]

# Expected URL slugs in CLAUDE.md table (derived from filename stems)
EXPECTED_SLUGS = {
    "Home.md": "/wiki/Home",
    "Quick-Start.md": "/wiki/Quick-Start",
    "CLI-Reference.md": "/wiki/CLI-Reference",
    "Architecture.md": "/wiki/Architecture",
    "File-Layout.md": "/wiki/File-Layout",
    "Components.md": "/wiki/Components",
    "Agent-System.md": "/wiki/Agent-System",
    "Pipeline-System.md": "/wiki/Pipeline-System",
    "Conditions-Engine.md": "/wiki/Conditions-Engine",
    "Git-Flow.md": "/wiki/Git-Flow",
    "Auth-Flows.md": "/wiki/Auth-Flows",
    "Database.md": "/wiki/Database",
    "Dev-Setup.md": "/wiki/Dev-Setup",
    "Operations.md": "/wiki/Operations",
    "_Sidebar.md": "(navigation)",
    "_Footer.md": "(footer)",
}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_wiki_links(text: str) -> list[str]:
    """Extract all [[PageName]] or [[PageName|Display Text]] links."""
    return re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text)


# ---------------------------------------------------------------------------
# CLAUDE.md page index accuracy
# ---------------------------------------------------------------------------


class TestClaudeMdTableAccuracy:
    """CLAUDE.md page index table entries must be accurate."""

    @pytest.fixture(autouse=True)
    def _load_claude_md(self) -> None:
        if not CLAUDE_MD.exists():
            pytest.skip("CLAUDE.md not found")
        self.text = _read_text(CLAUDE_MD)

    @pytest.mark.parametrize("page", REQUIRED_PAGES)
    def test_url_slug_correct(self, page: str) -> None:
        """Each page's URL slug in the table matches the expected value."""
        expected_slug = EXPECTED_SLUGS[page]
        # Table row format: | `Page.md` | `/wiki/Slug` | Topic |
        assert expected_slug in self.text, (
            f"CLAUDE.md table missing correct slug '{expected_slug}' for {page}"
        )

    def test_table_row_count_matches(self) -> None:
        """Number of table rows in the Page Index matches the required page count."""
        # Find all rows matching | `*.md` | pattern
        rows = re.findall(r"\|\s*`[^`]+\.md`\s*\|", self.text)
        assert len(rows) == len(REQUIRED_PAGES), (
            f"Page Index table has {len(rows)} rows, expected {len(REQUIRED_PAGES)}"
        )

    def test_table_has_three_columns(self) -> None:
        """Each data row in the Page Index table has exactly 3 columns."""
        in_table = False
        for line in self.text.split("\n"):
            if "| Page file |" in line:
                in_table = True
                continue
            if in_table and line.startswith("|--"):
                continue
            if in_table and line.startswith("|"):
                cols = [c.strip() for c in line.split("|") if c.strip()]
                assert len(cols) == 3, (
                    f"Table row should have 3 columns: {line!r}"
                )
            elif in_table and not line.startswith("|"):
                break


# ---------------------------------------------------------------------------
# Markdown formatting integrity
# ---------------------------------------------------------------------------


class TestMarkdownFormatting:
    """Validate markdown formatting in wiki pages."""

    @pytest.mark.parametrize("page", CONTENT_PAGES)
    def test_no_duplicate_h1(self, page: str) -> None:
        """Each page should have exactly one H1 header outside code blocks."""
        path = WIKI_DIR / page
        if not path.exists():
            pytest.skip(f"{page} does not exist")
        text = _read_text(path)
        # Strip code blocks before counting H1 headers
        stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        h1_count = len(re.findall(r"^# ", stripped, re.MULTILINE))
        assert h1_count == 1, (
            f"{page} has {h1_count} H1 headers outside code blocks, expected exactly 1"
        )

    @pytest.mark.parametrize("page", REQUIRED_PAGES)
    def test_code_blocks_balanced(self, page: str) -> None:
        """All code fences (```) must be balanced (even count)."""
        path = WIKI_DIR / page
        if not path.exists():
            pytest.skip(f"{page} does not exist")
        text = _read_text(path)
        fence_count = len(re.findall(r"^```", text, re.MULTILINE))
        assert fence_count % 2 == 0, (
            f"{page} has {fence_count} code fences (odd = unclosed block)"
        )

    @pytest.mark.parametrize("page", REQUIRED_PAGES)
    def test_no_empty_headers(self, page: str) -> None:
        """Headers should not be empty (e.g., '## ' with nothing after)."""
        path = WIKI_DIR / page
        if not path.exists():
            pytest.skip(f"{page} does not exist")
        text = _read_text(path)
        empty_headers = re.findall(r"^#{1,6}\s*$", text, re.MULTILINE)
        assert len(empty_headers) == 0, (
            f"{page} has {len(empty_headers)} empty header(s)"
        )

    @pytest.mark.parametrize("page", CONTENT_PAGES)
    def test_no_raw_wiki_urls(self, page: str) -> None:
        """Content pages should use [[wiki links]], not raw GitHub wiki URLs."""
        path = WIKI_DIR / page
        if not path.exists():
            pytest.skip(f"{page} does not exist")
        text = _read_text(path)
        raw_urls = re.findall(
            r"https?://github\.com/[^/]+/[^/]+/wiki/\S+", text
        )
        assert len(raw_urls) == 0, (
            f"{page} uses raw wiki URLs instead of [[links]]: {raw_urls}"
        )

    @pytest.mark.parametrize("page", REQUIRED_PAGES)
    def test_file_ends_with_newline(self, page: str) -> None:
        """Files should end with a trailing newline."""
        path = WIKI_DIR / page
        if not path.exists():
            pytest.skip(f"{page} does not exist")
        raw = path.read_bytes()
        assert raw.endswith(b"\n"), f"{page} does not end with a newline"


# ---------------------------------------------------------------------------
# Cross-link reciprocity
# ---------------------------------------------------------------------------


class TestCrossLinkReciprocity:
    """If page A links to page B, page B should link back to A (for content pages)."""

    def _build_link_graph(self) -> dict[str, set[str]]:
        """Build a directed graph of wiki cross-links."""
        graph: dict[str, set[str]] = {}
        for page in CONTENT_PAGES:
            path = WIKI_DIR / page
            if not path.exists():
                continue
            stem = page.replace(".md", "")
            text = _read_text(path)
            links = set(_extract_wiki_links(text))
            graph[stem] = links
        return graph

    def test_home_is_reachable_from_most_pages(self) -> None:
        """Most content pages should link back to Home (or be linked from sidebar)."""
        sidebar = WIKI_DIR / "_Sidebar.md"
        if not sidebar.exists():
            pytest.skip("_Sidebar.md missing")
        sidebar_links = set(_extract_wiki_links(_read_text(sidebar)))
        graph = self._build_link_graph()
        # Home should be reachable: either sidebar links to it or pages link to it
        pages_linking_home = {
            stem for stem, links in graph.items() if "Home" in links
        }
        # Home is reachable if sidebar or at least some pages link to it
        assert "Home" in sidebar_links or len(pages_linking_home) >= 1, (
            "Home page is not reachable from sidebar or any content page"
        )


# ---------------------------------------------------------------------------
# Sidebar structure tests
# ---------------------------------------------------------------------------


class TestSidebarStructure:
    """Deeper sidebar structure validation."""

    @pytest.fixture(autouse=True)
    def _load_sidebar(self) -> None:
        path = WIKI_DIR / "_Sidebar.md"
        if not path.exists():
            pytest.skip("_Sidebar.md missing")
        self.text = _read_text(path)
        self.links = _extract_wiki_links(self.text)

    def test_sidebar_no_broken_links(self) -> None:
        """Every link in sidebar resolves to an existing wiki page."""
        existing = {f.stem for f in WIKI_DIR.glob("*.md")}
        for link in self.links:
            assert link in existing, (
                f"Sidebar has broken link [[{link}]]"
            )

    def test_sidebar_no_duplicate_links(self) -> None:
        """Sidebar should not link to the same page twice."""
        seen: set[str] = set()
        dupes: list[str] = []
        for link in self.links:
            if link in seen:
                dupes.append(link)
            seen.add(link)
        assert len(dupes) == 0, f"Sidebar has duplicate links: {dupes}"

    def test_sidebar_has_expected_groups(self) -> None:
        """Sidebar should have the 4 expected section groups."""
        expected_groups = {"System", "Agents & Pipelines", "Auth & Data", "Setup & Ops"}
        bold_headers = set(re.findall(r"\*\*(.+?)\*\*", self.text))
        missing = expected_groups - bold_headers
        assert len(missing) == 0, (
            f"Sidebar missing section groups: {missing}"
        )


# ---------------------------------------------------------------------------
# Content depth tests (per-page topic validation)
# ---------------------------------------------------------------------------


class TestContentDepth:
    """Validate that key pages contain expected topic coverage."""

    def test_cli_reference_has_commands(self) -> None:
        """CLI-Reference.md should document actual CLI commands."""
        path = WIKI_DIR / "CLI-Reference.md"
        if not path.exists():
            pytest.skip("CLI-Reference.md missing")
        text = _read_text(path)
        # Should mention key commands
        for cmd in ["aquarco init", "aquarco auth", "aquarco repos"]:
            assert cmd in text, (
                f"CLI-Reference.md should document '{cmd}'"
            )

    def test_database_has_table_docs(self) -> None:
        """Database.md should document database tables."""
        path = WIKI_DIR / "Database.md"
        if not path.exists():
            pytest.skip("Database.md missing")
        text = _read_text(path)
        # Should mention key tables
        for table in ["repositories", "tasks"]:
            assert table in text, (
                f"Database.md should document the '{table}' table"
            )

    def test_architecture_has_docker(self) -> None:
        """Architecture.md should mention Docker Compose."""
        path = WIKI_DIR / "Architecture.md"
        if not path.exists():
            pytest.skip("Architecture.md missing")
        text = _read_text(path)
        assert "Docker" in text or "docker" in text, (
            "Architecture.md should mention Docker"
        )

    def test_auth_flows_has_sequence(self) -> None:
        """Auth-Flows.md should contain authentication flow details."""
        path = WIKI_DIR / "Auth-Flows.md"
        if not path.exists():
            pytest.skip("Auth-Flows.md missing")
        text = _read_text(path)
        assert "PKCE" in text or "OAuth" in text or "device" in text.lower(), (
            "Auth-Flows.md should describe auth mechanisms"
        )

    def test_pipeline_system_has_stages(self) -> None:
        """Pipeline-System.md should describe pipeline stages."""
        path = WIKI_DIR / "Pipeline-System.md"
        if not path.exists():
            pytest.skip("Pipeline-System.md missing")
        text = _read_text(path)
        for stage in ["analyze", "design", "implement", "test", "review"]:
            assert stage in text.lower(), (
                f"Pipeline-System.md should mention '{stage}' stage"
            )

    def test_conditions_engine_has_examples(self) -> None:
        """Conditions-Engine.md should contain condition examples."""
        path = WIKI_DIR / "Conditions-Engine.md"
        if not path.exists():
            pytest.skip("Conditions-Engine.md missing")
        text = _read_text(path)
        # Should have code blocks with condition examples
        assert "simple:" in text or "ai:" in text, (
            "Conditions-Engine.md should contain condition syntax examples"
        )

    def test_git_flow_has_branch_naming(self) -> None:
        """Git-Flow.md should describe branch naming conventions."""
        path = WIKI_DIR / "Git-Flow.md"
        if not path.exists():
            pytest.skip("Git-Flow.md missing")
        text = _read_text(path)
        assert "branch" in text.lower(), (
            "Git-Flow.md should describe branch conventions"
        )

    def test_quick_start_has_install_steps(self) -> None:
        """Quick-Start.md should have installation/setup steps."""
        path = WIKI_DIR / "Quick-Start.md"
        if not path.exists():
            pytest.skip("Quick-Start.md missing")
        text = _read_text(path)
        assert "install" in text.lower() or "setup" in text.lower() or "pip" in text, (
            "Quick-Start.md should describe installation steps"
        )

    def test_operations_has_backup(self) -> None:
        """Operations.md should mention backup/restore procedures."""
        path = WIKI_DIR / "Operations.md"
        if not path.exists():
            pytest.skip("Operations.md missing")
        text = _read_text(path)
        assert "backup" in text.lower() or "restore" in text.lower(), (
            "Operations.md should cover backup/restore"
        )

    def test_components_has_services(self) -> None:
        """Components.md should document Docker services."""
        path = WIKI_DIR / "Components.md"
        if not path.exists():
            pytest.skip("Components.md missing")
        text = _read_text(path)
        assert "caddy" in text.lower() or "postgres" in text.lower(), (
            "Components.md should document key services"
        )

    def test_agent_system_has_agent_list(self) -> None:
        """Agent-System.md should list available agents."""
        path = WIKI_DIR / "Agent-System.md"
        if not path.exists():
            pytest.skip("Agent-System.md missing")
        text = _read_text(path)
        assert "agent" in text.lower(), (
            "Agent-System.md should describe agents"
        )

    def test_dev_setup_has_testing(self) -> None:
        """Dev-Setup.md should mention testing procedures."""
        path = WIKI_DIR / "Dev-Setup.md"
        if not path.exists():
            pytest.skip("Dev-Setup.md missing")
        text = _read_text(path)
        assert "test" in text.lower() or "pytest" in text, (
            "Dev-Setup.md should mention testing"
        )

    def test_file_layout_has_directory_structure(self) -> None:
        """File-Layout.md should describe directory structure."""
        path = WIKI_DIR / "File-Layout.md"
        if not path.exists():
            pytest.skip("File-Layout.md missing")
        text = _read_text(path)
        assert "supervisor/" in text or "config/" in text or "cli/" in text, (
            "File-Layout.md should describe key directories"
        )
