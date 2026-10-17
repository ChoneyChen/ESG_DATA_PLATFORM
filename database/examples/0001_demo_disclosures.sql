INSERT INTO esg.source_report_refs (
    source_report_id,
    upstream_system,
    organization_ref,
    report_ref,
    report_version_ref,
    source_artifact_ref,
    source_sha256,
    report_title_snapshot,
    report_period_start,
    report_period_end,
    publication_date,
    language_code
) VALUES (
    '21000000-0000-4000-8000-000000000001',
    'demo-upstream',
    'organization-demo-001',
    'report-demo-2024',
    'report-demo-2024-v1',
    'kodo://demo/reports/report-demo-2024.pdf',
    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    '示例企业2024年度ESG报告',
    DATE '2024-01-01',
    DATE '2024-12-31',
    DATE '2025-04-30',
    'zh'
) ON CONFLICT (upstream_system, report_version_ref) DO NOTHING;

INSERT INTO esg.esg_disclosures (
    disclosure_id,
    source_report_id,
    concept_id,
    disclosure_type,
    raw_indicator_name,
    raw_disclosure_text,
    period_type,
    period_start,
    period_end,
    value_role,
    data_nature,
    mapping_status,
    quality_status,
    extraction_version,
    source_ir_run_id,
    source_ir_revision
) VALUES
    (
        '31000000-0000-4000-8000-000000000001',
        '21000000-0000-4000-8000-000000000001',
        '12000000-0000-4000-8000-000000000017',
        'quantitative',
        '女性员工占比',
        '2024年集团女性员工占比为42%。',
        'duration',
        DATE '2024-01-01',
        DATE '2024-12-31',
        'actual',
        'reported',
        'mapped',
        'approved',
        'demo-extractor-v1',
        'ir-demo-001',
        1
    ),
    (
        '31000000-0000-4000-8000-000000000002',
        '21000000-0000-4000-8000-000000000001',
        '12000000-0000-4000-8000-000000000017',
        'quantitative',
        '生产车间女性员工比例',
        '生产车间女性员工比例为31%。',
        'duration',
        DATE '2024-01-01',
        DATE '2024-12-31',
        'actual',
        'reported',
        'mapped',
        'approved',
        'demo-extractor-v1',
        'ir-demo-001',
        1
    ),
    (
        '31000000-0000-4000-8000-000000000003',
        '21000000-0000-4000-8000-000000000001',
        '12000000-0000-4000-8000-000000000017',
        'quantitative',
        '行政人员女性员工比例',
        '行政人员女性员工比例为55%。',
        'duration',
        DATE '2024-01-01',
        DATE '2024-12-31',
        'actual',
        'reported',
        'mapped',
        'approved',
        'demo-extractor-v1',
        'ir-demo-001',
        1
    ),
    (
        '31000000-0000-4000-8000-000000000004',
        '21000000-0000-4000-8000-000000000001',
        '12000000-0000-4000-8000-000000000009',
        'quantitative',
        '女性管理者比例',
        '女性管理者占管理人员总数的26%。',
        'duration',
        DATE '2024-01-01',
        DATE '2024-12-31',
        'actual',
        'reported',
        'mapped',
        'approved',
        'demo-extractor-v1',
        'ir-demo-001',
        1
    ),
    (
        '31000000-0000-4000-8000-000000000005',
        '21000000-0000-4000-8000-000000000001',
        '12000000-0000-4000-8000-000000000018',
        'qualitative',
        '董事会ESG监督',
        '董事会每季度审阅集团气候相关风险。',
        'duration',
        DATE '2024-01-01',
        DATE '2024-12-31',
        'actual',
        'reported',
        'mapped',
        'approved',
        'demo-extractor-v1',
        'ir-demo-001',
        1
    )
ON CONFLICT (disclosure_id) DO NOTHING;

INSERT INTO esg.structured_values (
    disclosure_id,
    value_type,
    raw_value_text,
    numeric_value,
    raw_unit,
    unit_id,
    normalized_numeric_value,
    normalized_unit_id,
    scale_factor,
    normalization_status,
    normalization_rule_version
) VALUES
    ('31000000-0000-4000-8000-000000000001', 'numeric', '42%', 42, '%', '11000000-0000-4000-8000-000000000004', 42, '11000000-0000-4000-8000-000000000004', 1, 'normalized', 'demo-unit-rules-v1'),
    ('31000000-0000-4000-8000-000000000002', 'numeric', '31%', 31, '%', '11000000-0000-4000-8000-000000000004', 31, '11000000-0000-4000-8000-000000000004', 1, 'normalized', 'demo-unit-rules-v1'),
    ('31000000-0000-4000-8000-000000000003', 'numeric', '55%', 55, '%', '11000000-0000-4000-8000-000000000004', 55, '11000000-0000-4000-8000-000000000004', 1, 'normalized', 'demo-unit-rules-v1'),
    ('31000000-0000-4000-8000-000000000004', 'numeric', '26%', 26, '%', '11000000-0000-4000-8000-000000000004', 26, '11000000-0000-4000-8000-000000000004', 1, 'normalized', 'demo-unit-rules-v1')
