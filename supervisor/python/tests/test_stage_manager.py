"""Tests for stage_manager — extracted from task_queue.

Focuses on the pure function _resolve_stage_status and backward-compat
delegation from task_queue via __getattr__.
"""

from __future__ import annotations

import json

import pytest

from aquarco_supervisor.stage_manager import _resolve_stage_status


# -----------------------------------------------------------------------
# _resolve_stage_status  (pure function, no DB needed)
# -----------------------------------------------------------------------


class TestResolveStageStatus:
    def test_success_no_error(self):
        output = {"_subtype": "success", "_is_error": False}
        status, error_msg = _resolve_stage_status(output, None)
        assert status == "completed"
        assert error_msg is None

    def test_error_max_turns(self):
        output = {"_subtype": "error_max_turns", "_is_error": True}
        status, error_msg = _resolve_stage_status(output, None)
        assert status == "max_turns"
        assert "max_turns" in error_msg

    def test_success_is_error_with_rate_limit(self):
        rate_limit_line = json.dumps({
            "type": "rate_limit_event",
            "rate_limit_info": {"resetsAt": "2026-04-08T12:00:00Z"},
        })
        output = {"_subtype": "success", "_is_error": True}
        status, error_msg = _resolve_stage_status(output, rate_limit_line)
        assert status == "rate_limited"
        assert "resetsAt=2026-04-08T12:00:00Z" in error_msg

    def test_success_is_error_no_rate_limit(self):
        raw = json.dumps({"type": "assistant", "message": {}})
        output = {"_subtype": "success", "_is_error": True}
        status, error_msg = _resolve_stage_status(output, raw)
        assert status == "failed"
        assert "is_error=true" in error_msg

    def test_success_is_error_no_raw_output(self):
        output = {"_subtype": "success", "_is_error": True}
        status, error_msg = _resolve_stage_status(output, None)
        assert status == "failed"

    def test_unexpected_subtype(self):
        output = {"_subtype": "something_else"}
        status, error_msg = _resolve_stage_status(output, None)
        assert status == "failed"
        assert "something_else" in error_msg

    def test_no_subtype(self):
        output = {}
        status, error_msg = _resolve_stage_status(output, None)
        assert status == "failed"
        assert "None" in error_msg

    def test_rate_limit_with_invalid_json_lines(self):
        raw = "not json\n{invalid too\n"
        output = {"_subtype": "success", "_is_error": True}
        status, error_msg = _resolve_stage_status(output, raw)
        assert status == "failed"

    def test_rate_limit_with_empty_lines(self):
        raw = "\n\n\n"
        output = {"_subtype": "success", "_is_error": True}
        status, error_msg = _resolve_stage_status(output, raw)
        assert status == "failed"


# -----------------------------------------------------------------------
# Backward-compat: task_queue.__getattr__ delegates to StageManager
# -----------------------------------------------------------------------


class TestTaskQueueBackwardCompat:
    def test_getattr_resolves_stage_manager(self):
        """task_queue.StageManager should resolve to stage_manager.StageManager."""
        from aquarco_supervisor import task_queue
        from aquarco_supervisor.stage_manager import StageManager
        assert task_queue.StageManager is StageManager


# -----------------------------------------------------------------------
# config_store lazy __getattr__
# -----------------------------------------------------------------------


class TestConfigStoreLazyExports:
    def test_agent_store_reexport(self):
        """config_store should re-export sync_all_agent_definitions_to_db."""
        from aquarco_supervisor.config_store import sync_all_agent_definitions_to_db
        from aquarco_supervisor.agent_store import sync_all_agent_definitions_to_db as orig
        assert sync_all_agent_definitions_to_db is orig

    def test_pipeline_store_reexport(self):
        """config_store should re-export sync_pipeline_definitions_to_db."""
        from aquarco_supervisor.config_store import sync_pipeline_definitions_to_db
        from aquarco_supervisor.pipeline_store import sync_pipeline_definitions_to_db as orig
        assert sync_pipeline_definitions_to_db is orig

    def test_unknown_attr_raises(self):
        import aquarco_supervisor.config_store as cs
        with pytest.raises(AttributeError, match="no_such_function"):
            cs.no_such_function  # noqa: B018

    def test_shared_constants_accessible(self):
        from aquarco_supervisor.config_store import (
            AGENT_API_VERSION,
            AGENT_KIND,
            PIPELINE_KIND,
        )
        assert AGENT_API_VERSION == "aquarco.agents/v1"
        assert AGENT_KIND == "AgentDefinition"
        assert PIPELINE_KIND == "PipelineDefinition"
