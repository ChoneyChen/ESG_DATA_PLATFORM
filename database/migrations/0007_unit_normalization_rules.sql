CREATE TABLE esg.normalization_rule_versions (
    rule_version text PRIMARY KEY,
    rule_digest char(64) NOT NULL,
    definition jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER immutable_normalization_rule
    BEFORE UPDATE OR DELETE ON esg.normalization_rule_versions
    FOR EACH ROW EXECUTE FUNCTION esg.reject_snapshot_mutation();
ALTER TABLE esg.outbox_events DROP CONSTRAINT outbox_events_event_type_check;
ALTER TABLE esg.outbox_events ADD CONSTRAINT outbox_events_event_type_check
    CHECK (event_type IN ('review','context','dictionary','normalization'));
