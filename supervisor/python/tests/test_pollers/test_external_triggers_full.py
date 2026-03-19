"""More comprehensive tests for external triggers poller."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from aifishtank_supervisor.database import Database
from aifishtank_supervisor.models import SupervisorConfig
from aifishtank_supervisor.pollers.external_triggers import ExternalTriggersPoller
from aifishtank_supervisor.task_queue import TaskQueue


@pytest.fixture
def watch_dir(tmp_path: Path) -> Path:
    d = tmp_path / "triggers"
    d.mkdir()
    (d / "processed").mkdir()
    return d


@pytest.fixture
def poller(
    sample_config: SupervisorConfig, watch_dir: Path
) -> ExternalTriggersPoller:
    # Override config to use temp dirs
    for p in sample_config.spec.pollers:
        if p.name == "external-triggers":
            p.config["watchDir"] = str(watch_dir)
            p.config["processedDir"] = str(watch_dir / "processed")

    tq = AsyncMock(spec=TaskQueue)
    tq.task_exists.return_value = False
    tq.create_task.return_value = True
    db = AsyncMock(spec=Database)
    db.fetch_val = AsyncMock(return_value=1)  # repo exists
    return ExternalTriggersPoller(sample_config, tq, db)


@pytest.mark.asyncio
async def test_poll_yaml_trigger(
    poller: ExternalTriggersPoller, watch_dir: Path
) -> None:
    trigger = {
        "category": "analyze",
        "title": "Test task",
        "repository": "test-repo",
    }
    (watch_dir / "test.yaml").write_text(yaml.dump(trigger))

    created = await poller.poll()
    assert created == 1

    # File should be moved to processed
    assert not (watch_dir / "test.yaml").exists()
    processed = list((watch_dir / "processed").glob("*.yaml"))
    assert len(processed) == 1


@pytest.mark.asyncio
async def test_poll_json_trigger(
    poller: ExternalTriggersPoller, watch_dir: Path
) -> None:
    import json

    trigger = {
        "category": "review",
        "title": "Review task",
        "repository": "test-repo",
    }
    (watch_dir / "test.json").write_text(json.dumps(trigger))

    created = await poller.poll()
    assert created == 1


@pytest.mark.asyncio
async def test_poll_invalid_yaml(
    poller: ExternalTriggersPoller, watch_dir: Path
) -> None:
    (watch_dir / "bad.yaml").write_text("{{invalid yaml")

    created = await poller.poll()
    assert created == 0

    # Should be in failed dir
    failed = list((watch_dir / "processed" / "failed").glob("*bad.yaml"))
    assert len(failed) == 1


@pytest.mark.asyncio
async def test_poll_no_category_still_creates_task(
    poller: ExternalTriggersPoller, watch_dir: Path
) -> None:
    """Category is no longer required on triggers; pipeline defaults to feature-pipeline."""
    trigger = {"title": "No category", "repository": "test-repo"}
    (watch_dir / "no-cat.yaml").write_text(yaml.dump(trigger))

    created = await poller.poll()
    assert created == 1


@pytest.mark.asyncio
async def test_poll_missing_title(
    poller: ExternalTriggersPoller, watch_dir: Path
) -> None:
    trigger = {"repository": "test-repo"}
    (watch_dir / "no-title.yaml").write_text(yaml.dump(trigger))

    created = await poller.poll()
    assert created == 0


@pytest.mark.asyncio
async def test_poll_missing_repository(
    poller: ExternalTriggersPoller, watch_dir: Path
) -> None:
    trigger = {"title": "Test"}
    (watch_dir / "no-repo.yaml").write_text(yaml.dump(trigger))

    created = await poller.poll()
    assert created == 0


@pytest.mark.asyncio
async def test_poll_existing_task(
    poller: ExternalTriggersPoller, watch_dir: Path
) -> None:
    poller._tq.task_exists.return_value = True

    trigger = {
        "category": "analyze",
        "title": "Existing",
        "repository": "test-repo",
    }
    (watch_dir / "existing.yaml").write_text(yaml.dump(trigger))

    created = await poller.poll()
    assert created == 0

    # Should still be moved to processed (not failed)
    assert not (watch_dir / "existing.yaml").exists()


@pytest.mark.asyncio
async def test_poll_empty_dir(
    poller: ExternalTriggersPoller, watch_dir: Path
) -> None:
    created = await poller.poll()
    assert created == 0


@pytest.mark.asyncio
async def test_poll_nonexistent_dir(
    sample_config: SupervisorConfig,
) -> None:
    for p in sample_config.spec.pollers:
        if p.name == "external-triggers":
            p.config["watchDir"] = "/nonexistent/path"

    tq = AsyncMock(spec=TaskQueue)
    db = AsyncMock(spec=Database)
    poller = ExternalTriggersPoller(sample_config, tq, db)

    created = await poller.poll()
    assert created == 0


@pytest.mark.asyncio
async def test_poll_multiple_files(
    poller: ExternalTriggersPoller, watch_dir: Path
) -> None:
    for i in range(3):
        trigger = {
            "category": "analyze",
            "title": f"Task {i}",
            "repository": "test-repo",
        }
        (watch_dir / f"task-{i}.yaml").write_text(yaml.dump(trigger))

    created = await poller.poll()
    assert created == 3


@pytest.mark.asyncio
async def test_poll_invalid_json(
    poller: ExternalTriggersPoller, watch_dir: Path
) -> None:
    """Invalid JSON triggers are moved to failed."""
    (watch_dir / "bad.json").write_text("{not valid json")

    created = await poller.poll()
    assert created == 0

    failed = list((watch_dir / "processed" / "failed").glob("*bad.json"))
    assert len(failed) == 1


@pytest.mark.asyncio
async def test_poll_non_dict_data(
    poller: ExternalTriggersPoller, watch_dir: Path
) -> None:
    """YAML that parses to a non-dict is moved to failed."""
    (watch_dir / "list.yaml").write_text("- item1\n- item2\n")

    created = await poller.poll()
    assert created == 0

    failed = list((watch_dir / "processed" / "failed").glob("*list.yaml"))
    assert len(failed) == 1


@pytest.mark.asyncio
async def test_poll_with_labels_in_context(
    poller: ExternalTriggersPoller, watch_dir: Path
) -> None:
    """Labels from trigger file are added to context."""
    trigger = {
        "category": "analyze",
        "title": "Labeled task",
        "repository": "test-repo",
        "labels": ["urgent", "bug"],
    }
    (watch_dir / "labeled.yaml").write_text(yaml.dump(trigger))

    created = await poller.poll()
    assert created == 1

    # Verify labels were passed in context
    call_kwargs = poller._tq.create_task.call_args.kwargs
    ctx = call_kwargs["context"]
    assert ctx["_labels"] == ["urgent", "bug"]
