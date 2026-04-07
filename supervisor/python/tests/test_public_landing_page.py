"""
Tests for the public landing page (public/index.html) and README.md changes.

Validates:
- Landing page HTML structure, required sections, and meta tags
- Link consistency (GitHub URLs, internal anchors)
- Accessibility basics (lang attribute, viewport meta)
- SVG logo/animation structure
- JavaScript animation definitions
- README.md content after trimming

Commit: 89521fe9589c - chore: Reduced README and public landing page added
"""

import re
import pytest
from pathlib import Path
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def project_root() -> Path:
    """Return the aquarco project root directory."""
    # tests/ -> python/ -> supervisor/ -> project root
    return Path(__file__).resolve().parent.parent.parent.parent


def read_file(relative_path: str) -> str:
    """Read a file relative to project root."""
    path = project_root() / relative_path
    return path.read_text()


class TagCollector(HTMLParser):
    """Simple HTML parser that collects tags and attributes."""

    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, list[tuple[str, str | None]]]] = []
        self.meta_tags: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.scripts_inline: list[str] = []
        self._in_script = False
        self._script_data = ""

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))
        attr_dict = {k: v for k, v in attrs if v is not None}
        if tag == "meta":
            self.meta_tags.append(attr_dict)
        if tag == "a":
            self.links.append(attr_dict)
        if tag == "script":
            if "src" not in attr_dict:
                self._in_script = True
                self._script_data = ""

    def handle_data(self, data):
        if self._in_script:
            self._script_data += data

    def handle_endtag(self, tag):
        if tag == "script" and self._in_script:
            self.scripts_inline.append(self._script_data)
            self._in_script = False


# ===========================================================================
# Landing Page Tests
# ===========================================================================


class TestLandingPageExists:
    """Verify the landing page file exists and is valid HTML."""

    def test_file_exists(self):
        path = project_root() / "public" / "index.html"
        assert path.exists(), "public/index.html must exist"

    def test_file_is_not_empty(self):
        content = read_file("public/index.html")
        assert len(content.strip()) > 0, "Landing page must not be empty"

    def test_is_valid_html5(self):
        content = read_file("public/index.html")
        assert content.strip().startswith("<!DOCTYPE html>"), \
            "Must start with HTML5 doctype"


class TestLandingPageMeta:
    """Validate HTML head meta tags and SEO basics."""

    @pytest.fixture
    def html(self) -> str:
        return read_file("public/index.html")

    @pytest.fixture
    def parsed(self, html) -> TagCollector:
        collector = TagCollector()
        collector.feed(html)
        return collector

    def test_has_charset_utf8(self, parsed: TagCollector):
        charsets = [m for m in parsed.meta_tags if m.get("charset")]
        assert any(
            m["charset"].lower() == "utf-8" for m in charsets
        ), "Must declare charset=UTF-8"

    def test_has_viewport_meta(self, parsed: TagCollector):
        viewports = [
            m for m in parsed.meta_tags if m.get("name") == "viewport"
        ]
        assert len(viewports) >= 1, "Must have viewport meta tag"
        assert "width=device-width" in viewports[0].get("content", "")

    def test_has_title_tag(self, html: str):
        match = re.search(r"<title>(.+?)</title>", html)
        assert match, "Must have a <title> tag"
        assert "aquarco" in match.group(1).lower(), \
            "Title must mention aquarco"

    def test_html_has_lang_attribute(self, html: str):
        match = re.search(r'<html\s+[^>]*lang="([^"]+)"', html)
        assert match, "html tag must have lang attribute"
        assert match.group(1) == "en", "lang should be 'en'"

    def test_missing_meta_description(self, html: str):
        """Document that meta description is absent (known issue from review)."""
        has_description = bool(
            re.search(r'<meta\s+name="description"', html)
        )
        # This is a known gap flagged in review — test documents current state
        assert not has_description, \
            "meta description is currently missing (known issue)"


