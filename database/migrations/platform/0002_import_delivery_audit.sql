CREATE TABLE plat.import_receipt_attempts (
    attempt_id uuid PRIMARY KEY,
    receipt_id text NOT NULL REFERENCES plat.import_receipts(receipt_id),
    status text NOT NULL CHECK (status IN ('imported','failed')),
    operator_ref text NOT NULL,
    reason text NOT NULL,
    detail jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE plat.import_stage_events (
    import_batch_id uuid NOT NULL REFERENCES esg.import_batches(import_batch_id),
    manifest_digest char(64) NOT NULL,
    execution_status text NOT NULL,
    result_state text NOT NULL CHECK (result_state IN ('partial','complete')),
    manifest_snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (import_batch_id,manifest_digest)
);
