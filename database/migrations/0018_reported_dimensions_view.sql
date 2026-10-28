-- The printed classification beside the clause classification.
--
-- esg_api.v_disclosure_dimensions keeps COALESCE(canonical_value, raw_value) so
-- the frozen comparison identity of existing API rows does not change. A report
-- that prints 风能 while the clause fixes energy_source=renewable needs both
-- readings available, so this view exposes the unmodified printed values for
-- every disclosure that has them.
CREATE OR REPLACE VIEW esg_api.v_reported_disclosure_dimensions AS
WITH dimension_values AS (
    SELECT
        disclosure_id,
        dimension_key,
        jsonb_agg(to_jsonb(raw_value) ORDER BY ordinal) AS values
    FROM esg.disclosure_dimensions
    WHERE normalization_status <> 'rejected'
      AND raw_value IS NOT NULL
      AND raw_value <> ''
    GROUP BY disclosure_id, dimension_key
)
SELECT
    disclosure_id,
    jsonb_object_agg(dimension_key, values ORDER BY dimension_key) AS reported_dimensions
FROM dimension_values
GROUP BY disclosure_id;

COMMENT ON VIEW esg_api.v_reported_disclosure_dimensions IS
    '报告原印分类：按原词保存，口径比较仍用 v_disclosure_dimensions';
