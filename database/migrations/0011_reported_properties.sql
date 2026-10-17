-- Supplemental scientific descriptions are reviewed and frozen separately
-- from the normalized identity axes used for publication and comparison.
ALTER TABLE esg.measurement_contexts
    ADD COLUMN reported_properties jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(reported_properties) = 'array');
-- The existing immutable_measurement_context trigger also protects this column.