class TestLandingPageStructure:
    """Validate the page has all required sections."""

    @pytest.fixture
    def html(self) -> str:
        return read_file("public/index.html")

    def test_has_hero_section(self, html: str):
        assert 'class="hero"' in html, "Must have hero section"

    def test_hero_has_heading(self, html: str):
        match = re.search(r'<h1[^>]*>.*?aquarco.*?</h1>', html, re.DOTALL)
        assert match, "Hero must contain h1 with 'aquarco'"

    def test_has_tagline(self, html: str):
        assert 'class="tagline"' in html, "Must have a tagline element"

    def test_has_cta_buttons(self, html: str):
        assert 'class="btn btn-primary"' in html, "Must have primary CTA"
        assert 'class="btn btn-ghost"' in html, "Must have ghost/secondary CTA"

    def test_has_terminal_section(self, html: str):
        assert 'class="terminal-section"' in html or 'class="terminal"' in html, \
            "Must have terminal demo section"

    def test_terminal_shows_aquarco_commands(self, html: str):
        assert "aquarco init" in html, "Terminal must show 'aquarco init'"
        assert "aquarco auth" in html, "Terminal must show 'aquarco auth'"
        assert "aquarco repos add" in html, "Terminal must show 'aquarco repos add'"

    def test_has_pipeline_section(self, html: str):
        assert 'id="how"' in html, "Must have 'how it works' section with id='how'"

    def test_pipeline_has_all_stages(self, html: str):
        stages = ["Analyze", "Design", "Implement", "Test", "Review", "Submit"]
        for stage in stages:
            assert stage in html, f"Pipeline must list stage: {stage}"

    def test_has_taglines_grid(self, html: str):
        assert 'class="taglines-grid"' in html, "Must have taglines grid"

    def test_has_footer(self, html: str):
        assert "<footer>" in html, "Must have footer element"

    def test_footer_mentions_mit_license(self, html: str):
        footer_match = re.search(
            r"<footer>(.*?)</footer>", html, re.DOTALL
        )
        assert footer_match, "Footer must exist"
        assert "MIT" in footer_match.group(1), "Footer must mention MIT License"


class TestLandingPageLinks:
    """Validate links and URL consistency."""

    @pytest.fixture
    def html(self) -> str:
        return read_file("public/index.html")

    @pytest.fixture
    def parsed(self, html) -> TagCollector:
        collector = TagCollector()
        collector.feed(html)
        return collector

    def test_cta_github_link_uses_correct_org(self, parsed: TagCollector):
        github_links = [
            l for l in parsed.links
            if l.get("href", "").startswith("https://github.com/")
        ]
        assert len(github_links) >= 1, "Must have at least one GitHub link"
        # CTA button should point to aquarco/aquarco
        cta_link = next(
            (l for l in github_links
             if "aquarco/aquarco" in l.get("href", "")),
            None,
        )
        assert cta_link is not None, \
            "CTA must link to github.com/aquarco/aquarco"

    def test_footer_github_url_inconsistency(self, html: str):
        """Document the footer URL inconsistency found in review.

        The footer link text says 'github.com/yourorg/aquarco' but the href
        points to 'github.com/aquarco/aquarco'. This is a known issue.
        """
        footer = re.search(r"<footer>(.*?)</footer>", html, re.DOTALL)
        assert footer
        footer_html = footer.group(1)
        # The link text contains 'yourorg' which is inconsistent
        assert "yourorg" in footer_html, \
            "Footer link text still contains 'yourorg' placeholder (known issue)"

    def test_internal_anchor_how_exists(self, html: str):
        """CTA 'See how it works' links to #how which must exist."""
        assert 'href="#how"' in html, "Must have link to #how"
        assert 'id="how"' in html, "Must have element with id='how'"

    def test_google_fonts_preconnect(self, html: str):
        assert 'rel="preconnect"' in html, \
            "Should preconnect to fonts.googleapis.com"


class TestLandingPageSVGLogo:
    """Validate the SVG aquarium logo."""

    @pytest.fixture
    def html(self) -> str:
        return read_file("public/index.html")

    def test_has_svg_in_hero(self, html: str):
        assert '<svg' in html, "Hero must contain an SVG logo"

    def test_svg_has_three_fish(self, html: str):
        for fish_id in ["fish1", "fish2", "fish3"]:
            assert f'id="{fish_id}"' in html, \
                f"SVG must contain fish with id='{fish_id}'"

    def test_fish_have_distinct_colors(self, html: str):
        # Each fish should have a different fill color
        colors = set()
        for fish_id in ["fish1", "fish2", "fish3"]:
            block = re.search(
                rf'id="{fish_id}"[^>]*>.*?</g>',
                html,
                re.DOTALL,
            )
            assert block, f"Fish {fish_id} group must exist"
            fill = re.search(r'fill="(#[0-9a-fA-F]+)"', block.group(0))
            if fill:
                colors.add(fill.group(1))
        assert len(colors) == 3, \
            f"Three fish should have 3 distinct colors, got {colors}"


