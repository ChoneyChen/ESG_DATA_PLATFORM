-- Serialize candidate import with source quarantine. Reservations are durable:
-- an interrupted cleanup must be recovered, never silently allowed to expire.
CREATE TABLE plat.artifact_lifecycle_mutex (
    mutex_id integer PRIMARY KEY CHECK (mutex_id = 1)
);
INSERT INTO plat.artifact_lifecycle_mutex VALUES (1);

CREATE TABLE plat.artifact_lifecycle_reservations (
    reservation_id uuid PRIMARY KEY,
    scope jsonb NOT NULL,
    state text NOT NULL CHECK (state IN ('reserved','quarantined','purged','released')),
    reason text NOT NULL CHECK (length(trim(reason)) > 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
