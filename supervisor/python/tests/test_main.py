"""Tests for supervisor main module."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.main import Supervisor, _build_health_report


def test_build_health_report_with_stats() -> None:
    stats = {"completed": 10, "failed": 2, "pending": 5}
    report = _build_health_report(stats, uptime_minutes=120)

    assert "## Supervisor Health Report" in report
    assert "**Uptime:** 120 minutes" in report
    assert "| completed | 10 |" in report
    assert "| failed | 2 |" in report
    assert "| pending | 5 |" in report


def test_build_health_report_empty_stats() -> None:
    report = _build_health_report({}, uptime_minutes=0)

    assert "## Supervisor Health Report" in report
    assert "| (none) | 0 |" in report


def test_build_health_report_sorted() -> None:
    stats = {"pending": 1, "completed": 2, "executing": 3}
    report = _build_health_report(stats, uptime_minutes=60)

    lines = report.split("\n")
    status_lines = [
        line for line in lines
        if line.startswith("| ") and "Status" not in line and "---" not in line
    ]
    statuses = [line.split("|")[1].strip() for line in status_lines]
    assert statuses == ["completed", "executing", "pending"]


def test_supervisor_should_run_first_time(sample_config: Any) -> None:
    """_should_run returns True if the poller has never run."""
    supervisor = Supervisor(sample_config, {})
    assert supervisor._should_run("my-poller", interval=60) is True


def test_supervisor_should_run_after_interval(sample_config: Any) -> None:
    """_should_run returns True once enough time has passed."""
    import time

    supervisor = Supervisor(sample_config, {})
    supervisor._poller_last_run["my-poller"] = time.time() - 100
    assert supervisor._should_run("my-poller", interval=60) is True


def test_supervisor_should_not_run_before_interval(sample_config: Any) -> None:
    """_should_run returns False when interval hasn't elapsed."""
    import time

    supervisor = Supervisor(sample_config, {})
    supervisor._poller_last_run["my-poller"] = time.time() - 10
    assert supervisor._should_run("my-poller", interval=60) is False


def test_supervisor_mark_ran(sample_config: Any) -> None:
    """_mark_ran records the current time for a poller."""
    import time

    supervisor = Supervisor(sample_config, {})
    before = time.time()
    supervisor._mark_ran("my-poller")
    after = time.time()

    recorded = supervisor._poller_last_run["my-poller"]
    assert before <= recorded <= after


@pytest.mark.asyncio
async def test_supervisor_handle_shutdown(sample_config: Any) -> None:
    supervisor = Supervisor(sample_config, {})
    supervisor._shutdown_event = asyncio.Event()
    assert supervisor._shutdown is False
    assert not supervisor._shutdown_event.is_set()
    supervisor._handle_shutdown()
    assert supervisor._shutdown is True
    assert supervisor._shutdown_event.is_set()


def test_supervisor_handle_reload(sample_config: Any) -> None:
    supervisor = Supervisor(sample_config, {})
    assert supervisor._reload_requested is False
    supervisor._handle_reload()
    assert supervisor._reload_requested is True


@pytest.mark.asyncio
async def test_supervisor_run_pollers_skips_disabled(sample_config: Any) -> None:
    """Disabled pollers are not called."""
    supervisor = Supervisor(sample_config, {})

    mock_poller = MagicMock()
    mock_poller.name = "test-poller"
    mock_poller.is_enabled.return_value = False
    mock_poller.get_interval.return_value = 10
    mock_poller.poll = AsyncMock()

    supervisor._pollers = [mock_poller]
    await supervisor._run_pollers()

    mock_poller.poll.assert_not_called()


@pytest.mark.asyncio
async def test_supervisor_run_pollers_calls_enabled(sample_config: Any) -> None:
    """Enabled pollers that are due get called."""
    supervisor = Supervisor(sample_config, {})

    mock_poller = MagicMock()
    mock_poller.name = "test-poller"
    mock_poller.is_enabled.return_value = True
    mock_poller.get_interval.return_value = 0  # always ready
    mock_poller.poll = AsyncMock(return_value=0)

    supervisor._pollers = [mock_poller]
    await supervisor._run_pollers()

    mock_poller.poll.assert_called_once()


