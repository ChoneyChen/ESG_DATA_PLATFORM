-- List cleanup is separate from immutable observations and published membership.
CREATE TABLE esg.capture_projection_archives (
    projection_id text PRIMARY KEY REFERENCES esg.capture_projections(projection_id),
    archived_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor text NOT NULL
);
