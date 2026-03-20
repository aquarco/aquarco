"""Unit tests for cli/agents.py — agent discovery and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from aquarco_supervisor.cli.agents import (
    KEBAB_CASE_RE,
    REQUIRED_API_VERSION,
    REQUIRED_KIND,
    SEMVER_RE,
    VALID_CATEGORIES,
    VALID_OUTPUT_FORMATS,
    ValidationError,
    _build_registry,
    _get_field,
    validate_definition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_definition(tmp_path: Path, data: dict[str, Any], filename: str = "test-agent.yaml") -> Path:
    """Write a YAML definition file and return its path."""
    f = tmp_path / filename
    f.write_text(yaml.dump(data))
    return f


def _minimal_valid(prompts_dir: Path, prompt_file: str = "agent.md") -> dict[str, Any]:
    """Return a minimal dict that passes all validation checks."""
    (prompts_dir / prompt_file).write_text("# prompt")
    return {
        "apiVersion": REQUIRED_API_VERSION,
        "kind": REQUIRED_KIND,
        "metadata": {
            "name": "my-agent",
            "version": "1.0.0",
            "description": "A description long enough.",
        },
        "spec": {
            "categories": ["review"],
            "promptFile": prompt_file,
            "output": {"format": "task-file"},
        },
    }


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
        err = ValidationError("metadata.name", "is required")
        assert str(err) == "metadata.name: is required"

    def test_fields_stored(self) -> None:
        err = ValidationError("spec.priority", "must be 1-100")
        assert err.field == "spec.priority"
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
        # SEMVER_RE has no end-anchor, so "1.0.0.0" matches the leading "1.0.0"
        # This documents the intentional (permissive) behaviour of the regex.
        assert SEMVER_RE.match("1.0.0.0")


# ---------------------------------------------------------------------------
# validate_definition — happy path
# ---------------------------------------------------------------------------


class TestValidateDefinitionHappyPath:
    def test_minimal_valid_definition(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)

        assert errors == []
        assert record is not None
        assert record["name"] == "my-agent"
        assert record["version"] == "1.0.0"
        assert record["categories"] == ["review"]
        assert record["outputFormat"] == "task-file"
        assert record["priority"] == 50  # default

    def test_explicit_priority_returned(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["spec"]["priority"] = 75
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)

        assert errors == []
        assert record is not None
        assert record["priority"] == 75

    def test_triggers_and_capabilities_included(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["spec"]["triggers"] = {"produces": ["analysis-done"], "consumes": ["task-created"]}
        data["spec"]["capabilities"] = {"maxConcurrent": 3}
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)

        assert errors == []
        assert record is not None
        assert record["triggers"]["produces"] == ["analysis-done"]
        assert record["triggers"]["consumes"] == ["task-created"]
        assert record["capabilities"] == {"maxConcurrent": 3}

    def test_labels_included_from_metadata(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["metadata"]["labels"] = {"team": "platform"}
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)

        assert errors == []
        assert record is not None
        assert record["labels"] == {"team": "platform"}

    def test_all_valid_categories_accepted(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["spec"]["categories"] = list(VALID_CATEGORIES)
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert errors == []

    def test_all_valid_output_formats_accepted(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        for fmt in VALID_OUTPUT_FORMATS:
            data = _minimal_valid(prompts_dir)
            data["spec"]["output"]["format"] = fmt
            f = _write_definition(defs_dir, data, f"agent-{fmt}.yaml")
            errors, record = validate_definition(f, prompts_dir)
            assert errors == [], f"Expected no errors for format '{fmt}'"


# ---------------------------------------------------------------------------
# validate_definition — YAML errors
# ---------------------------------------------------------------------------


class TestValidateDefinitionYamlErrors:
    def test_invalid_yaml_returns_error(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        f = tmp_path / "bad.yaml"
        f.write_text("{bad: yaml: [unclosed")

        errors, record = validate_definition(f, prompts_dir)

        assert len(errors) >= 1
        assert errors[0].field == "(yaml)"
        assert record is None

    def test_non_mapping_root_returns_error(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        f = tmp_path / "list.yaml"
        f.write_text("- item1\n- item2\n")

        errors, record = validate_definition(f, prompts_dir)

        assert len(errors) == 1
        assert errors[0].field == "(yaml)"
        assert record is None


# ---------------------------------------------------------------------------
# validate_definition — apiVersion / kind
# ---------------------------------------------------------------------------


class TestValidateDefinitionApiVersionKind:
    def test_wrong_api_version(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["apiVersion"] = "wrong/v1"
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)

        assert any(e.field == "apiVersion" for e in errors)
        assert record is None

    def test_missing_api_version(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        del data["apiVersion"]
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "apiVersion" for e in errors)

    def test_wrong_kind(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["kind"] = "SomethingElse"
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "kind" for e in errors)
        assert record is None

    def test_missing_kind(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        del data["kind"]
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "kind" for e in errors)


# ---------------------------------------------------------------------------
# validate_definition — metadata.name
# ---------------------------------------------------------------------------


class TestValidateDefinitionName:
    def test_missing_name(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        del data["metadata"]["name"]
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "metadata.name" for e in errors)

    @pytest.mark.parametrize("bad_name", ["MyAgent", "123bad", "_under", "with space", "CamelCase"])
    def test_invalid_kebab_case_names(self, tmp_path: Path, bad_name: str) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["metadata"]["name"] = bad_name
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "metadata.name" for e in errors)

    @pytest.mark.parametrize("good_name", ["a", "my-agent", "agent-v2", "abc123"])
    def test_valid_kebab_case_names(self, tmp_path: Path, good_name: str) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["metadata"]["name"] = good_name
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        name_errors = [e for e in errors if e.field == "metadata.name"]
        assert name_errors == []


# ---------------------------------------------------------------------------
# validate_definition — metadata.version (semver)
# ---------------------------------------------------------------------------


class TestValidateDefinitionVersion:
    def test_missing_version(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        del data["metadata"]["version"]
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "metadata.version" for e in errors)

    @pytest.mark.parametrize("bad_ver", ["v1.0.0", "1.0", "1", "not-a-version"])
    def test_invalid_semver(self, tmp_path: Path, bad_ver: str) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["metadata"]["version"] = bad_ver
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "metadata.version" for e in errors)

    @pytest.mark.parametrize("good_ver", ["0.0.0", "1.0.0", "10.20.30"])
    def test_valid_semver(self, tmp_path: Path, good_ver: str) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["metadata"]["version"] = good_ver
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        ver_errors = [e for e in errors if e.field == "metadata.version"]
        assert ver_errors == []


# ---------------------------------------------------------------------------
# validate_definition — metadata.description
# ---------------------------------------------------------------------------


class TestValidateDefinitionDescription:
    def test_missing_description(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        del data["metadata"]["description"]
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "metadata.description" for e in errors)

    def test_description_too_short(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["metadata"]["description"] = "short"  # < 10 chars
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "metadata.description" for e in errors)

    def test_description_exactly_ten_chars_ok(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["metadata"]["description"] = "1234567890"  # exactly 10 chars
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        desc_errors = [e for e in errors if e.field == "metadata.description"]
        assert desc_errors == []


# ---------------------------------------------------------------------------
# validate_definition — spec.categories
# ---------------------------------------------------------------------------


class TestValidateDefinitionCategories:
    def test_missing_categories(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        del data["spec"]["categories"]
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "spec.categories" for e in errors)

    def test_empty_categories_list(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["spec"]["categories"] = []
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "spec.categories" for e in errors)

    def test_invalid_category_value(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["spec"]["categories"] = ["invalid-category"]
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any("spec.categories[" in e.field for e in errors)

    def test_mixed_valid_and_invalid_categories(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["spec"]["categories"] = ["review", "BOGUS"]
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        cat_errors = [e for e in errors if "spec.categories[" in e.field]
        assert len(cat_errors) == 1
        assert "BOGUS" in cat_errors[0].message


# ---------------------------------------------------------------------------
# validate_definition — spec.promptFile
# ---------------------------------------------------------------------------


class TestValidateDefinitionPromptFile:
    def test_missing_prompt_file_field(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        del data["spec"]["promptFile"]
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "spec.promptFile" for e in errors)

    def test_prompt_file_not_on_disk(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["spec"]["promptFile"] = "nonexistent.md"
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        prompt_errors = [e for e in errors if e.field == "spec.promptFile"]
        assert any("not found" in e.message for e in prompt_errors)

    def test_prompt_file_exists_passes(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()
        (prompts_dir / "my-prompt.md").write_text("# hello")

        data = _minimal_valid(prompts_dir)
        data["spec"]["promptFile"] = "my-prompt.md"
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        prompt_errors = [e for e in errors if e.field == "spec.promptFile"]
        assert prompt_errors == []


# ---------------------------------------------------------------------------
# validate_definition — spec.output.format
# ---------------------------------------------------------------------------


class TestValidateDefinitionOutputFormat:
    def test_missing_output_section(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        del data["spec"]["output"]
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "spec.output.format" for e in errors)

    def test_invalid_output_format(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["spec"]["output"]["format"] = "telegram-message"
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "spec.output.format" for e in errors)


# ---------------------------------------------------------------------------
# validate_definition — spec.priority
# ---------------------------------------------------------------------------


class TestValidateDefinitionPriority:
    def test_priority_below_minimum(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["spec"]["priority"] = 0
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "spec.priority" for e in errors)

    def test_priority_above_maximum(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["spec"]["priority"] = 101
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "spec.priority" for e in errors)

    def test_priority_non_integer(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["spec"]["priority"] = "high"
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert any(e.field == "spec.priority" for e in errors)

    @pytest.mark.parametrize("prio", [1, 50, 100])
    def test_boundary_priorities_valid(self, tmp_path: Path, prio: int) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        data["spec"]["priority"] = prio
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        prio_errors = [e for e in errors if e.field == "spec.priority"]
        assert prio_errors == []
        assert record is not None
        assert record["priority"] == prio

    def test_priority_absent_defaults_to_50(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        data = _minimal_valid(prompts_dir)
        # no priority key in spec
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)
        assert errors == []
        assert record is not None
        assert record["priority"] == 50


# ---------------------------------------------------------------------------
# validate_definition — multiple errors accumulated
# ---------------------------------------------------------------------------


class TestValidateDefinitionMultipleErrors:
    def test_multiple_errors_accumulated(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        defs_dir = tmp_path / "defs"
        defs_dir.mkdir()

        # Deliberately broken on several fields
        data: dict[str, Any] = {
            "apiVersion": "wrong/v1",
            "kind": "WrongKind",
            "metadata": {
                "name": "BadName",  # not kebab-case
                "version": "not-semver",
                "description": "short",  # too short
            },
            "spec": {
                "categories": [],
                "output": {"format": "invalid"},
            },
        }
        f = _write_definition(defs_dir, data)

        errors, record = validate_definition(f, prompts_dir)

        assert len(errors) >= 5
        assert record is None
        fields = {e.field for e in errors}
        assert "apiVersion" in fields
        assert "kind" in fields
        assert "metadata.name" in fields
        assert "metadata.version" in fields


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
            "promptFile": "agent.md",
            "definitionFile": f"{name}.yaml",
            "categories": categories,
            "priority": priority,
            "outputFormat": "task-file",
            "triggers": {"produces": [], "consumes": []},
            "capabilities": {},
            "resources": {},
            "labels": {},
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
