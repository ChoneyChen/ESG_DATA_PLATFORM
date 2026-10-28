-- A clause version belongs to an immutable package version. The stored contract
-- digest is a projection of that package; recompiling the same package with a
-- fixed compiler may change the projection without changing the clause.
ALTER TABLE esg.clause_versions ADD COLUMN IF NOT EXISTS package_source_digest text;
