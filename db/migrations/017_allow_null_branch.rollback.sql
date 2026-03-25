SET search_path TO aquarco, public;
UPDATE repositories SET branch = 'main' WHERE branch IS NULL;
ALTER TABLE repositories ALTER COLUMN branch SET NOT NULL;
ALTER TABLE repositories ALTER COLUMN branch SET DEFAULT 'main';
