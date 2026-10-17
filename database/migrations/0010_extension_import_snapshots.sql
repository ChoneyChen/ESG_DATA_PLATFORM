-- Freeze the optional execution overlay beside its immutable base import.
-- Original-only historical batches keep their previous identity unchanged.
CREATE TABLE esg.extension_import_snapshots (
    import_batch_id uuid PRIMARY KEY REFERENCES esg.import_batches(import_batch_id),
    runtime_digest char(64) NOT NULL,
    runtime_snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (jsonb_typeof(runtime_snapshot)='object'),
    CHECK (runtime_snapshot->>'schema_version' IS NOT DISTINCT FROM 'runtime-standard-snapshot-1.0'),
    CHECK (runtime_snapshot->>'runtime_digest' IS NOT DISTINCT FROM runtime_digest::text),
    CHECK (runtime_snapshot ?& ARRAY['parent_package_id','parent_package_version','parent_source_digest','selections','extension_releases']),
    CHECK (jsonb_typeof(runtime_snapshot->'selections') IS NOT DISTINCT FROM 'array'),
    CHECK (jsonb_typeof(runtime_snapshot->'extension_releases') IS NOT DISTINCT FROM 'array')
);
CREATE TABLE esg.extension_import_releases (
    import_batch_id uuid NOT NULL REFERENCES esg.extension_import_snapshots(import_batch_id),
    extension_id text NOT NULL,
    definition_version text NOT NULL,
    release_digest char(64) NOT NULL REFERENCES esg.extension_releases(release_digest),
    include_reported boolean NOT NULL,
    derivation_rule_ids jsonb NOT NULL CHECK (jsonb_typeof(derivation_rule_ids)='array'),
    PRIMARY KEY (import_batch_id,extension_id),
    FOREIGN KEY (extension_id,definition_version) REFERENCES esg.extension_releases(extension_id,definition_version)
);
CREATE FUNCTION esg.check_extension_import_release() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE snapshot jsonb; registered jsonb; selected jsonb;
BEGIN
    SELECT r.release INTO registered FROM esg.extension_releases r WHERE r.extension_id=NEW.extension_id
        AND r.definition_version=NEW.definition_version AND r.release_digest=NEW.release_digest;
    SELECT runtime_snapshot INTO snapshot FROM esg.extension_import_snapshots WHERE import_batch_id=NEW.import_batch_id;
    IF registered IS NULL OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(snapshot->'extension_releases') item WHERE item=registered) THEN
        RAISE EXCEPTION 'extension import release identity mismatch';
    END IF;
    SELECT selection INTO selected FROM jsonb_array_elements(snapshot->'selections') selection
        WHERE selection->>'extension_id'=NEW.extension_id AND selection->>'definition_version'=NEW.definition_version
        AND selection->>'release_digest'=NEW.release_digest
        AND (selection->'value_origins' @> '["reported"]'::jsonb)=NEW.include_reported
        AND selection->'derivation_rule_ids'=NEW.derivation_rule_ids;
    IF selected IS NULL OR jsonb_typeof(selected->'value_origins') IS DISTINCT FROM 'array'
        OR jsonb_array_length(selected->'value_origins')=0 THEN
        RAISE EXCEPTION 'extension import selection differs from frozen runtime snapshot';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(selected->'value_origins') origin WHERE origin IS NULL OR origin NOT IN ('reported','derived')
            OR NOT registered->'definition'->'allowed_value_origins' @> jsonb_build_array(origin))
        OR (SELECT count(DISTINCT origin) FROM jsonb_array_elements_text(selected->'value_origins') origin)<>jsonb_array_length(selected->'value_origins')
        OR (SELECT count(DISTINCT rule_id) FROM jsonb_array_elements_text(NEW.derivation_rule_ids) rule_id)<>jsonb_array_length(NEW.derivation_rule_ids)
        OR (selected->'value_origins' @> '["derived"]'::jsonb AND jsonb_array_length(NEW.derivation_rule_ids)=0)
        OR (NOT selected->'value_origins' @> '["derived"]'::jsonb AND jsonb_array_length(NEW.derivation_rule_ids)>0)
        OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(NEW.derivation_rule_ids) rule_id
            WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(registered->'definition'->'derivation_rules') rule
                WHERE rule->>'rule_id'=rule_id)) THEN
        RAISE EXCEPTION 'extension import selection has unregistered origin or calculation rule';
    END IF;
    RETURN NEW;
END $$;
CREATE FUNCTION esg.check_extension_import_parent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM esg.import_batches batch WHERE batch.import_batch_id=NEW.import_batch_id
        AND batch.package_id=NEW.runtime_snapshot->>'parent_package_id'
        AND batch.package_version=NEW.runtime_snapshot->>'parent_package_version'
        AND batch.package_digest=NEW.runtime_snapshot->>'parent_source_digest') THEN
        RAISE EXCEPTION 'extension import parent differs from immutable base batch';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_extension_import_parent BEFORE INSERT ON esg.extension_import_snapshots
    FOR EACH ROW EXECUTE FUNCTION esg.check_extension_import_parent();
CREATE TRIGGER check_extension_import_release BEFORE INSERT ON esg.extension_import_releases
    FOR EACH ROW EXECUTE FUNCTION esg.check_extension_import_release();
CREATE TRIGGER immutable_extension_import_snapshot BEFORE UPDATE OR DELETE ON esg.extension_import_snapshots
    FOR EACH ROW EXECUTE FUNCTION esg.reject_snapshot_mutation();
CREATE TRIGGER immutable_extension_import_release BEFORE UPDATE OR DELETE ON esg.extension_import_releases
    FOR EACH ROW EXECUTE FUNCTION esg.reject_snapshot_mutation();