@pytest.mark.asyncio
async def test_supervisor_run_pollers_marks_ran_after_error(sample_config: Any) -> None:
    """Even when poller.poll raises, the poller is marked as ran."""
    supervisor = Supervisor(sample_config, {})

    mock_poller = MagicMock()
    mock_poller.name = "failing-poller"
    mock_poller.is_enabled.return_value = True
    mock_poller.get_interval.return_value = 0
    mock_poller.poll = AsyncMock(side_effect=RuntimeError("boom"))

    supervisor._pollers = [mock_poller]
    await supervisor._run_pollers()

    assert "failing-poller" in supervisor._poller_last_run


@pytest.mark.asyncio
async def test_dispatch_pending_tasks_no_capacity(sample_config: Any) -> None:
    """When all agent slots are taken, no tasks are dispatched."""
    supervisor = Supervisor(sample_config, {})

    mock_db = AsyncMock()
    mock_db.fetch_val = AsyncMock(return_value=10)  # 10 active, max is 2
    mock_tq = AsyncMock()
    mock_registry = AsyncMock()
    mock_executor = AsyncMock()

    supervisor._db = mock_db
    supervisor._tq = mock_tq
    supervisor._registry = mock_registry
    supervisor._executor = mock_executor

    await supervisor._dispatch_pending_tasks()

    mock_tq.get_next_task.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_pending_tasks_launches_task(sample_config: Any) -> None:
    """When capacity exists and a task is available, it is dispatched."""
    supervisor = Supervisor(sample_config, {})

    mock_db = AsyncMock()
    mock_db.fetch_val = AsyncMock(return_value=0)  # 0 active

    mock_task = MagicMock()
    mock_task.id = "task-001"
    mock_task.pipeline = "feature-pipeline"
    mock_task.initial_context = {}

    mock_tq = AsyncMock()
    # Return a task once, then None to stop the loop
    mock_tq.get_next_task = AsyncMock(side_effect=[mock_task, None])
    mock_registry = AsyncMock()
    mock_executor = AsyncMock()

    supervisor._db = mock_db
    supervisor._tq = mock_tq
    supervisor._registry = mock_registry
    supervisor._executor = mock_executor

    await supervisor._dispatch_pending_tasks()

    mock_tq.get_next_task.assert_called()
    assert len(supervisor._in_flight) == 1


@pytest.mark.asyncio
async def test_run_task_success(sample_config: Any) -> None:
    """_run_task sets status to executing and calls execute_pipeline."""
    supervisor = Supervisor(sample_config, {})

    mock_tq = AsyncMock()
    mock_executor = AsyncMock()

    supervisor._tq = mock_tq
    supervisor._executor = mock_executor

    await supervisor._run_task("task-001", "feature-pipeline", {"key": "val"})

    mock_tq.update_task_status.assert_awaited_once()
    mock_executor.execute_pipeline.assert_awaited_once_with(
        "feature-pipeline", "task-001", {"key": "val"}
    )
    mock_tq.fail_task.assert_not_called()


@pytest.mark.asyncio
async def test_run_task_failure_calls_fail_task(sample_config: Any) -> None:
    """If execute_pipeline raises, fail_task is called."""
    supervisor = Supervisor(sample_config, {})

    mock_tq = AsyncMock()
    mock_executor = AsyncMock()
    mock_executor.execute_pipeline = AsyncMock(side_effect=RuntimeError("agent crashed"))

    supervisor._tq = mock_tq
    supervisor._executor = mock_executor

    await supervisor._run_task("task-002", "bugfix-pipeline", {})

    mock_tq.fail_task.assert_awaited_once_with("task-002", "Unhandled execution error")


@pytest.mark.asyncio
async def test_check_timed_out_tasks(sample_config: Any) -> None:
    """_check_timed_out_tasks marks timed-out tasks as TIMEOUT."""
    supervisor = Supervisor(sample_config, {})

    mock_tq = AsyncMock()
    mock_tq.get_timed_out_tasks = AsyncMock(return_value=["task-abc", "task-xyz"])

    supervisor._tq = mock_tq

    await supervisor._check_timed_out_tasks()

    assert mock_tq.fail_task.await_count == 2
    # update_task_status is no longer called — fail_task handles the status
    mock_tq.update_task_status.assert_not_called()

    calls = mock_tq.fail_task.await_args_list
    task_ids = [c.args[0] for c in calls]
    assert "task-abc" in task_ids
    assert "task-xyz" in task_ids


