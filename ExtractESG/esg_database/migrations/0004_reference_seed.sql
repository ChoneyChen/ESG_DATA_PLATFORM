INSERT INTO esg.unit_definitions (
    unit_id,
    unit_code,
    symbol,
    unit_family,
    definition,
    status
) VALUES
    (
        '11000000-0000-4000-8000-000000000001',
        'tonne_co2e',
        'tCO2e',
        'ghg_mass',
        'Metric tonne of carbon dioxide equivalent.',
        'active'
    ),
    (
        '11000000-0000-4000-8000-000000000002',
        'cubic_metre',
        'm3',
        'volume',
        'Cubic metre.',
        'active'
    ),
    (
        '11000000-0000-4000-8000-000000000003',
        'megawatt_hour',
        'MWh',
        'energy',
        'Megawatt hour.',
        'active'
    ),
    (
        '11000000-0000-4000-8000-000000000004',
        'percent',
        '%',
        'ratio',
        'Percentage on a 0 to 100 scale.',
        'active'
    ),
    (
        '11000000-0000-4000-8000-000000000005',
        'tonne',
        't',
        'mass',
        'Metric tonne.',
        'active'
    ),
    (
        '11000000-0000-4000-8000-000000000006',
        'hour_per_person',
        'h/person',
        'time_per_person',
        'Hours per person.',
        'active'
    ),
    (
        '11000000-0000-4000-8000-000000000007',
        'incident_per_million_hours',
        'incident/million h',
        'incident_rate',
        'Incidents per one million working hours.',
        'active'
    )
ON CONFLICT (unit_code) DO NOTHING;

INSERT INTO esg.canonical_concepts (
    concept_id,
    concept_code,
    concept_name_zh,
    concept_name_en,
    category,
    disclosure_kind,
    quantity_kind,
    default_unit_family,
    aggregation_type,
    definition,
    status
) VALUES
    ('12000000-0000-4000-8000-000000000001', 'GHG_SCOPE1', '温室气体排放范围一', 'Scope 1 greenhouse gas emissions', 'E', 'quantitative', 'mass', 'ghg_mass', 'additive', 'Direct greenhouse gas emissions reported as Scope 1.', 'proposed'),
    ('12000000-0000-4000-8000-000000000002', 'GHG_SCOPE2', '温室气体排放范围二', 'Scope 2 greenhouse gas emissions', 'E', 'quantitative', 'mass', 'ghg_mass', 'additive', 'Indirect greenhouse gas emissions from purchased energy reported as Scope 2.', 'proposed'),
    ('12000000-0000-4000-8000-000000000003', 'WATER_CONSUMPTION', '水资源消耗', 'Water consumption', 'E', 'quantitative', 'volume', 'volume', 'additive', 'Water consumed during the reporting period, distinct from withdrawal where the report permits.', 'proposed'),
    ('12000000-0000-4000-8000-000000000004', 'ENERGY_CONSUMPTION', '能源消耗总量', 'Total energy consumption', 'E', 'quantitative', 'energy', 'energy', 'additive', 'Energy consumed during the reporting period.', 'proposed'),
    ('12000000-0000-4000-8000-000000000005', 'RENEWABLE_ENERGY_RATIO', '可再生能源占比', 'Renewable energy ratio', 'E', 'quantitative', 'ratio', 'ratio', 'non_additive', 'Share of renewable energy under the disclosed denominator and boundary.', 'proposed'),
    ('12000000-0000-4000-8000-000000000006', 'WASTE_TOTAL', '废弃物总量', 'Total waste generated', 'E', 'quantitative', 'mass', 'mass', 'additive', 'Total waste generated under the disclosed scope.', 'proposed'),
    ('12000000-0000-4000-8000-000000000007', 'EMPLOYEE_TURNOVER', '员工流失率', 'Employee turnover rate', 'S', 'quantitative', 'ratio', 'ratio', 'non_additive', 'Employee turnover rate under the disclosed workforce population and method.', 'proposed'),
    ('12000000-0000-4000-8000-000000000008', 'EMPLOYEE_TRAINING_HOURS', '员工培训时长', 'Employee training hours', 'S', 'quantitative', 'time_per_person', 'time_per_person', 'non_additive', 'Training hours under the disclosed workforce population and denominator.', 'proposed'),
    ('12000000-0000-4000-8000-000000000009', 'FEMALE_MANAGER_RATIO', '女性管理者比例', 'Female manager ratio', 'S', 'quantitative', 'ratio', 'ratio', 'non_additive', 'Share of women in the disclosed management population.', 'proposed'),
    ('12000000-0000-4000-8000-000000000010', 'SAFETY_INCIDENT_RATE', '安全事故率', 'Safety incident rate', 'S', 'quantitative', 'rate', 'incident_rate', 'non_additive', 'Occupational safety incident rate under the disclosed calculation method.', 'proposed'),
    ('12000000-0000-4000-8000-000000000011', 'BOARD_INDEPENDENCE', '董事会独立性', 'Board independence ratio', 'G', 'quantitative', 'ratio', 'ratio', 'non_additive', 'Share of independent directors on the board.', 'proposed'),
    ('12000000-0000-4000-8000-000000000012', 'FEMALE_BOARD_RATIO', '女性董事比例', 'Female board member ratio', 'G', 'quantitative', 'ratio', 'ratio', 'non_additive', 'Share of women among board members.', 'proposed'),
    ('12000000-0000-4000-8000-000000000013', 'ESG_COMMITTEE', 'ESG委员会', 'ESG committee', 'G', 'categorical', 'boolean', NULL, 'not_applicable', 'Whether an ESG or sustainability committee is disclosed as established.', 'proposed'),
    ('12000000-0000-4000-8000-000000000014', 'EXECUTIVE_COMPENSATION', '高管薪酬与ESG挂钩', 'Executive compensation linked to ESG', 'G', 'categorical', 'enum', NULL, 'not_applicable', 'Whether and to what extent executive compensation is linked to ESG performance.', 'proposed'),
    ('12000000-0000-4000-8000-000000000015', 'DATA_SECURITY', '数据安全管理', 'Data security management', 'G', 'qualitative', NULL, NULL, 'not_applicable', 'Disclosed data security governance, policy, process or control.', 'proposed'),
    ('12000000-0000-4000-8000-000000000016', 'SUPPLIER_ESG_ASSESSMENT', '供应商ESG评估', 'Supplier ESG assessment coverage', 'S', 'quantitative', 'ratio', 'ratio', 'non_additive', 'Share of suppliers covered by an ESG assessment under the disclosed denominator.', 'proposed'),
    ('12000000-0000-4000-8000-000000000017', 'FEMALE_EMPLOYEE_RATIO', '女性员工比例', 'Female employee ratio', 'S', 'quantitative', 'ratio', 'ratio', 'non_additive', 'Share of women in a specifically disclosed employee population.', 'proposed'),
    ('12000000-0000-4000-8000-000000000018', 'BOARD_ESG_OVERSIGHT', '董事会ESG监督', 'Board ESG oversight', 'G', 'qualitative', NULL, NULL, 'not_applicable', 'A disclosure about board-level oversight of ESG matters.', 'proposed')