ON CONFLICT (disclosure_id) DO NOTHING;

INSERT INTO esg.disclosure_dimensions (
    disclosure_dimension_id,
    disclosure_id,
    dimension_key,
    ordinal,
    raw_value,
    canonical_value,
    normalization_status
) VALUES
    ('41000000-0000-4000-8000-000000000001', '31000000-0000-4000-8000-000000000001', 'organization_boundary', 1, '集团', 'group', 'normalized'),
    ('41000000-0000-4000-8000-000000000002', '31000000-0000-4000-8000-000000000001', 'workforce_segment', 1, '全体员工', 'all_employees', 'normalized'),
    ('41000000-0000-4000-8000-000000000003', '31000000-0000-4000-8000-000000000001', 'gender', 1, '女性', 'female', 'normalized'),
    ('41000000-0000-4000-8000-000000000004', '31000000-0000-4000-8000-000000000002', 'organization_boundary', 1, '集团', 'group', 'normalized'),
    ('41000000-0000-4000-8000-000000000005', '31000000-0000-4000-8000-000000000002', 'workforce_segment', 1, '生产车间员工', 'production_workshop', 'normalized'),
    ('41000000-0000-4000-8000-000000000006', '31000000-0000-4000-8000-000000000002', 'gender', 1, '女性', 'female', 'normalized'),
    ('41000000-0000-4000-8000-000000000007', '31000000-0000-4000-8000-000000000003', 'organization_boundary', 1, '集团', 'group', 'normalized'),
    ('41000000-0000-4000-8000-000000000008', '31000000-0000-4000-8000-000000000003', 'workforce_segment', 1, '行政人员', 'administrative_staff', 'normalized'),
    ('41000000-0000-4000-8000-000000000009', '31000000-0000-4000-8000-000000000003', 'gender', 1, '女性', 'female', 'normalized'),
    ('41000000-0000-4000-8000-000000000010', '31000000-0000-4000-8000-000000000004', 'organization_boundary', 1, '集团', 'group', 'normalized'),
    ('41000000-0000-4000-8000-000000000011', '31000000-0000-4000-8000-000000000004', 'workforce_segment', 1, '管理人员', 'management', 'normalized'),
    ('41000000-0000-4000-8000-000000000012', '31000000-0000-4000-8000-000000000004', 'gender', 1, '女性', 'female', 'normalized'),
    ('41000000-0000-4000-8000-000000000013', '31000000-0000-4000-8000-000000000005', 'organization_boundary', 1, '集团', 'group', 'normalized')
ON CONFLICT (disclosure_dimension_id) DO NOTHING;

INSERT INTO esg.qualitative_claims (
    disclosure_id,
    claim_type,
    subject_text,
    predicate_text,
    object_text,
    claim_status,
    frequency_text,
    qualifiers,
    summary_text
) VALUES (
    '31000000-0000-4000-8000-000000000005',
    'governance',
    '董事会',
    '审阅',
    '集团气候相关风险',
    'implemented',
    '每季度',
    '{"scope": "集团"}'::jsonb,
    '董事会按季度审阅集团气候相关风险。'
) ON CONFLICT (disclosure_id) DO NOTHING;

INSERT INTO esg.disclosure_sources (
    disclosure_source_id,
    disclosure_id,
    source_role,
    page_number,
    printed_page_label,
    source_node_id,
    source_quote,
    ordinal
) VALUES
    ('51000000-0000-4000-8000-000000000001', '31000000-0000-4000-8000-000000000001', 'primary', 10, '8', 'cell-demo-001', '集团女性员工占比为42%', 1),
    ('51000000-0000-4000-8000-000000000002', '31000000-0000-4000-8000-000000000002', 'primary', 10, '8', 'cell-demo-002', '生产车间女性员工比例为31%', 1),
    ('51000000-0000-4000-8000-000000000003', '31000000-0000-4000-8000-000000000003', 'primary', 10, '8', 'cell-demo-003', '行政人员女性员工比例为55%', 1),
    ('51000000-0000-4000-8000-000000000004', '31000000-0000-4000-8000-000000000004', 'primary', 10, '8', 'cell-demo-004', '女性管理者占管理人员总数的26%', 1),
    ('51000000-0000-4000-8000-000000000005', '31000000-0000-4000-8000-000000000005', 'primary', 22, '20', 'block-demo-005', '董事会每季度审阅集团气候相关风险。', 1)
ON CONFLICT (disclosure_source_id) DO NOTHING;

