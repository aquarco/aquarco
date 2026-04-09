SET search_path TO aquarco, public;
-- WARNING: This drops the entire aquarco schema and ALL data within it!
ALTER ROLE aquarco RESET search_path;
DROP EXTENSION IF EXISTS pgcrypto;
DROP SCHEMA IF EXISTS aquarco CASCADE;
