#!/usr/bin/env python3
"""Performance benchmarks for Python supervisor vs shell scripts.

Measures startup time, config loading, and poller cycle latency.
Run inside the VM where the database is available.

Usage:
    python3 supervisor/scripts/benchmark.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "python" / "src"))

SUPERVISOR_DIR = Path(__file__).parent.parent
CONFIG_FILE = SUPERVISOR_DIR / "config" / "supervisor.yaml"
RESULTS: list[dict[str, object]] = []


def bench(name: str):
    """Decorator to time a benchmark function."""
    def decorator(fn):
        def wrapper(*a, **kw):
            start = time.perf_counter()
            result = fn(*a, **kw)
            elapsed_ms = (time.perf_counter() - start) * 1000
            RESULTS.append({"name": name, "ms": round(elapsed_ms, 2)})
            print(f"  {name}: {elapsed_ms:.2f} ms")
            return result
        return wrapper
    return decorator


# --- Python Benchmarks ---


@bench("python: import package")
def bench_python_import():
    import importlib
    importlib.invalidate_caches()
    # Force reimport by removing from sys.modules
    for key in list(sys.modules.keys()):
        if key.startswith("aifishtank_supervisor"):
            del sys.modules[key]
    import aifishtank_supervisor  # noqa: F401


@bench("python: load config")
def bench_python_config():
    from aifishtank_supervisor.config import load_config
    load_config(str(CONFIG_FILE))


@bench("python: parse models (100 tasks)")
def bench_python_models():
    from aifishtank_supervisor.models import Task
    for i in range(100):
        Task(
            id=f"bench-{i}",
            title=f"Benchmark task {i}",
            category="analyze",
            source="benchmark",
            source_ref=str(i),
            repository="test-repo",
            pipeline="feature-pipeline",
            initial_context={"key": "value", "number": i},
        )


@bench("python: check 1000 conditions")
def bench_python_conditions():
    from aifishtank_supervisor.pipeline.executor import check_conditions
    output = {"analysis": {"estimated_complexity": "high", "status": "pass"}}
    conditions = ["analysis.estimated_complexity >= medium"]
    for _ in range(1000):
        check_conditions(conditions, output)


@bench("python: build 100 accumulated contexts")
def bench_python_context():
    from aifishtank_supervisor.pipeline.context import build_accumulated_context
    task_context = {
        "task": {"id": "bench-1"},
        "stages": [
            {
                "stage_number": i,
                "category": "analyze",
                "agent": "agent-1",
                "status": "completed",
                "summary": f"Stage {i} summary",
                "full_output": "x" * 500,
            }
            for i in range(10)
        ],
        "context_entries": [{"key": f"k{i}", "value": f"v{i}"} for i in range(5)],
    }
    for _ in range(100):
        build_accumulated_context(task_context, current_stage=8, previous_output={"x": 1})


@bench("python: JSON extract (100 iterations)")
def bench_python_json_extract():
    from aifishtank_supervisor.cli.claude import _extract_json
    text = 'Some preamble\n```json\n{"result": "ok", "data": [1,2,3]}\n```\nEnd'
    for _ in range(100):
        _extract_json(text)


# --- Shell Benchmarks ---


@bench("shell: source config.sh + load_config")
def bench_shell_config():
    script = f"""
        source "{SUPERVISOR_DIR}/lib/utils.sh"
        source "{SUPERVISOR_DIR}/lib/config.sh"
        load_config "{CONFIG_FILE}"
    """
    subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        timeout=10,
    )


@bench("shell: source all libraries")
def bench_shell_source_all():
    script = f"""
        source "{SUPERVISOR_DIR}/lib/utils.sh"
        source "{SUPERVISOR_DIR}/lib/config.sh"
        load_config "{CONFIG_FILE}"
        source "{SUPERVISOR_DIR}/lib/task-queue.sh"
        source "{SUPERVISOR_DIR}/lib/agent-registry.sh"
        source "{SUPERVISOR_DIR}/lib/pipeline-executor.sh"
    """
    subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        timeout=10,
    )


def main():
    print("=" * 60)
    print("  AI Fishtank Supervisor Performance Benchmarks")
    print("=" * 60)
    print()

    if not CONFIG_FILE.exists():
        print(f"Config file not found: {CONFIG_FILE}")
        sys.exit(1)

    print("Python benchmarks:")
    bench_python_import()
    bench_python_config()
    bench_python_models()
    bench_python_conditions()
    bench_python_context()
    bench_python_json_extract()

    print()
    print("Shell benchmarks:")
    bench_shell_config()
    bench_shell_source_all()

    print()
    print("=" * 60)
    print("  Summary")
    print("=" * 60)
    print(f"{'Benchmark':<45} {'Time (ms)':>10}")
    print("-" * 57)
    for r in RESULTS:
        print(f"  {r['name']:<43} {r['ms']:>10.2f}")

    # Write JSON results
    results_file = Path("/tmp/aifishtank-benchmark-results.json")
    results_file.write_text(json.dumps(RESULTS, indent=2))
    print(f"\nResults saved to {results_file}")


if __name__ == "__main__":
    main()
