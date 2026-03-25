SET search_path TO aquarco, public;
-- WARNING: This drops the entire aquarco schema and ALL data within it!
DROP EXTENSION IF EXISTS pgcrypto;
DROP SCHEMA IF EXISTS aquarco CASCADE;
