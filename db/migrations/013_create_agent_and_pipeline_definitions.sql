-- depends: 012_fix_null_agent_instance
-- Store versioned agent definitions (full YAML content) in the database.
-- Same (name, version) → UPDATE; new version → INSERT, old versions deactivated.
CREATE TABLE IF NOT EXISTS agent_definitions (
    name            TEXT NOT NULL,             -- metadata.name (kebab-case identifier)
    version         TEXT NOT NULL,             -- metadata.version (semver)
    description     TEXT NOT NULL,             -- metadata.description
    labels          JSONB DEFAULT '{}',        -- metadata.labels (key-value pairs)
    spec            JSONB NOT NULL,            -- full spec object from YAML
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,  -- only one active version per name
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (name, version)
);

CREATE INDEX idx_agent_definitions_active ON agent_definitions (name) WHERE is_active;

-- Trigger to auto-update updated_at (reuses existing function from 008)
CREATE TRIGGER trg_agent_definitions_updated_at
    BEFORE UPDATE ON agent_definitions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Store versioned pipeline definitions in the database.
CREATE TABLE IF NOT EXISTS pipeline_definitions (
    name            TEXT NOT NULL,             -- pipeline name identifier
    version         TEXT NOT NULL,             -- pipeline version (semver)
    trigger_config  JSONB NOT NULL DEFAULT '{}',  -- trigger object (labels, events)
    stages          JSONB NOT NULL DEFAULT '[]',  -- ordered array of stage configs
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,  -- only one active version per name
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (name, version)
);

CREATE INDEX idx_pipeline_definitions_active ON pipeline_definitions (name) WHERE is_active;

CREATE TRIGGER trg_pipeline_definitions_updated_at
    BEFORE UPDATE ON pipeline_definitions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
