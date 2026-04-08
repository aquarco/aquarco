SET search_path TO aquarco, public;
ALTER TABLE stages DROP COLUMN IF EXISTS live_output;
