-- A selection preview owns only decisions and provenance, never copied observations.
CREATE TABLE esg.capture_dataset_previews (
    preview_digest text PRIMARY KEY,
    request_digest text NOT NULL,
    snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