@pytest.mark.asyncio
async def test_check_timed_out_tasks_no_tq(sample_config: Any) -> None:
    """_check_timed_out_tasks does nothing if task queue not initialized."""
    supervisor = Supervisor(sample_config, {})
    supervisor._tq = None
    # Should complete without error
    await supervisor._check_timed_out_tasks()


@pytest.mark.asyncio
async def test_maybe_report_health_disabled(sample_config: Any) -> None:
    """Health reporting does nothing when health is disabled."""
    supervisor = Supervisor(sample_config, {})
    # sample_config has health.enabled=False
    mock_db = AsyncMock()
    supervisor._db = mock_db

    await supervisor._maybe_report_health()

    mock_db.fetch_all.assert_not_called()


@pytest.mark.asyncio
async def test_reload_config_success(sample_config: Any, sample_config_path: Any) -> None:
    """_reload_config loads fresh config and secrets."""
    supervisor = Supervisor(sample_config, {})
    supervisor._config_file = str(sample_config_path)

    with patch("aquarco_supervisor.main.load_config", return_value=sample_config) as mock_cfg, \
         patch("aquarco_supervisor.main.load_secrets", return_value={"key": "val"}) as mock_sec:
        await supervisor._reload_config()

    mock_cfg.assert_called_once_with(str(sample_config_path))
    mock_sec.assert_called_once()
    assert supervisor._secrets == {"key": "val"}


@pytest.mark.asyncio
async def test_reload_config_reloads_registry(sample_config: Any, sample_config_path: Any) -> None:
    """_reload_config reloads the in-memory agent registry when present."""
    supervisor = Supervisor(sample_config, {})
    supervisor._config_file = str(sample_config_path)

    mock_registry = AsyncMock()
    mock_registry._agents = {"review-agent": {}}
    supervisor._registry = mock_registry

    with patch("aquarco_supervisor.main.load_config", return_value=sample_config), \
         patch("aquarco_supervisor.main.load_secrets", return_value={}):
        await supervisor._reload_config()

    mock_registry.load.assert_awaited_once()


@pytest.mark.asyncio
async def test_reload_config_skips_registry_when_none(
    sample_config: Any, sample_config_path: Any
) -> None:
    """_reload_config succeeds without calling registry.load() when _registry is None."""
    supervisor = Supervisor(sample_config, {})
    supervisor._config_file = str(sample_config_path)

    # Ensure _registry is None (pre-initialization state)
    assert supervisor._registry is None

    with patch("aquarco_supervisor.main.load_config", return_value=sample_config), \
         patch("aquarco_supervisor.main.load_secrets", return_value={"k": "v"}):
        await supervisor._reload_config()

    # Registry should still be None — no attempt to create or load
    assert supervisor._registry is None
    # Config and secrets should still be updated
    assert supervisor._secrets == {"k": "v"}


@pytest.mark.asyncio
async def test_reload_config_registry_load_error_handled(
    sample_config: Any, sample_config_path: Any
) -> None:
    """If registry.load() raises during reload, the error is caught and config still updates."""
    supervisor = Supervisor(sample_config, {})
    supervisor._config_file = str(sample_config_path)

    mock_registry = AsyncMock()
    mock_registry.load = AsyncMock(side_effect=RuntimeError("corrupt agent file"))
    supervisor._registry = mock_registry

    with patch("aquarco_supervisor.main.load_config", return_value=sample_config), \
         patch("aquarco_supervisor.main.load_secrets", return_value={"new": "secrets"}):
        # Should not raise — the exception in the entire _reload_config is caught
        await supervisor._reload_config()

    mock_registry.load.assert_awaited_once()


