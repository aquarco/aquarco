-- Rollback migration 039: Remove msg_spending_state column from stages

SET search_path TO aquarco, public;

ALTER TABLE stages DROP COLUMN IF EXISTS msg_spending_state;
