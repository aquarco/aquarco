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

-- Pin the role-level search_path so that the PostgreSQL default
-- ("$user", public) does not resolve "$user" to the aquarco schema.
-- Without this, tools that open their own connections (e.g. yoyo-migrations)
-- would create internal tables in aquarco instead of public.
ALTER ROLE aquarco SET search_path TO public;

-- Enable the pgcrypto extension (used for gen_random_uuid() where needed).
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Set search path for this session so subsequent migrations work
-- without schema-qualifying every object.
SET search_path TO aquarco, public;