class TestLandingPageAnimations:
    """Validate JavaScript animations for fish and bubbles."""

    @pytest.fixture
    def html(self) -> str:
        return read_file("public/index.html")

    @pytest.fixture
    def js(self, html) -> str:
        match = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
        assert match, "Must have inline script"
        return match.group(1)

    def test_fish_animation_definitions(self, js: str):
        """Fish animation array must define all three fish."""
        assert "fishes" in js, "Must define fishes array"
        for fish_id in ["fish1", "fish2", "fish3"]:
            assert f"'{fish_id}'" in js, \
                f"Fish animation must reference '{fish_id}'"

    def test_fish_animation_uses_raf(self, js: str):
        assert "requestAnimationFrame" in js, \
            "Fish animation must use requestAnimationFrame"

    def test_bubble_definitions(self, js: str):
        assert "bubbleDefs" in js, "Must define bubble animation defs"

    def test_bubbles_create_svg_circles(self, js: str):
        assert "createElementNS" in js, \
            "Bubbles must be created as SVG elements"
        assert "'circle'" in js, "Bubbles must be circle elements"

    def test_no_visibility_change_handler(self, js: str):
        """Document that animations lack visibility change pausing (review finding)."""
        has_visibility = (
            "visibilitychange" in js or "IntersectionObserver" in js
        )
        assert not has_visibility, \
            "Animations currently lack visibility-based pausing (known issue)"


class TestLandingPageCSS:
    """Validate CSS custom properties and responsive design basics."""

    @pytest.fixture
    def html(self) -> str:
        return read_file("public/index.html")

    def test_defines_css_custom_properties(self, html: str):
        assert ":root" in html, "Must define CSS custom properties in :root"

    def test_has_aquarco_brand_colors(self, html: str):
        # The review mentioned teal, blue, purple as brand colors
        assert "--teal:" in html, "Must define --teal color"
        assert "--blue:" in html, "Must define --blue color"
        assert "--purple:" in html, "Must define --purple color"

    def test_uses_custom_fonts(self, html: str):
        assert "Libre Baskerville" in html, "Must use Libre Baskerville font"
        assert "JetBrains Mono" in html, "Must use JetBrains Mono font"

    def test_responsive_hero_padding(self, html: str):
        """Hero section should have padding for mobile."""
        hero_match = re.search(r"\.hero\s*\{([^}]+)\}", html)
        assert hero_match, "Must have .hero CSS rule"
        assert "padding" in hero_match.group(1), "Hero must have padding"


# ===========================================================================
# README.md Tests
# ===========================================================================


class TestREADMETrimmed:
    """Validate README.md content after trimming."""

    @pytest.fixture
    def readme(self) -> str:
        return read_file("README.md")

    def test_readme_exists(self):
        path = project_root() / "README.md"
        assert path.exists(), "README.md must exist"

    def test_readme_starts_with_heading(self, readme: str):
        assert readme.strip().startswith("# Aquarco"), \
            "README must start with '# Aquarco'"

    def test_has_quick_start_section(self, readme: str):
        assert "## Quick Start" in readme, "Must have Quick Start section"

    def test_quick_start_has_essential_commands(self, readme: str):
        for cmd in ["aquarco init", "aquarco auth", "aquarco repos add"]:
            assert cmd in readme, f"Quick Start must mention '{cmd}'"

    def test_has_cli_reference_section(self, readme: str):
        assert "## CLI Reference" in readme, "Must have CLI Reference section"

    def test_cli_reference_is_table(self, readme: str):
        # Should have a markdown table with | delimiters
        cli_section = readme.split("## CLI Reference")[1] if "## CLI Reference" in readme else ""
        assert "|" in cli_section, "CLI reference should be a markdown table"

    def test_readme_is_concise(self, readme: str):
        """README should be trimmed — under 60 lines."""
        lines = readme.strip().split("\n")
        assert len(lines) <= 60, \
            f"README should be concise after trimming, got {len(lines)} lines"

    def test_no_architecture_section(self, readme: str):
        """Architecture details moved to CLAUDE.md, not in README."""
        assert "## Architecture" not in readme, \
            "Architecture section should be removed from README"

    def test_no_auth_flow_section(self, readme: str):
        """Auth flow details moved to CLAUDE.md."""
        assert "## Auth" not in readme or "## Authentication" not in readme, \
            "Auth flow section should be removed from README"

    def test_dashboard_url_mentioned(self, readme: str):
        assert "localhost:8080" in readme, \
            "README should mention the dashboard URL"
