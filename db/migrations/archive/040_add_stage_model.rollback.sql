-- depends: 040_add_stage_model

SET search_path TO aquarco, public;

ALTER TABLE stages
  DROP COLUMN IF EXISTS model;
