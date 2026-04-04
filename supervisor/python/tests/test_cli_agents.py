"""Unit tests for cli/agents.py — agent discovery and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aquarco_supervisor.cli.agents import (
    KEBAB_CASE_RE,
    SEMVER_RE,
    VALID_CATEGORIES,
    VALID_ROLES,
    ValidationError,
    _build_registry,
    _get_field,
    validate_definition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_md_definition(tmp_path: Path, frontmatter: str, prompt: str = "# Test prompt\n",
                          filename: str = "test-agent.md") -> Path:
    """Write a hybrid .md definition file and return its path."""
    f = tmp_path / filename
    f.write_text(f"---\n{frontmatter}---\n{prompt}")
    return f


def _minimal_valid_frontmatter() -> str:
    """Return minimal valid frontmatter YAML content."""
    return (
        'name: my-agent\n'
        'version: "1.0.0"\n'
        'description: "A description long enough."\n'
        'categories:\n'
        '  - review\n'
    )


# ---------------------------------------------------------------------------
# _get_field
# ---------------------------------------------------------------------------


class TestGetField:
    def test_simple_key(self) -> None:
        assert _get_field({"a": 1}, "a") == 1

    def test_nested_key(self) -> None:
        assert _get_field({"a": {"b": {"c": 42}}}, "a.b.c") == 42

    def test_missing_key_returns_none(self) -> None:
        assert _get_field({"a": 1}, "b") is None

    def test_missing_intermediate_returns_none(self) -> None:
        assert _get_field({"a": 1}, "a.b.c") is None

    def test_non_dict_intermediate_returns_none(self) -> None:
        assert _get_field({"a": "string"}, "a.b") is None

    def test_empty_doc(self) -> None:
        assert _get_field({}, "a.b") is None


# ---------------------------------------------------------------------------
# ValidationError
# ---------------------------------------------------------------------------


class TestValidationError:
    def test_str_representation(self) -> None:
        err = ValidationError("name", "is required")
        assert str(err) == "name: is required"

    def test_fields_stored(self) -> None:
        err = ValidationError("priority", "must be 1-100")
        assert err.field == "priority"
        assert err.message == "must be 1-100"


# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------


class TestKebabCaseRegex:
    @pytest.mark.parametrize("name", ["a", "my-agent", "agent123", "a1b2c3"])
    def test_valid(self, name: str) -> None:
        assert KEBAB_CASE_RE.match(name)

    @pytest.mark.parametrize("name", ["My-Agent", "123agent", "-agent", "agent_name", ""])
    def test_invalid(self, name: str) -> None:
        assert not KEBAB_CASE_RE.match(name)


class TestSemverRegex:
    @pytest.mark.parametrize("v", ["0.0.0", "1.0.0", "10.20.30", "1.0.0-alpha", "2.3.4+build"])
    def test_valid(self, v: str) -> None:
        assert SEMVER_RE.match(v)

    @pytest.mark.parametrize("v", ["1.0", "v1.0.0", "1"])
    def test_invalid(self, v: str) -> None:
        assert not SEMVER_RE.match(v)

    def test_four_part_version_matches_prefix(self) -> None:
        assert SEMVER_RE.match("1.0.0.0")


# ---------------------------------------------------------------------------
# validate_definition — happy path
# ---------------------------------------------------------------------------


class TestValidateDefinitionHappyPath:
    def test_minimal_valid_definition(self, tmp_path: Path) -> None:
        f = _write_md_definition(tmp_path, _minimal_valid_frontmatter())
        errors, record = validate_definition(f)

        assert errors == []
        assert record is not None
        assert record["name"] == "my-agent"
        assert record["version"] == "1.0.0"
        assert record["categories"] == ["review"]
        assert record["priority"] == 50  # default

    def test_explicit_priority_returned(self, tmp_path: Path) -> None:
        fm = _minimal_valid_frontmatter() + "priority: 75\n"
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)

        assert errors == []
        assert record is not None
        assert record["priority"] == 75

    def test_all_valid_categories_accepted(self, tmp_path: Path) -> None:
        cats = "\n".join(f"  - {c}" for c in VALID_CATEGORIES)
        fm = (
            'name: my-agent\n'
            'version: "1.0.0"\n'
            'description: "A description long enough."\n'
            f'categories:\n{cats}\n'
        )
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        assert errors == []

    def test_system_agent_with_role(self, tmp_path: Path) -> None:
        """System agents with role instead of categories should pass."""
        fm = (
            'name: planner-agent\n'
            'version: "1.0.0"\n'
            'description: "Plans pipeline execution"\n'
            'role: planner\n'
        )
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        assert errors == []
        assert record is not None
        assert record["role"] == "planner"
        assert record["categories"] == []


# ---------------------------------------------------------------------------
# validate_definition — frontmatter errors
# ---------------------------------------------------------------------------


class TestValidateDefinitionFrontmatterErrors:
    def test_missing_opening_delimiter(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.md"
        f.write_text("name: test\n---\n# Prompt\n")
        errors, record = validate_definition(f)
        assert len(errors) >= 1
        assert errors[0].field == "(frontmatter)"
        assert record is None

    def test_missing_closing_delimiter(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.md"
        f.write_text("---\nname: test\n# No closing\n")
        errors, record = validate_definition(f)
        assert len(errors) >= 1
        assert record is None

    def test_invalid_yaml_in_frontmatter(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.md"
        f.write_text("---\n{bad: yaml: [unclosed\n---\n# Prompt\n")
        errors, record = validate_definition(f)
        assert len(errors) >= 1
        assert record is None


# ---------------------------------------------------------------------------
# validate_definition — name
# ---------------------------------------------------------------------------


class TestValidateDefinitionName:
    def test_missing_name(self, tmp_path: Path) -> None:
        fm = (
            'version: "1.0.0"\n'
            'description: "A description long enough."\n'
            'categories:\n  - review\n'
        )
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        assert any(e.field == "name" for e in errors)

    @pytest.mark.parametrize("bad_name", ["MyAgent", "123bad", "_under", "with space", "CamelCase"])
    def test_invalid_kebab_case_names(self, tmp_path: Path, bad_name: str) -> None:
        fm = (
            f'name: {bad_name}\n'
            'version: "1.0.0"\n'
            'description: "A description long enough."\n'
            'categories:\n  - review\n'
        )
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        assert any(e.field == "name" for e in errors)

    @pytest.mark.parametrize("good_name", ["a", "my-agent", "agent-v2", "abc123"])
    def test_valid_kebab_case_names(self, tmp_path: Path, good_name: str) -> None:
        fm = (
            f'name: {good_name}\n'
            'version: "1.0.0"\n'
            'description: "A description long enough."\n'
            'categories:\n  - review\n'
        )
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        name_errors = [e for e in errors if e.field == "name"]
        assert name_errors == []


# ---------------------------------------------------------------------------
# validate_definition — version (semver)
# ---------------------------------------------------------------------------


class TestValidateDefinitionVersion:
    def test_missing_version(self, tmp_path: Path) -> None:
        fm = (
            'name: my-agent\n'
            'description: "A description long enough."\n'
            'categories:\n  - review\n'
        )
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        assert any(e.field == "version" for e in errors)

    @pytest.mark.parametrize("bad_ver", ["v1.0.0", "1.0", "1", "not-a-version"])
    def test_invalid_semver(self, tmp_path: Path, bad_ver: str) -> None:
        fm = (
            'name: my-agent\n'
            f'version: "{bad_ver}"\n'
            'description: "A description long enough."\n'
            'categories:\n  - review\n'
        )
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        assert any(e.field == "version" for e in errors)


# ---------------------------------------------------------------------------
# validate_definition — description
# ---------------------------------------------------------------------------


class TestValidateDefinitionDescription:
    def test_missing_description(self, tmp_path: Path) -> None:
        fm = (
            'name: my-agent\n'
            'version: "1.0.0"\n'
            'categories:\n  - review\n'
        )
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        assert any(e.field == "description" for e in errors)

    def test_description_too_short(self, tmp_path: Path) -> None:
        fm = (
            'name: my-agent\n'
            'version: "1.0.0"\n'
            'description: "short"\n'
            'categories:\n  - review\n'
        )
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        assert any(e.field == "description" for e in errors)

    def test_description_exactly_ten_chars_ok(self, tmp_path: Path) -> None:
        fm = (
            'name: my-agent\n'
            'version: "1.0.0"\n'
            'description: "1234567890"\n'
            'categories:\n  - review\n'
        )
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        desc_errors = [e for e in errors if e.field == "description"]
        assert desc_errors == []


# ---------------------------------------------------------------------------
# validate_definition — categories/role
# ---------------------------------------------------------------------------


class TestValidateDefinitionCategories:
    def test_missing_categories_and_role(self, tmp_path: Path) -> None:
        fm = (
            'name: my-agent\n'
            'version: "1.0.0"\n'
            'description: "A description long enough."\n'
        )
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        assert any(e.field == "categories/role" for e in errors)

    def test_invalid_category_value(self, tmp_path: Path) -> None:
        fm = (
            'name: my-agent\n'
            'version: "1.0.0"\n'
            'description: "A description long enough."\n'
            'categories:\n  - invalid-category\n'
        )
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        assert any("categories[" in e.field for e in errors)

    def test_invalid_role_value(self, tmp_path: Path) -> None:
        fm = (
            'name: my-agent\n'
            'version: "1.0.0"\n'
            'description: "A description long enough."\n'
            'role: invalid-role\n'
        )
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        assert any(e.field == "role" for e in errors)


# ---------------------------------------------------------------------------
# validate_definition — prompt body
# ---------------------------------------------------------------------------


class TestValidateDefinitionPromptBody:
    def test_empty_prompt_body(self, tmp_path: Path) -> None:
        f = _write_md_definition(tmp_path, _minimal_valid_frontmatter(), prompt="")
        errors, record = validate_definition(f)
        assert any(e.field == "(prompt)" for e in errors)

    def test_whitespace_only_prompt_body(self, tmp_path: Path) -> None:
        f = _write_md_definition(tmp_path, _minimal_valid_frontmatter(), prompt="   \n  \n")
        errors, record = validate_definition(f)
        assert any(e.field == "(prompt)" for e in errors)


# ---------------------------------------------------------------------------
# validate_definition — priority
# ---------------------------------------------------------------------------


class TestValidateDefinitionPriority:
    def test_priority_below_minimum(self, tmp_path: Path) -> None:
        fm = _minimal_valid_frontmatter() + "priority: 0\n"
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        assert any(e.field == "priority" for e in errors)

    def test_priority_above_maximum(self, tmp_path: Path) -> None:
        fm = _minimal_valid_frontmatter() + "priority: 101\n"
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        assert any(e.field == "priority" for e in errors)

    @pytest.mark.parametrize("prio", [1, 50, 100])
    def test_boundary_priorities_valid(self, tmp_path: Path, prio: int) -> None:
        fm = _minimal_valid_frontmatter() + f"priority: {prio}\n"
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)
        prio_errors = [e for e in errors if e.field == "priority"]
        assert prio_errors == []
        assert record is not None
        assert record["priority"] == prio

    def test_priority_absent_defaults_to_50(self, tmp_path: Path) -> None:
        f = _write_md_definition(tmp_path, _minimal_valid_frontmatter())
        errors, record = validate_definition(f)
        assert errors == []
        assert record is not None
        assert record["priority"] == 50


# ---------------------------------------------------------------------------
# validate_definition — multiple errors accumulated
# ---------------------------------------------------------------------------


class TestValidateDefinitionMultipleErrors:
    def test_multiple_errors_accumulated(self, tmp_path: Path) -> None:
        fm = (
            'name: BadName\n'
            'version: "not-semver"\n'
            'description: "short"\n'
        )
        f = _write_md_definition(tmp_path, fm)
        errors, record = validate_definition(f)

        assert len(errors) >= 3
        assert record is None
        fields = {e.field for e in errors}
        assert "name" in fields
        assert "version" in fields
        assert "description" in fields


# ---------------------------------------------------------------------------
# _build_registry
# ---------------------------------------------------------------------------


class TestBuildRegistry:
    def _make_record(
        self,
        name: str,
        categories: list[str],
        priority: int = 50,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "version": "1.0.0",
            "description": "Test agent",
            "definitionFile": f"{name}.md",
            "categories": categories,
            "priority": priority,
            "resources": {},
            "tools": {},
        }

    def test_schema_version_present(self) -> None:
        reg = _build_registry([])
        assert reg["schemaVersion"] == "1.0.0"

    def test_agent_count_correct(self) -> None:
        records = [self._make_record("a", ["review"]), self._make_record("b", ["test"])]
        reg = _build_registry(records)
        assert reg["agentCount"] == 2

    def test_agents_list_preserved(self) -> None:
        records = [self._make_record("my-agent", ["review"])]
        reg = _build_registry(records)
        assert len(reg["agents"]) == 1
        assert reg["agents"][0]["name"] == "my-agent"

    def test_category_index_built(self) -> None:
        records = [
            self._make_record("agent-a", ["review", "test"]),
            self._make_record("agent-b", ["review"]),
        ]
        reg = _build_registry(records)
        idx = reg["categoryIndex"]
        assert "review" in idx
        assert "test" in idx
        assert set(idx["review"]) == {"agent-a", "agent-b"}
        assert idx["test"] == ["agent-a"]

    def test_category_index_sorted_by_priority(self) -> None:
        records = [
            self._make_record("high-prio", ["review"], priority=10),
            self._make_record("low-prio", ["review"], priority=90),
        ]
        reg = _build_registry(records)
        assert reg["categoryIndex"]["review"] == ["high-prio", "low-prio"]

    def test_empty_registry(self) -> None:
        reg = _build_registry([])
        assert reg["agentCount"] == 0
        assert reg["agents"] == []
        assert reg["categoryIndex"] == {}

    def test_generated_at_is_utc_iso_format(self) -> None:
        import re
        reg = _build_registry([])
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", reg["generatedAt"])
