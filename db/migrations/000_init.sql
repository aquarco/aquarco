-- depends:
-- Migration: 000_init.sql
-- Purpose: Database and schema initialization for the Aquarco agent system.
--
-- This file is run once before all other migrations.
-- It establishes the aquarco schema, sets the search path,
-- and records any database-level configuration.
--
-- Run this as a superuser or the database owner.

-- The aquarco schema namespaces all application objects.
-- Using a dedicated schema keeps the database clean and allows
-- multiple environments (aquarco, aquarco_test) in one cluster.
CREATE SCHEMA IF NOT EXISTS aquarco;

-- Set the default search path for the application role so that
-- all subsequent migrations and queries do not need to qualify
-- every object with the schema name.
-- Replace 'aquarco_app' with the actual application role name used
-- in the connection pool.
-- ALTER ROLE aquarco_app SET search_path TO aquarco, public;

-- Enable the pgcrypto extension (used for gen_random_uuid() where needed).
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Set search path for this session so subsequent migrations work
-- without schema-qualifying every object.
SET search_path TO aquarco, public;