@pytest.mark.asyncio
async def test_reload_config_registry_logs_agent_count(
    sample_config: Any, sample_config_path: Any
) -> None:
    """_reload_config logs agent_count from the registry after reload."""
    supervisor = Supervisor(sample_config, {})
    supervisor._config_file = str(sample_config_path)

    mock_registry = AsyncMock()
    mock_registry._agents = {"agent-a": {}, "agent-b": {}, "agent-c": {}}
    supervisor._registry = mock_registry

    with patch("aquarco_supervisor.main.load_config", return_value=sample_config), \
         patch("aquarco_supervisor.main.load_secrets", return_value={}):
        await supervisor._reload_config()

    mock_registry.load.assert_awaited_once()
    # Verify the registry has the expected number of agents (used in the log call)
    assert len(supervisor._registry._agents) == 3


@pytest.mark.asyncio
async def test_reload_config_failure_keeps_old(sample_config: Any) -> None:
    """If reload fails, old config is preserved."""
    supervisor = Supervisor(sample_config, {"old": "secret"})
    supervisor._config_file = "/nonexistent.yaml"

    with patch("aquarco_supervisor.main.load_config", side_effect=FileNotFoundError("gone")):
        await supervisor._reload_config()

    # Old secrets should be preserved since reload failed
    assert supervisor._secrets == {"old": "secret"}


@pytest.mark.asyncio
async def test_maybe_report_health_enabled(sample_config: Any) -> None:
    """When health is enabled and interval elapsed, fetches stats and reports."""
    # Enable health reporting
    sample_config.spec.health.enabled = True
    sample_config.spec.health.report_interval_minutes = 0  # always due
    sample_config.spec.health.issue_number = 1

    supervisor = Supervisor(sample_config, {})
    supervisor._last_health_report = 0  # force due

    mock_db = AsyncMock()
    mock_db.fetch_all = AsyncMock(return_value=[
        {"status": "completed", "count": 5},
        {"status": "failed", "count": 1},
    ])
    mock_db.fetch_one = AsyncMock(return_value={"url": "https://github.com/test/repo.git"})
    supervisor._db = mock_db

    with patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_exec.return_value = mock_proc

        await supervisor._maybe_report_health()

    mock_db.fetch_all.assert_awaited_once()
    mock_exec.assert_awaited_once()
    # Verify gh issue comment was called
    call_args = mock_exec.await_args[0]
    assert "gh" in call_args
    assert "issue" in call_args
    assert "comment" in call_args


@pytest.mark.asyncio
async def test_maybe_report_health_not_due(sample_config: Any) -> None:
    """When health interval hasn't elapsed, skip reporting."""
    sample_config.spec.health.enabled = True
    sample_config.spec.health.report_interval_minutes = 60

    supervisor = Supervisor(sample_config, {})
    supervisor._last_health_report = time.time()  # just reported

    mock_db = AsyncMock()
    supervisor._db = mock_db

    await supervisor._maybe_report_health()

    mock_db.fetch_all.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_report_health_no_db(sample_config: Any) -> None:
    """When db is not initialized, skip reporting."""
    sample_config.spec.health.enabled = True
    sample_config.spec.health.report_interval_minutes = 0

    supervisor = Supervisor(sample_config, {})
    supervisor._last_health_report = 0
    supervisor._db = None

    await supervisor._maybe_report_health()


@pytest.mark.asyncio
async def test_maybe_report_health_exception_handled(sample_config: Any) -> None:
    """Exceptions during health reporting are caught."""
    sample_config.spec.health.enabled = True
    sample_config.spec.health.report_interval_minutes = 0

    supervisor = Supervisor(sample_config, {})
    supervisor._last_health_report = 0

    mock_db = AsyncMock()
    mock_db.fetch_all = AsyncMock(side_effect=RuntimeError("db down"))
    supervisor._db = mock_db

    # Should not raise
    await supervisor._maybe_report_health()


