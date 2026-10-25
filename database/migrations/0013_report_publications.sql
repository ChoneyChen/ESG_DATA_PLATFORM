-- Report-scoped publication. A report is the unit a person publishes, withdraws
-- or deletes; the global dataset version stays only as a read compatibility
-- snapshot, never as the control surface.
CREATE TABLE esg.report_publications (
    source_report_id uuid PRIMARY KEY REFERENCES esg.source_report_refs(source_report_id),
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','pending','published','withdrawn','deleted')),
    publication_version integer NOT NULL DEFAULT 0 CHECK (publication_version >= 0),
    published_at timestamptz,
    withdrawn_at timestamptz,
    deleted_at timestamptz,
    operator_ref text,
    reason text,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE esg.report_publication_members (
    source_report_id uuid NOT NULL REFERENCES esg.report_publications(source_report_id),
    publication_version integer NOT NULL,
    observation_id uuid NOT NULL REFERENCES esg.curated_observations(observation_id),
    PRIMARY KEY (source_report_id,publication_version,observation_id)
);

CREATE TABLE esg.report_publication_history (
    history_id uuid PRIMARY KEY,
    source_report_id uuid NOT NULL,
    publication_version integer NOT NULL,
    action text NOT NULL CHECK (action IN ('published','withdrawn','deleted','restored')),
    observation_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    operator_ref text NOT NULL,
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX report_publication_status_idx ON esg.report_publications(status,updated_at DESC);
CREATE INDEX report_publication_history_idx ON esg.report_publication_history(source_report_id,created_at DESC);
CREATE INDEX report_publication_member_idx ON esg.report_publication_members(source_report_id,publication_version);

-- Existing public data was visible through dataset versions. Seed report
-- states from what is actually published today: reports with observations are
-- published, reports with approved contexts but no observation are pending.
WITH report_state AS (
    SELECT r.source_report_id,
        EXISTS (
            SELECT 1 FROM esg.observation_sources os
            JOIN esg.esg_disclosures d USING(disclosure_id)
            WHERE d.source_report_id=r.source_report_id
        ) AS has_observation,
        EXISTS (
            SELECT 1 FROM esg.esg_disclosures d
            JOIN esg.disclosure_standard_mappings m USING(disclosure_id)
            WHERE d.source_report_id=r.source_report_id
              AND d.quality_status='approved' AND m.review_status='approved'
              AND EXISTS (SELECT 1 FROM esg.measurement_contexts c
                          WHERE c.mapping_id=m.disclosure_standard_mapping_id)
        ) AS has_approved
    FROM esg.source_report_refs r
)
INSERT INTO esg.report_publications(source_report_id,status,publication_version,published_at)
SELECT source_report_id,
    CASE WHEN has_observation THEN 'published' WHEN has_approved THEN 'pending' ELSE 'draft' END,
    CASE WHEN has_observation THEN 1 ELSE 0 END,
    CASE WHEN has_observation THEN CURRENT_TIMESTAMP ELSE NULL END
FROM report_state;

INSERT INTO esg.report_publication_members(source_report_id,publication_version,observation_id)
SELECT DISTINCT rp.source_report_id,1,os.observation_id
FROM esg.report_publications rp
JOIN esg.esg_disclosures d ON d.source_report_id=rp.source_report_id
JOIN esg.observation_sources os USING(disclosure_id)
WHERE rp.status='published';