ON CONFLICT (concept_code) DO NOTHING;

INSERT INTO esg.concept_synonyms (
    concept_id,
    language_code,
    synonym,
    synonym_type
) VALUES
    ('12000000-0000-4000-8000-000000000001', 'zh', '范围一排放', 'alias'),
    ('12000000-0000-4000-8000-000000000002', 'zh', '范围二排放', 'alias'),
    ('12000000-0000-4000-8000-000000000017', 'zh', '女性雇员占比', 'report_phrase'),
    ('12000000-0000-4000-8000-000000000018', 'zh', '董事会监督ESG', 'report_phrase')
ON CONFLICT (concept_id, language_code, synonym) DO NOTHING;

INSERT INTO esg.standard_frameworks (
    framework_id,
    framework_code,
    framework_name,
    issuing_body,
    jurisdiction,
    status
) VALUES
    ('13000000-0000-4000-8000-000000000001', 'GRI', 'GRI Standards', 'Global Reporting Initiative', 'Global', 'active'),
    ('13000000-0000-4000-8000-000000000002', 'HKEX', 'HKEX ESG Reporting Guide', 'Hong Kong Exchanges and Clearing Limited', 'Hong Kong', 'active'),
    ('13000000-0000-4000-8000-000000000003', 'IFRS_S', 'IFRS Sustainability Disclosure Standards', 'IFRS Foundation', 'Global', 'active'),
    ('13000000-0000-4000-8000-000000000004', 'ESRS', 'European Sustainability Reporting Standards', 'European Commission / EFRAG', 'European Union', 'active')
ON CONFLICT (framework_code) DO NOTHING;

