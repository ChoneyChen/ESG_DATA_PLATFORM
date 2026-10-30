-- Import-only provenance is independent of the active publication dictionary.
CREATE TABLE plat.import_contract_releases (
    contract_id text PRIMARY KEY,
    permission text NOT NULL CHECK (permission='import_only'),
    release_sha256 char(64) NOT NULL,
    dictionary_version text NOT NULL,
    dictionary_file_sha256 char(64) NOT NULL,
    profile_id text NOT NULL,
    profile_version text NOT NULL,
    profile_file_sha256 char(64) NOT NULL,
    ready_source_datapoint_ids text[] NOT NULL,
    package_bindings jsonb NOT NULL,
    dictionary_snapshot jsonb NOT NULL,
    profile_snapshot jsonb NOT NULL,
    release_snapshot jsonb NOT NULL,
    operator_ref text NOT NULL CHECK (length(trim(operator_ref))>0),
    reason text NOT NULL CHECK (length(trim(reason))>0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dictionary_version,dictionary_file_sha256,profile_id,profile_version,profile_file_sha256)
);
CREATE FUNCTION plat.protect_import_contract() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'immutable import-only contract';
END $$;
CREATE TRIGGER immutable_import_contract BEFORE UPDATE OR DELETE ON plat.import_contract_releases
    FOR EACH ROW EXECUTE FUNCTION plat.protect_import_contract();