@pytest.mark.asyncio
async def test_main_loop_single_iteration(sample_config: Any) -> None:
    """Main loop runs one iteration then shuts down."""
    supervisor = Supervisor(sample_config, {})
    supervisor._shutdown_event = asyncio.Event()

    # Mock all components
    supervisor._clone_worker = AsyncMock()
    supervisor._pull_worker = AsyncMock()
    supervisor._tq = AsyncMock()
    supervisor._registry = AsyncMock()
    supervisor._executor = AsyncMock()
    supervisor._db = AsyncMock()
    supervisor._pollers = []

    # No active agents, no tasks
    supervisor._db.fetch_val = AsyncMock(return_value=0)
    supervisor._tq.get_next_task = AsyncMock(return_value=None)
    supervisor._tq.get_timed_out_tasks = AsyncMock(return_value=[])

    # Shutdown after first iteration — set shutdown flag before cooldown wait
    async def clone_then_shutdown() -> None:
        supervisor._shutdown = True
        supervisor._shutdown_event.set()

    supervisor._clone_worker.clone_pending_repos = AsyncMock(
        side_effect=clone_then_shutdown
    )

    await supervisor._main_loop()

    supervisor._clone_worker.clone_pending_repos.assert_awaited_once()
    supervisor._db.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_loop_handles_exception(sample_config: Any) -> None:
    """Main loop catches exceptions and continues."""
    supervisor = Supervisor(sample_config, {})
    supervisor._shutdown_event = asyncio.Event()

    call_count = 0

    async def clone_error_then_shutdown() -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            supervisor._shutdown = True
            supervisor._shutdown_event.set()
        raise RuntimeError("clone error")

    supervisor._clone_worker = AsyncMock()
    supervisor._clone_worker.clone_pending_repos = AsyncMock(
        side_effect=clone_error_then_shutdown
    )
    supervisor._pull_worker = None
    supervisor._pollers = []
    supervisor._tq = None
    supervisor._registry = None
    supervisor._executor = None
    supervisor._db = AsyncMock()

    await supervisor._main_loop()
    # Should complete without raising
    supervisor._db.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_loop_drains_in_flight(sample_config: Any) -> None:
    """Main loop waits for in-flight tasks on shutdown."""
    supervisor = Supervisor(sample_config, {})
    supervisor._clone_worker = None
    supervisor._pull_worker = None
    supervisor._pollers = []
    supervisor._tq = None
    supervisor._registry = None
    supervisor._executor = None
    supervisor._db = AsyncMock()
    supervisor._shutdown = True  # immediate shutdown

    # Add a fake in-flight task
    completed = False

    async def slow_task():
        nonlocal completed
        await asyncio.sleep(0.01)
        completed = True

    supervisor._in_flight = {asyncio.create_task(slow_task())}

    await supervisor._main_loop()

    assert completed is True


@pytest.mark.asyncio
async def test_dispatch_pending_tasks_no_components(sample_config: Any) -> None:
    """dispatch returns early when components not initialized."""
    supervisor = Supervisor(sample_config, {})
    supervisor._tq = None
    supervisor._registry = None
    supervisor._executor = None
    supervisor._db = None

    await supervisor._dispatch_pending_tasks()  # should not raise


@pytest.mark.asyncio
async def test_start_initializes_components(sample_config: Any, tmp_path: Any) -> None:
    """start() initializes DB, components, and enters main loop."""
    import yaml

    config_path = tmp_path / "supervisor.yaml"
    config_data = {
        "apiVersion": "aquarco.supervisor/v1",
        "metadata": {"name": "test"},
        "spec": {
            "workdir": str(tmp_path),
            "agentsDir": str(tmp_path / "agents"),
            "database": {"url": "postgresql://x:x@localhost/x", "maxConnections": 1},
            "logging": {"level": "debug", "format": "json"},
            "globalLimits": {
                "maxConcurrentAgents": 1,
                "maxRetries": 1,
                "cooldownBetweenTasksSeconds": 1,
            },
            "secrets": {"githubTokenFile": "/tmp/t", "anthropicKeyFile": "/tmp/a"},
            "health": {"enabled": False},
            "pollers": [],
        },
    }
    config_path.write_text(yaml.dump(config_data))

    supervisor = Supervisor(sample_config, {})

    with patch.object(supervisor, "_main_loop", new_callable=AsyncMock) as mock_loop, \
         patch("aquarco_supervisor.main.Database") as mock_db_cls, \
         patch("aquarco_supervisor.main.AgentRegistry") as mock_reg_cls, \
         patch("aquarco_supervisor.main.setup_logging"):
        mock_db_inst = AsyncMock()
        mock_db_cls.return_value = mock_db_inst
        mock_reg_inst = AsyncMock()
        mock_reg_cls.return_value = mock_reg_inst

        await supervisor.start(str(config_path))

    mock_db_inst.connect.assert_awaited_once()
    mock_reg_inst.load.assert_awaited_once()
    mock_loop.assert_awaited_once()


