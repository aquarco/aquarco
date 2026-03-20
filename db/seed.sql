-- db/seed.sql
-- Purpose: Development seed data for the Aquarco agent system.
--
-- This file is safe to run repeatedly; INSERT statements use
-- ON CONFLICT DO NOTHING so re-seeding is idempotent.
--
-- Usage:
--   psql -d aquarco -f db/seed.sql
-- Or via the Makefile target:
--   make db-seed

SET search_path TO aquarco, public;

-- ---------------------------------------------------------------------------
-- Repositories
-- ---------------------------------------------------------------------------

INSERT INTO repositories (name, url, branch, clone_dir, pollers, clone_status, head_sha)
VALUES
    (
        'aquarco',
        'git@github.com:example-org/aquarco.git',
        'main',
        '/repos/aquarco',
        ARRAY['github-issues-poller', 'github-pr-poller'],
        'ready',
        'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
    ),
    (
        'sample-app',
        'git@github.com:example-org/sample-app.git',
        'main',
        '/repos/sample-app',
        ARRAY['github-issues-poller'],
        'pending',
        NULL
    )
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Agent instances (one row per agent definition)
-- ---------------------------------------------------------------------------

INSERT INTO agent_instances (agent_name, active_count, total_executions, total_tokens_used)
VALUES
    ('review-agent',         0, 0, 0),
    ('implementation-agent', 0, 0, 0),
    ('test-agent',           0, 0, 0),
    ('design-agent',         0, 0, 0),
    ('docs-agent',           0, 0, 0),
    ('analyze-agent',        0, 0, 0)
