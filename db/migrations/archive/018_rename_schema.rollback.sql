SET search_path TO aquarco, public;
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.schemata WHERE schema_name = 'aquarco'
  ) THEN
    ALTER SCHEMA aquarco RENAME TO aifishtank;
  END IF;
END
$$;
