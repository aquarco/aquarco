-- depends: 040_add_stage_model
-- Migration 041: Backfill stages.model from raw_output NDJSON
--
-- For stages completed before migration 040 added the model column,
-- extract the Claude model identifier from the NDJSON raw_output.
-- Priority: system init message > first assistant message model field.
-- Stages with NULL raw_output or no model in NDJSON remain NULL.

SET search_path TO aquarco, public;

DO $$
DECLARE
    r       RECORD;
    line    TEXT;
    j       JSONB;
    model_val TEXT;
BEGIN
    FOR r IN
        SELECT id, raw_output
        FROM stages
        WHERE model IS NULL
          AND raw_output IS NOT NULL
          AND raw_output <> ''
    LOOP
        model_val := NULL;

        -- Pass 1: look for system init message
        FOREACH line IN ARRAY string_to_array(r.raw_output, E'\n')
        LOOP
            line := trim(line);
            CONTINUE WHEN line = '';
            BEGIN
                j := line::jsonb;
                IF j->>'type' = 'system'
                   AND j->>'subtype' = 'init'
                   AND j->>'model' IS NOT NULL
                THEN
                    model_val := j->>'model';
                    EXIT;
                END IF;
            EXCEPTION WHEN OTHERS THEN
                CONTINUE;
            END;
        END LOOP;

        -- Pass 2: fall back to first assistant message
        IF model_val IS NULL THEN
            FOREACH line IN ARRAY string_to_array(r.raw_output, E'\n')
            LOOP
                line := trim(line);
                CONTINUE WHEN line = '';
                BEGIN
                    j := line::jsonb;
                    IF j->>'type' = 'assistant'
                       AND j->'message'->>'model' IS NOT NULL
                    THEN
                        model_val := j->'message'->>'model';
                        EXIT;
                    END IF;
                EXCEPTION WHEN OTHERS THEN
                    CONTINUE;
                END;
            END LOOP;
        END IF;

        IF model_val IS NOT NULL THEN
            UPDATE stages SET model = model_val WHERE id = r.id;
        END IF;
    END LOOP;
END;
$$;