# ---------------------------------------------------------------------------
# _sync_definitions_to_db — schema path resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_definitions_to_db_calls_sync_all_with_schema_paths(
    sample_config: Any, tmp_path: Any
) -> None:
    """_sync_definitions_to_db passes system/pipeline schema paths when they exist."""
    from unittest.mock import patch, AsyncMock

    supervisor = Supervisor(sample_config, {})
    supervisor._db = AsyncMock()

    # Build a fake agents_dir and schema dir where both schema files exist.
    # main.py computes: schema_dir = agents_dir.parent.parent / "schemas"
    # So if agents_dir = tmp_path/agents/definitions, schema_dir = tmp_path/schemas
    agents_dir = tmp_path / "agents" / "definitions"
    schema_dir = tmp_path / "schemas"
    agents_dir.mkdir(parents=True)
    schema_dir.mkdir(parents=True)
    (schema_dir / "system-agent-v1.json").write_text("{}")
    (schema_dir / "pipeline-agent-v1.json").write_text("{}")

    # Point config to the tmp agents_dir
    supervisor._config.spec.agents_dir = str(agents_dir)

    with patch(
        "aquarco_supervisor.main.sync_all_agent_definitions_to_db",
        new_callable=AsyncMock,
        return_value=0,
    ) as mock_sync:
        await supervisor._sync_definitions_to_db()

    mock_sync.assert_awaited_once()
    call_kwargs = mock_sync.call_args.kwargs
    assert call_kwargs["system_schema_path"] == schema_dir / "system-agent-v1.json"
    assert call_kwargs["pipeline_schema_path"] == schema_dir / "pipeline-agent-v1.json"


@pytest.mark.asyncio
async def test_sync_definitions_to_db_passes_none_when_schemas_missing(
    sample_config: Any, tmp_path: Any
) -> None:
    """_sync_definitions_to_db passes None schema paths when schema files are absent."""
    from unittest.mock import patch, AsyncMock

    supervisor = Supervisor(sample_config, {})
    supervisor._db = AsyncMock()

    # agents_dir exists but no schema files
    agents_dir = tmp_path / "agents" / "definitions"
    agents_dir.mkdir(parents=True)
    supervisor._config.spec.agents_dir = str(agents_dir)

    with patch(
        "aquarco_supervisor.main.sync_all_agent_definitions_to_db",
        new_callable=AsyncMock,
        return_value=0,
    ) as mock_sync:
        await supervisor._sync_definitions_to_db()

    mock_sync.assert_awaited_once()
    call_kwargs = mock_sync.call_args.kwargs
    assert call_kwargs["system_schema_path"] is None
    assert call_kwargs["pipeline_schema_path"] is None


@pytest.mark.asyncio
async def test_sync_definitions_to_db_skips_when_no_db(
    sample_config: Any,
) -> None:
    """_sync_definitions_to_db returns early when no DB is available."""
    from unittest.mock import patch, AsyncMock

    supervisor = Supervisor(sample_config, {})
    supervisor._db = None

    with patch(
        "aquarco_supervisor.main.sync_all_agent_definitions_to_db",
        new_callable=AsyncMock,
    ) as mock_sync:
        await supervisor._sync_definitions_to_db()

    mock_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_definitions_to_db_handles_exception_gracefully(
    sample_config: Any, tmp_path: Any
) -> None:
    """_sync_definitions_to_db catches exceptions without propagating them."""
    from unittest.mock import patch, AsyncMock

    supervisor = Supervisor(sample_config, {})
    supervisor._db = AsyncMock()

    agents_dir = tmp_path / "agents" / "definitions"
    agents_dir.mkdir(parents=True)
    supervisor._config.spec.agents_dir = str(agents_dir)

    with patch(
        "aquarco_supervisor.main.sync_all_agent_definitions_to_db",
        new_callable=AsyncMock,
        side_effect=RuntimeError("unexpected error"),
    ):
        # Must not raise — exceptions are caught internally
        await supervisor._sync_definitions_to_db()