ON CONFLICT (agent_name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Poll state (seed cursors so pollers start from a recent known position)
-- ---------------------------------------------------------------------------

INSERT INTO poll_state (poller_name, last_poll_at, last_successful_at, cursor, state_data)
VALUES
    (
        'github-issues-poller',
        NOW() - INTERVAL '5 minutes',
        NOW() - INTERVAL '5 minutes',
        '2026-03-14T00:00:00Z',
        '{"rate_limit_remaining": 5000, "rate_limit_reset": null}'::jsonb
    ),
    (
        'github-pr-poller',
        NOW() - INTERVAL '5 minutes',
        NOW() - INTERVAL '5 minutes',
        '2026-03-14T00:00:00Z',
        '{"rate_limit_remaining": 5000, "rate_limit_reset": null}'::jsonb
    )
ON CONFLICT (poller_name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Tasks — various lifecycle states for UI development and testing
-- ---------------------------------------------------------------------------

INSERT INTO tasks (
    id, title, category, status, priority,
    source, source_ref, pipeline, repository,
    initial_context, created_at, updated_at,
    current_stage, retry_count
)
VALUES
    -- 1. Pending task — just arrived, not yet queued
    (
        'github-issue-101',
        'Add rate limiting to GraphQL API',
        'implementation',
        'pending',
        30,
        'github-issue', '101',
        'feature-pipeline',
        'aquarco',
        '{"issue_number": 101, "issue_title": "Add rate limiting to GraphQL API", "body": "We need per-user rate limiting on the GraphQL endpoint.", "labels": ["enhancement", "backend"]}'::jsonb,
        NOW() - INTERVAL '10 minutes',
        NOW() - INTERVAL '10 minutes',
        0, 0
    ),

    -- 2. Queued task — picked up by supervisor, waiting for agent slot
    (
        'github-issue-98',
        'Review PR: fix null pointer in task router',
        'review',
        'queued',
        10,
        'github-pr', '42',
        'review-pipeline',
        'aquarco',
        '{"pr_number": 42, "pr_title": "fix null pointer in task router", "diff_url": "https://github.com/example-org/aquarco/pull/42.diff"}'::jsonb,
        NOW() - INTERVAL '20 minutes',
        NOW() - INTERVAL '2 minutes',
        0, 0
    ),

    -- 3. Executing task — currently running in stage 1
    (
        'github-issue-95',
        'Analyse performance regression in pipeline executor',
        'analyze',
        'executing',
        20,
        'github-issue', '95',
        'analysis-pipeline',
        'aquarco',
        '{"issue_number": 95, "labels": ["performance", "regression"], "milestone": "v1.0"}'::jsonb,
        NOW() - INTERVAL '45 minutes',
        NOW() - INTERVAL '3 minutes',
        1, 0
    ),

    -- 4. Completed task
    (
        'github-issue-80',
        'Write unit tests for supervisor retry logic',
        'test',
        'completed',
        50,
        'github-issue', '80',
        'test-pipeline',
        'aquarco',
        '{"issue_number": 80, "labels": ["testing"]}'::jsonb,
        NOW() - INTERVAL '3 hours',
        NOW() - INTERVAL '1 hour',
        2, 0
    ),

    -- 5. Failed task — exhausted retries
    (
        'github-issue-77',
        'Design schema for multi-tenant agent isolation',
        'design',
        'failed',
        40,
        'github-issue', '77',
        'design-pipeline',
        'sample-app',
        '{"issue_number": 77, "labels": ["design", "architecture"]}'::jsonb,
        NOW() - INTERVAL '6 hours',
        NOW() - INTERVAL '4 hours',
        0, 3
    ),

    -- 6. Blocked task — waiting on external dependency
    (
        'github-issue-110',
        'Document agent definition YAML schema',
        'docs',
        'blocked',
        60,
        'github-issue', '110',
        'docs-pipeline',
        'aquarco',
        '{"issue_number": 110, "labels": ["documentation"], "blocked_by": "TASK-002 schema not finalised"}'::jsonb,
        NOW() - INTERVAL '1 hour',
        NOW() - INTERVAL '30 minutes',
        0, 0
    )
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Stages for the executing task (github-issue-95)
-- ---------------------------------------------------------------------------

INSERT INTO stages (
    task_id, stage_number, category, agent, agent_version,
    status, started_at, completed_at,
    structured_output, tokens_input, tokens_output, retry_count
)
VALUES
    -- Stage 0: completed analysis
    (
        'github-issue-95',
        0,
        'analyze',
        'analyze-agent',
        '1.0.0',
        'completed',
        NOW() - INTERVAL '40 minutes',
        NOW() - INTERVAL '25 minutes',
        '{
            "findings": [
                "Pipeline executor spawns a new DB connection per stage",
                "No connection pooling in supervisor main loop",
                "stage_number index missing on stages table causes seq scan"
            ],
            "severity": "medium",
            "recommended_next_stage": "implementation"
        }'::jsonb,
        4200, 1800,
        0
    ),
    -- Stage 1: currently executing
    (
        'github-issue-95',
        1,
        'implementation',
        'implementation-agent',
        '1.0.0',
        'executing',
        NOW() - INTERVAL '3 minutes',
        NULL,
        NULL,
        NULL, NULL,
        0
    )
ON CONFLICT (task_id, stage_number) DO NOTHING;

-- Stages for the completed task (github-issue-80)
INSERT INTO stages (
    task_id, stage_number, category, agent, agent_version,
    status, started_at, completed_at,
    structured_output, tokens_input, tokens_output, retry_count
)
VALUES
    (
        'github-issue-80',
        0,
        'analyze',
        'analyze-agent',
        '1.0.0',
        'completed',
        NOW() - INTERVAL '3 hours',
        NOW() - INTERVAL '2 hours' - INTERVAL '30 minutes',
        '{"scope": "supervisor retry logic", "test_surface": ["retryTask", "escalateToBlocked", "resetStage"]}'::jsonb,
        2100, 900,
        0
    ),
    (
        'github-issue-80',
        1,
        'test',
        'test-agent',
        '1.0.0',
        'completed',
        NOW() - INTERVAL '2 hours' - INTERVAL '25 minutes',
        NOW() - INTERVAL '1 hour',
        '{"tests_written": 14, "coverage_delta": "+8.3%", "files_created": ["src/supervisor/__tests__/retry.test.ts"]}'::jsonb,
        5800, 2400,
        0
    )
ON CONFLICT (task_id, stage_number) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Context entries for the executing task (github-issue-95)
-- ---------------------------------------------------------------------------

INSERT INTO context (task_id, stage_number, key, value_type, value_json, value_text, value_file_ref)
VALUES
    (
        'github-issue-95',
        0,
        'analysis_findings',
        'json',
        '[
            "Pipeline executor spawns a new DB connection per stage",
            "No connection pooling in supervisor main loop",
            "stage_number index missing on stages table causes seq scan"
        ]'::jsonb,
        NULL, NULL
    ),
    (
        'github-issue-95',
        0,
        'severity',
        'text',
        NULL,
        'medium',
        NULL
    ),
    (
        'github-issue-95',
        0,
        'profiling_output',
        'file_ref',
        NULL, NULL,
        'blobs/github-issue-95-profile.txt'
    )
ON CONFLICT (task_id, key) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Pipeline checkpoint for the executing task
-- ---------------------------------------------------------------------------

INSERT INTO pipeline_checkpoints (task_id, last_completed_stage, checkpoint_data)
VALUES
    (
        'github-issue-95',
        0,
        '{
            "branch": "fix/perf-regression-95",
            "workspace": "/workspace/github-issue-95",
            "env": {
                "TASK_ID": "github-issue-95",
                "PIPELINE": "analysis-pipeline"
            }
        }'::jsonb
    )
ON CONFLICT (task_id) DO NOTHING;
