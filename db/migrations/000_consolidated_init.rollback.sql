SET search_path TO aquarco, public;
-- Rollback: drop the entire aquarco schema and all contained objects.
-- This atomically removes all tables, functions, triggers, indexes, and sequences.
DROP SCHEMA aquarco CASCADE;
