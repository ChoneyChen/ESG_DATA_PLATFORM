-- Source-PDF cleanup coordinates with new capture ingestion, without a Plat dependency.
CREATE TABLE esg.capture_source_locks (
    source_key text PRIMARY KEY
);
CREATE TABLE esg.capture_source_reservations (
    reservation_id text PRIMARY KEY,
    scope jsonb NOT NULL,
    source_keys jsonb NOT NULL,
    state text NOT NULL CHECK(state IN ('reserved','quarantined','released','purged')),
    reason text NOT NULL,
    actor text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
