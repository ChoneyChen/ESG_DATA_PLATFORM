CREATE VIEW esg_api.v_disclosure_dimensions AS
WITH dimension_values AS (
    SELECT
        disclosure_id,
        dimension_key,
        jsonb_agg(
            to_jsonb(COALESCE(canonical_value, raw_value))
            ORDER BY ordinal
        ) AS values
    FROM esg.disclosure_dimensions
    WHERE normalization_status <> 'rejected'
    GROUP BY disclosure_id, dimension_key
)
SELECT
    disclosure_id,
    jsonb_object_agg(dimension_key, values ORDER BY dimension_key) AS dimensions
FROM dimension_values
GROUP BY disclosure_id;

CREATE VIEW esg_api.v_quantitative_observations AS
SELECT
    d.disclosure_id,
    r.organization_ref,
    r.report_ref,
    r.report_version_ref,
    d.concept_id,
    c.concept_code,
    c.concept_name_zh,
    c.category,
    d.raw_indicator_name,
    v.raw_value_text,
    v.numeric_value,
    v.raw_unit,
    v.normalized_numeric_value,
    normalized_unit.unit_code AS normalized_unit_code,
    normalized_unit.symbol AS normalized_unit_symbol,
    d.period_type,
    d.period_start,
    d.period_end,
    d.value_role,
    d.data_nature,
    COALESCE(dim.dimensions, '{}'::jsonb) AS dimensions,
    md5(
        concat_ws(
            '|',
            COALESCE(c.concept_code, 'UNMAPPED'),
            d.period_type,
            COALESCE(normalized_unit.unit_code, v.raw_unit, 'NO_UNIT'),
            COALESCE(dim.dimensions, '{}'::jsonb)::text
        )
    ) AS comparison_signature,
    d.quality_status,
    (
        SELECT array_agg(DISTINCT s.page_number ORDER BY s.page_number)
        FROM esg.disclosure_sources s
        WHERE s.disclosure_id = d.disclosure_id
    ) AS source_pages,
    (
        SELECT s.source_quote
        FROM esg.disclosure_sources s
        WHERE s.disclosure_id = d.disclosure_id
        ORDER BY
            CASE WHEN s.source_role = 'primary' THEN 0 ELSE 1 END,
            s.ordinal
        LIMIT 1
    ) AS primary_source_quote
FROM esg.esg_disclosures d
JOIN esg.source_report_refs r
    ON r.source_report_id = d.source_report_id
JOIN esg.structured_values v
    ON v.disclosure_id = d.disclosure_id
LEFT JOIN esg.canonical_concepts c
    ON c.concept_id = d.concept_id
LEFT JOIN esg.unit_definitions normalized_unit
    ON normalized_unit.unit_id = v.normalized_unit_id
LEFT JOIN esg_api.v_disclosure_dimensions dim
    ON dim.disclosure_id = d.disclosure_id
WHERE
    d.disclosure_type = 'quantitative'
    AND v.value_type = 'numeric'
    AND d.quality_status = 'approved';

CREATE VIEW esg_api.v_categorical_observations AS
SELECT
    d.disclosure_id,
    r.organization_ref,
    r.report_ref,
    r.report_version_ref,
    d.concept_id,
    c.concept_code,
    c.concept_name_zh,
    c.category,
    d.raw_indicator_name,
    v.raw_value_text,
    v.value_type,
    v.boolean_value,
    v.enum_code,
    v.text_value,
    d.period_start,
    d.period_end,
    d.value_role,
    COALESCE(dim.dimensions, '{}'::jsonb) AS dimensions,
    d.quality_status
FROM esg.esg_disclosures d
JOIN esg.source_report_refs r
    ON r.source_report_id = d.source_report_id
JOIN esg.structured_values v
    ON v.disclosure_id = d.disclosure_id
LEFT JOIN esg.canonical_concepts c
    ON c.concept_id = d.concept_id
LEFT JOIN esg_api.v_disclosure_dimensions dim
    ON dim.disclosure_id = d.disclosure_id
WHERE
    d.disclosure_type = 'categorical'
    AND v.value_type IN ('boolean', 'enum', 'text')
    AND d.quality_status = 'approved';

CREATE VIEW esg_api.v_qualitative_disclosures AS
SELECT
    d.disclosure_id,
    r.organization_ref,
    r.report_ref,
    r.report_version_ref,
    d.concept_id,
    c.concept_code,
    c.concept_name_zh,
    c.category,
    q.claim_type,
    q.subject_text,
    q.predicate_text,
    q.object_text,
    q.claim_status,
    q.effective_date,
    q.target_date,
    q.frequency_text,
    q.qualifiers,
    q.summary_text,
    COALESCE(dim.dimensions, '{}'::jsonb) AS dimensions,
    d.quality_status,
    (
        SELECT array_agg(DISTINCT s.page_number ORDER BY s.page_number)
        FROM esg.disclosure_sources s
        WHERE s.disclosure_id = d.disclosure_id
    ) AS source_pages
FROM esg.esg_disclosures d
JOIN esg.source_report_refs r
    ON r.source_report_id = d.source_report_id
JOIN esg.qualitative_claims q
    ON q.disclosure_id = d.disclosure_id
LEFT JOIN esg.canonical_concepts c
    ON c.concept_id = d.concept_id
LEFT JOIN esg_api.v_disclosure_dimensions dim
    ON dim.disclosure_id = d.disclosure_id
WHERE
    d.disclosure_type = 'qualitative'
    AND d.quality_status = 'approved';

CREATE VIEW esg_api.v_standard_disclosures AS
SELECT
    m.disclosure_standard_mapping_id,
    m.disclosure_id,
    r.organization_ref,
    r.report_ref,
    f.framework_code,
    fv.version_label AS framework_version,
    req.requirement_code,
    req.requirement_title,
    m.mapping_relation,
    m.satisfaction_status,
    m.mapping_reason,
    m.review_status
FROM esg.disclosure_standard_mappings m
JOIN esg.esg_disclosures d
    ON d.disclosure_id = m.disclosure_id
JOIN esg.source_report_refs r
    ON r.source_report_id = d.source_report_id
JOIN esg.standard_requirements req
    ON req.requirement_id = m.requirement_id
JOIN esg.framework_versions fv
    ON fv.framework_version_id = req.framework_version_id
JOIN esg.standard_frameworks f
    ON f.framework_id = fv.framework_id
WHERE
    d.quality_status = 'approved'
    AND m.review_status = 'approved';

