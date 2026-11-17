from esg_contracts.evidence import ObjectSearchPage
"""Scientific counterexamples: provenance alone cannot make a digit a quantity."""
import pytest
from esg_contracts.evidence import TextEvidence, EvidenceFrame, EvidenceCell, EvidenceRow
from esg_contracts.facts import RunRequest, ReportedField
from esg_extraction.pipeline.engine import ExtractionBackbone
from esg_extraction.pipeline.binding import context_from_claims, mapping_for
from esg_extraction.pipeline.decisions import IdentityProposal
from esg_extraction.pipeline.adapters.artifacts import FileArtifactPort
from esg_document.document.literals import numeric_rows
from test_backbone import repository, documents, Model, quantitative, run_backbone
from test_organization import fact, mapping, reported


@pytest.mark.parametrize('text,raw,kind', [
    ('公司使用六氟化硫（SF6）气体设置报警装置。', '6', 'formula'),
    ('2由于子公司改进，氮氧化物排放量下降。', '2', 'footnote'),
    ('共有2家工厂实施监测。', '2', 'unresolved'),
    ('报告期排放0吨。', '0', 'measurement'),
    ('报告值为1-2吨。', '1-2', 'measurement'),
    ('2025年排放20吨。', '2025', 'date'),
])
def test_literal_typing_keeps_true_unitless_counts(text, raw, kind):
    rows = numeric_rows(TextEvidence(object_id='p', text=text, page_index=0))
    assert next(c for r in rows for c in r.cells if c.raw_text == raw).candidate_kind == kind


def test_semantic_digit_rejections_never_become_facts(documents, repository, tmp_path):
    clause = quantitative(repository)
    clause = clause.model_copy(update={'purposes':clause.purposes.model_copy(update={
        'enrichment':clause.purposes.enrichment.model_copy(update={'identity_axes':['reporting_period']})})}).seal()
    texts = ['SF6气体报警', '2由于子公司改进，污染物排放量下降。', '工厂数量2']
    frames = [EvidenceFrame(object_id=str(i), kind='text', page_index=i,
        rows=numeric_rows(TextEvidence(object_id=str(i), text=text, page_index=i)), digest=str(i)) for i,text in enumerate(texts)]
    class Docs:
        document = documents.document
        def search_page(self, *args):
            from esg_contracts.evidence import ObjectReference
            objects = [ObjectReference(object_id=f.object_id, kind='text', page_index=f.page_index, score=1) for f in frames]
            return ObjectSearchPage(objects=objects,total=len(objects),returned=len(objects),query_digest='fixed',scope={'available_pages':[0,1,2]})
        def read(self, run_id, candidate, chunk): return frames[int(candidate.object_id)]
    class Reader(Model):
        def complete(self, **kwargs):
            self.calls.append(kwargs)
            return {'matches': [{'row_id':r['row_id'], 'columns':['value']} for r in kwargs['context']['frame']['rows']]}, {}
    model = Reader(); artifacts=FileArtifactPort(tmp_path/'out')
    result=run_backbone(Docs(), model, artifacts, 'test', RunRequest(ir_run_id='ir-example', clause_keys=[clause.clause_key]), {clause.clause_key:clause})
    assert [f['value']['value_raw'] for f in result['facts']] == ['2']
    assert len(model.calls) == 1
    assert len(artifacts.read('test','diagnostics/candidates.json')) == 2
    assessment=result['summary']['assessment']
    assert assessment['execution_state']=='completed'
    assert assessment['metadata_completeness']=='partial'
    assert assessment['rejected_candidates']==2


def test_final_bound_identity_resolves_prior_attempt_warnings():
    source=TextEvidence(object_id='text',text='2025 SO2',page_index=0)
    frame=EvidenceFrame(object_id='frame',kind='text',page_index=0,digest='x',texts=[source])
    attempts=[]
    context, flags=context_from_claims(frame,[
        IdentityProposal(axis='pollutant',value='SO2',quote='invented',source_id='text'),
        IdentityProposal(axis='pollutant',value='SO2',quote='SO2',source_id='text'),
    ],attempts=attempts)
    assert context.dimensions['pollutant'].value=='SO2'
    assert 'unbound_identity:pollutant' not in flags
    assert 'unbound_identity:pollutant' in attempts


def test_sparse_context_is_received_but_known_medium_conflict_is_separate(documents, repository):
    document=documents.document('ir-example');clause=repository.get('E2-4_02')
    from esg_data.categories import VocabularyEntry
    medium=clause.attributes['pollution_medium'].model_copy(update={'vocabulary':[
        VocabularyEntry(code='air',label='空气',aliases=['air']),
        VocabularyEntry(code='water',label='水体',aliases=['water']),
    ]})
    clause=clause.model_copy(update={'attributes':{**clause.attributes,'pollution_medium':medium}}).seal()
    sparse=fact(document,'sparse','1,530.14')
    link=mapping_for(clause,document,sparse,'match','air pollutant')
    assert link.match=='match' and not link.scope_ids
    water=fact(document,'water','175.61',dimensions={'pollution_medium':reported('water')})
    link=mapping_for(clause,document,water,'match','water measurement')
    assert link.match=='different'


def test_matrix_coordinates_ignore_inherited_vs_stated_without_losing_rows(documents,repository):
    from esg_extraction.pipeline.organization import build_organized_dataset
    document=documents.document('ir-example');clause=repository.get('E2-4_02')
    one=fact(document,'one','1',dimensions={'pollutant':reported('SO2')})
    two=fact(document,'two','2',dimensions={'pollutant':reported('SO2')})
    two=two.model_copy(update={'identity':two.identity.model_copy(update={'reporting_period':two.identity.reporting_period.model_copy(update={'state':'inherited'})})})
    table=build_organized_dataset('run',document,{clause.clause_key:clause},[one,two],[mapping(clause,f) for f in [one,two]]).tables[0]
    assert len(table.matrix.columns)==1
    assert len(table.matrix.cells[0].row_ids)==2
    assert len(table.rows)==2


@pytest.mark.parametrize('raw', ['吨','SF6','CO2','排放量','—','未披露'])
def test_even_a_selected_table_label_cannot_become_a_quantity(raw,documents,repository):
    from esg_extraction.pipeline.binding import numeric_fact
    from esg_extraction.pipeline.decisions import NumericMatch
    clause=quantitative(repository)
    cell=EvidenceCell(cell_id='cell',column_id='value',raw_text=raw)
    row=EvidenceRow(row_id='row',label='指标',cells=[cell])
    frame=EvidenceFrame(object_id='table',kind='table',page_index=0,rows=[row],digest='frame')
    result=numeric_fact(clause,documents.document('ir-example'),frame,NumericMatch(row_id='row',columns=['value'],reason='proposed',selection='measurement'),row,cell,'test')
    assert result[0] is None and result[2]


def test_unit_from_a_physical_table_title_is_accepted(documents,repository):
    from esg_extraction.pipeline.binding import numeric_fact
    from esg_extraction.pipeline.decisions import NumericMatch
    clause=quantitative(repository)
    cell=EvidenceCell(cell_id='cell',column_id='value',raw_text='12.3')
    row=EvidenceRow(row_id='row',label='综合能源消耗量',cells=[cell])
    title=TextEvidence(object_id='printed-title',text='能源消耗（MWh）',page_index=0)
    frame=EvidenceFrame(object_id='table',kind='table',page_index=0,title=title.text,title_sources=[title],rows=[row],digest='frame')
    fact,link,_=numeric_fact(clause,documents.document('ir-example'),frame,NumericMatch(row_id='row',columns=['value'],reason='direct value',selection='measurement'),row,cell,'test')
    assert fact.value.unit_raw=='MWh'
    assert not any(flag.startswith('clause_evidence_missing:unit:') for flag in link.flags)


def test_other_real_measurement_survives_the_model_boundary(documents, repository, tmp_path):
    clause = quantitative(repository)
    class Reader(Model):
        def complete(self, **kwargs):
            rows = kwargs['context']['frame']['rows']
            return {'matches': [{'row_id': row['row_id'], 'columns': ['c1'],
                'selection': 'measurement', 'match': 'different',
                'reason': 'A real measurement outside this target'} for row in rows]}, {}
    result = run_backbone(documents, Reader(), FileArtifactPort(tmp_path/'out'),
        'other-measurements', RunRequest(ir_run_id='ir-example', clause_keys=[clause.clause_key]),
        {clause.clause_key: clause})
    assert len(result['facts']) == 3
    assert any(fact['identity']['reporting_period']['value']=='2024' for fact in result['facts'])
    assert all(link['match'] == 'different' for link in result['clause_mappings'])
    assert result['summary']['assessment']['other_facts'] == 3
    assert result['summary']['assessment']['execution_state'] == 'completed'


def test_metadata_completeness_consumes_enrichment_without_display_columns(documents, repository):
    from esg_extraction.pipeline.statistics import RunStatistics
    clause = repository.get('E2-4_02')
    clause = clause.model_copy(update={'purposes': clause.purposes.model_copy(update={
        'enrichment': clause.purposes.enrichment.model_copy(update={'identity_axes': ['new_optional_axis']})})}).seal()
    source = fact(documents.document('ir-example'), 'sparse', '12.3')
    statistics = RunStatistics({clause.clause_key: clause})
    statistics.record(source, mapping(clause, source))
    result = statistics.summary([], [], True)['assessment']
    assert result['metadata_completeness'] == 'partial'
    assert source.value.value_raw == '12.3'


@pytest.mark.parametrize('role,quote,raw,year,expected_updates,expected_relation',[
    ('actual','Renewable Energy %','100%','2030',0,'match'),
    (['actual','target'],'Actual value and target are both 100%.','100%','2030',0,'match'),
    ('actual','Actual renewable energy share was 34% in 2024.','34%','2024',1,'match'),
    ('target','We are committed to powering our global operations with 100% renewable energy by 2030.','100%','2030',1,'different'),
    ('target','We target 100% renewable energy by 2030, with an interim target of 82% by 2028.','50%','2025',0,'match'),
    ('target','We target 100% renewable energy by 2030, with an interim target of 82% by 2028.','82%','2028',1,'different'),
    ('target','We target 100% renewable energy by 2030, with an interim target of 82% by 2028.','100%','2030',1,'different'),
    ('target','目标为二〇三〇年达到100%。','100%','2030',1,'different'),
    ('target','The target applies to all disclosed periods.','50%','2025',1,'different'),
])
def test_enrichment_category_requires_a_quote_supporting_the_claimed_role(
    documents,repository,role,quote,raw,year,expected_updates,expected_relation
):
    from esg_extraction.pipeline.enrichment import apply_context,ContextDecision
    document=documents.document('ir-example');clause=repository.get('E1-5_09')
    source=fact(document,'role-proof',raw,year)
    facts={source.source_fact_id:source};mappings={source.source_fact_id:mapping(clause,source)}
    evidence=TextEvidence(object_id='role-passage',span_id='c1',text=quote,page_index=2,source_snapshot=document.snapshot_id)
    frame=EvidenceFrame(object_id='context',kind='text',page_index=2,digest='role-context',texts=[evidence])
    decision=ContextDecision.model_validate({'assignments':[{'fact_ids':['f1'],'identity':[
        {'axis':'value_role','value':role,'quote':quote,'source_id':'c1'}]}]})
    observation={'requested_attributes':['value_role'],'source_levels':{'c1':'table'},
        'fact_sources':{source.source_fact_id:['c1']},'report_sources':[],'report_attributes':[]}

    count,changed=apply_context(clause,{document.snapshot_id:document},frame,decision,
        facts,mappings,{'f1':source.source_fact_id},observation)

    assert count==expected_updates and bool(changed)==bool(expected_updates)
    assert mappings[source.source_fact_id].match==expected_relation
    assert facts[source.source_fact_id].value.value_raw==raw
    field=facts[source.source_fact_id].identity.value_role
    if expected_updates:
        assert field.value==role and field.raw_text==quote
        assert field.evidence[0].object_id=='role-passage'
    else:
        assert field.state=='not_read'


@pytest.mark.parametrize('actual_quote,expected_state,expected_relation',[
    ('Renewable Energy %','stated','different'),
    ('Actual renewable share in 2030 was 100%.','conflict','uncertain'),
])
def test_each_role_proposal_is_proven_before_combining_claims(
    documents,repository,actual_quote,expected_state,expected_relation
):
    from esg_extraction.pipeline.enrichment import apply_context,ContextDecision
    document=documents.document('ir-example');clause=repository.get('E1-5_09')
    source=fact(document,'mixed-role','100%','2030')
    target_quote='Target renewable share for 2030 is 100%.'
    passage=TextEvidence(object_id='role-passage',span_id='c1',text=actual_quote+' '+target_quote,
        page_index=2,source_snapshot=document.snapshot_id)
    frame=EvidenceFrame(object_id='context',kind='text',page_index=2,digest='mixed-role',texts=[passage])
    claims=[{'axis':'value_role','value':role,'quote':quote,'source_id':'c1'}
        for role,quote in [('actual',actual_quote),('target',target_quote)]]
    decision=ContextDecision.model_validate({'assignments':[{'fact_ids':['f1'],'identity':claims}]})
    facts={source.source_fact_id:source};mappings={source.source_fact_id:mapping(clause,source)}
    observation={'requested_attributes':['value_role'],'source_levels':{'c1':'table'},
        'fact_sources':{source.source_fact_id:['c1']},'report_sources':[],'report_attributes':[]}
    apply_context(clause,{document.snapshot_id:document},frame,decision,
        facts,mappings,{'f1':source.source_fact_id},observation)
    assert facts[source.source_fact_id].identity.value_role.state==expected_state
    assert mappings[source.source_fact_id].match==expected_relation
    assert facts[source.source_fact_id].value.value_raw=='100%'


def test_role_schema_requires_one_fact_per_assignment():
    import jsonschema
    from esg_extraction.pipeline.enrichment import context_schema
    frame=EvidenceFrame(object_id='context',kind='text',page_index=0,digest='schema',
        texts=[TextEvidence(object_id='passage',span_id='c1',text='Target is 100%.',page_index=0)])
    grouped={'assignments':[{'fact_ids':['f1','f2'],'identity':[
        {'axis':'value_role','value':'target','quote':'Target is 100%.','source_id':'c1'}]}]}
    schema=context_schema(frame,{'f1':'one','f2':'two'},['value_role'])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(grouped,schema)
    grouped['assignments'][0]['fact_ids']=['f2']
    jsonschema.validate(grouped,schema)


@pytest.mark.parametrize('value,quote,expected',[
    ('group operations','Coverage includes Group\n Operations.', 'Group\n Operations'),
    ('统计范围','統計範圍涵蓋本公司。','統計範圍'),
    (2028,'The year is 2028.',2028),
    (False,'Verified: FALSE.',False),
    ({'subject':'group','counts':[2,True]},'Group has 2 entries; TRUE.',{'subject':'Group','counts':[2,True]}),
])
def test_attribute_literals_bind_source_spelling_and_preserve_leaf_types(value,quote,expected):
    from esg_extraction.pipeline.enrichment import bind_attribute_literal
    actual=bind_attribute_literal(value,quote)
    assert actual==expected and type(actual) is type(expected)
    if isinstance(value,dict):
        assert type(actual['counts'][0]) is int and type(actual['counts'][1]) is bool


@pytest.mark.parametrize('value,quote',[
    ('2022','All operations use 100% renewable energy by 2030.'),
    ('global operations','Coverage includes the entire company.'),
    ('group','Group and GROUP'),
    ('2022','Code 20220'),
    (82,'The target is 82.5 percent.'),
    (5,'The target is 82.5 percent.'),
    (' group ','Subgroup'),
    (True,'The answer is yes.'),
    ({'subject':'Group','missing':'unknown'},'Group'),
    ([], 'Any text'),
])
def test_attribute_literals_reject_invention_and_ambiguous_normalization(value,quote):
    from esg_extraction.pipeline.enrichment import bind_attribute_literal
    with pytest.raises(ValueError):
        bind_attribute_literal(value,quote)


@pytest.mark.parametrize('value,quote,expected',[
    ('2022','Goal of powering all operations by 2030.',None),
    ('all operations','Goal of powering All Operations by 2030.','All Operations'),
])
def test_open_attribute_enrichment_keeps_unsupported_value_unknown_and_diagnostic(documents,repository,value,quote,expected):
    from esg_extraction.pipeline.enrichment import apply_context,ContextDecision
    document=documents.document('ir-example');clause=repository.get('E1-5_09')
    source=fact(document,'literal','29%','2022')
    frame=EvidenceFrame(object_id='context',kind='text',page_index=2,digest='literal',texts=[
        TextEvidence(object_id='title',span_id='c1',text=quote,page_index=2,source_snapshot=document.snapshot_id)])
    observation={'requested_attributes':['reporting_boundary'],'source_levels':{'c1':'table'},
        'fact_sources':{source.source_fact_id:['c1']},'report_sources':[],'report_attributes':[]}
    decision=ContextDecision.model_validate({'assignments':[{'fact_ids':['f1'],'identity':[
        {'axis':'reporting_boundary','value':value,'quote':quote,'source_id':'c1'}]}]})
    facts={source.source_fact_id:source};mappings={source.source_fact_id:mapping(clause,source)};rejected=[]
    apply_context(clause,{document.snapshot_id:document},frame,decision,facts,mappings,
        {'f1':source.source_fact_id},observation,rejected_claims=rejected)
    field=facts[source.source_fact_id].identity.reporting_boundary
    assert field.value==expected
    assert field.state==('stated' if expected else 'not_read')
    assert facts[source.source_fact_id].value==source.value
    assert facts[source.source_fact_id].identity.reporting_period==source.identity.reporting_period
    assert bool(rejected)==(expected is None)
    if rejected:
        assert rejected[0]['reason']=='attribute_value_missing_or_ambiguous_in_quote'


def test_declared_vocabulary_binds_a_proven_code_without_requiring_its_literal_spelling(documents,repository):
    from esg_extraction.pipeline.enrichment import apply_context,ContextDecision
    document=documents.document('ir-example');clause=repository.get('E1-5_09')
    source=fact(document,'target-alias','100%','2030')
    quote='We are committed to powering operations with renewable energy by 2030.'
    frame=EvidenceFrame(object_id='context',kind='text',page_index=2,digest='known-code',texts=[
        TextEvidence(object_id='role',span_id='c1',text=quote,page_index=2,source_snapshot=document.snapshot_id)])
    decision=ContextDecision.model_validate({'assignments':[{'fact_ids':['f1'],'identity':[
        {'axis':'value_role','value':'目标','quote':quote,'source_id':'c1'}]}]})
    observation={'requested_attributes':['value_role'],'source_levels':{'c1':'table'},
        'fact_sources':{source.source_fact_id:['c1']},'report_sources':[],'report_attributes':[]}
    facts={source.source_fact_id:source};mappings={source.source_fact_id:mapping(clause,source)}
    apply_context(clause,{document.snapshot_id:document},frame,decision,facts,mappings,
        {'f1':source.source_fact_id},observation)
    assert facts[source.source_fact_id].identity.value_role.value=='target'
    assert mappings[source.source_fact_id].match=='different'


@pytest.mark.parametrize('raw,year,quote,accepted',[
    ('82%','2028','Target is 82% by 2028 and 100% by 2030.',True),
    ('100%','2030','Target is 82% by 2028 and 100% by 2030.',True),
    ('50%','2025','Target is 82% by 2028 and 100% by 2030.',False),
    ('50%','2028','Target is 82% by 2028 and 100% by 2030.',False),
    ('82%','2028','Target is 82%.',False),
    ('82%','2028','Target is 82.5% by 2028.',False),
    ('82','2028','Target is 82.5% by 2028.',False),
])
def test_report_role_requires_both_actual_value_and_explicit_fact_year(documents,repository,raw,year,quote,accepted):
    from esg_extraction.pipeline.enrichment import apply_context,ContextDecision
    document=documents.document('ir-example');clause=repository.get('E1-5_09')
    role=clause.attributes['value_role'].model_copy(update={'context_levels':['local','report']})
    clause=clause.model_copy(update={'attributes':{**clause.attributes,'value_role':role}}).seal()
    source=fact(document,'report-target',raw,year)
    frame=EvidenceFrame(object_id='context',kind='text',page_index=3,digest='report-role',texts=[
        TextEvidence(object_id='target-passage',span_id='c1',text=quote,page_index=3,source_snapshot=document.snapshot_id)])
    decision=ContextDecision.model_validate({'assignments':[{'fact_ids':['f1'],'identity':[
        {'axis':'value_role','value':'target','quote':quote,'source_id':'c1'}]}]})
    observation={'requested_attributes':['value_role'],'source_levels':{'c1':'report'},
        'fact_sources':{source.source_fact_id:[]},'report_sources':['c1'],'report_attributes':['value_role']}
    facts={source.source_fact_id:source};mappings={source.source_fact_id:mapping(clause,source)};rejected=[]
    apply_context(clause,{document.snapshot_id:document},frame,decision,facts,mappings,
        {'f1':source.source_fact_id},observation,rejected_claims=rejected)
    assert facts[source.source_fact_id].identity.value_role.state==('stated' if accepted else 'not_read')
    assert mappings[source.source_fact_id].match==('different' if accepted else 'match')
    assert bool(rejected)!=accepted


def test_attribute_context_search_is_per_attribute_and_never_becomes_local(documents,repository):
    from esg_contracts.evidence import ObjectReference
    from esg_extraction.pipeline.enrichment import context_frame
    document=documents.document('ir-example');clause=repository.get('E1-5_09')
    attributes=dict(clause.attributes)
    attributes['reporting_boundary']=attributes['reporting_boundary'].model_copy(update={
        'context_levels':['report'],'context_terms':['reporting boundary']})
    attributes['measurement_method']=attributes['measurement_method'].model_copy(update={
        'context_levels':['local','report'],'context_terms':['calculation method']})
    attributes['reporting_entity']=attributes['reporting_entity'].model_copy(update={
        'context_levels':['local'],'context_terms':['reporting entities']})
    clause=clause.model_copy(update={'attributes':attributes,'purposes':clause.purposes.model_copy(update={
        'enrichment':clause.purposes.enrichment.model_copy(update={
            'identity_axes':['reporting_boundary','measurement_method','reporting_entity']})})}).seal()
    source=fact(document,'source','29%')
    table=EvidenceFrame(object_id='own-table',kind='table',page_index=2,digest='own',source_snapshot=document.snapshot_id,
        rows=[EvidenceRow(row_id='row',label='Renewable Energy',label_sources=[
            TextEvidence(object_id='own-label',text='Renewable Energy',page_index=2)],cells=[
                EvidenceCell(cell_id='cell-source',column_id='year',raw_text='29%')])])
    context=EvidenceFrame(object_id='report-text',kind='text',page_index=2,digest='search',texts=[
        TextEvidence(object_id='boundary',text='Reporting boundary covers Group operations.',page_index=2),
        TextEvidence(object_id='method',text='Calculation method is Method A.',page_index=3),
        TextEvidence(object_id='other-company',text='At Recurrent Energy we monitor projects.',page_index=2)])
    class Docs:
        def search_page(self,*args):
            return ObjectSearchPage(objects=[ObjectReference(object_id=context.object_id,kind='text',page_index=2,score=1)],
                total=1,returned=1,query_digest='context',scope={})
        def read(self,*args):return context
    profile={'attributes':{axis:attribute.context_terms for axis,attribute in attributes.items()},
        'levels':{axis:attribute.context_levels for axis,attribute in attributes.items()}}
    frame,observation=context_frame(Docs(),{document.snapshot_id:document},clause,[source],profile,[table])
    aliases={text.object_id:text.span_id for text in frame.texts}
    permissions=observation['attribute_sources'][source.source_fact_id]
    assert 'other-company' not in aliases
    assert aliases['boundary'] in permissions['reporting_boundary']
    assert aliases['boundary'] not in permissions['measurement_method']
    assert aliases['method'] in permissions['measurement_method']
    assert aliases['method'] not in permissions['reporting_boundary']
    assert aliases['method'] not in permissions['reporting_entity']
    assert set(observation['report_sources']).isdisjoint(observation['fact_sources'][source.source_fact_id])


def test_numbered_local_note_requires_the_current_rows_reference(documents,repository):
    from esg_extraction.pipeline.enrichment import context_frame,attribute_source_ids,local_note_applies
    document=documents.document('ir-example');clause=repository.get('E3-4_01')
    clause=clause.model_copy(update={'purposes':clause.purposes.model_copy(update={
        'enrichment':clause.purposes.enrichment.model_copy(update={'identity_axes':['measurement_method']})})}).seal()
    sources=[fact(document,'water','2,823'),fact(document,'energy','12,345')]
    table=EvidenceFrame(object_id='shared-table',kind='table',page_index=2,digest='shared',source_snapshot=document.snapshot_id,
        rows=[EvidenceRow(row_id=key,label=label,label_sources=[TextEvidence(object_id='label-'+key,text=label,page_index=2)],
            cells=[EvidenceCell(cell_id='cell-'+key,column_id='year',raw_text=raw)]) for key,label,raw in [
                ('water','Total water consumption','2,823'),('energy','Comprehensive energy consumption⁴','12,345')]],
        texts=[TextEvidence(object_id='energy-note',text='⁴ Energy consumption uses GB/T 2589·2020.',page_index=2)])
    profile={'attributes':{axis:attribute.context_terms for axis,attribute in clause.attributes.items()},
        'levels':{axis:attribute.context_levels for axis,attribute in clause.attributes.items()}}
    frame,observation=context_frame(None,{document.snapshot_id:document},clause,sources,profile,[table])
    note=next(text.span_id for text in frame.texts if text.object_id=='energy-note')
    assert note not in observation['fact_sources']['water']
    assert note in observation['fact_sources']['energy']
    # Pre-map checkpoints are rebuilt with the same scope rules.
    old={key:value for key,value in observation.items() if key!='attribute_sources'}
    old['fact_sources']={key:[*ids,note] for key,ids in observation['fact_sources'].items()}
    migrated=attribute_source_ids(clause,frame,old)
    assert note not in migrated['water']['measurement_method']
    assert note in migrated['energy']['measurement_method']
    assert not local_note_applies('³ Energy note','Volume m³')


def test_constrained_schema_cannot_call_another_target_measurement_nonmeasurement(documents, repository):
    import jsonschema
    from pydantic import ValidationError
    from esg_extraction.pipeline.decisions import NumericMatch, decision_schema
    from esg_contracts.evidence import SearchIntent
    clause=quantitative(repository)
    frame=documents.read('ir-example',documents.search('ir-example',SearchIntent(target_id=clause.clause_key,
        contract_digest=clause.clause_digest,data_kind='quantitative',positive_terms=['能源']))[0])
    item={'row_id':frame.rows[0].row_id,'columns':['c1'],'reason':'real other quantity','match':'different','selection':'nonmeasurement'}
    with pytest.raises(ValidationError):NumericMatch.model_validate(item)
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate({'matches':[item]},decision_schema('quantitative',frame))
    item['selection']='measurement'
    jsonschema.validate({'matches':[{k:item[k] for k in ('row_id','columns','match')}]},decision_schema('quantitative',frame))


def test_adjacent_quantity_capture_does_not_capture_unselected_rows():
    from esg_extraction.pipeline.engine import adjacent_table_quantities
    source=TextEvidence(object_id='unit',object_type='cell',text='吨',page_index=0)
    row=EvidenceRow(row_id='other',label='其他实测项目',row_role='data',unit_raw='吨',unit_sources=[source],
        cells=[EvidenceCell(cell_id='value',column_id='2025',raw_text='175.61')])
    frame=EvidenceFrame(object_id='table',kind='table',page_index=0,digest='x',rows=[row])
    extra=adjacent_table_quantities(frame,['2025'],[])
    assert not extra
    assert not adjacent_table_quantities(frame,None,[])
    assert not adjacent_table_quantities(frame.model_copy(update={'kind':'text'}),['2025'],[])
    assert not adjacent_table_quantities(frame,['2025'],extra)
    assert not adjacent_table_quantities(frame.model_copy(update={'rows':[row.model_copy(update={'unit_sources':[]})]}),['2025'],[])


def test_statement_quote_alignment_preserves_source_and_rejects_rewording():
    from esg_extraction.pipeline.binding import source_quote
    source = "原文：持續監察，(GB16297- 1996)及排放標準。"
    start, end = source_quote(source, "持續監察，(GB16297- 1996) 及排放標準。")
    assert source[start:end] == "持續監察，(GB16297- 1996)及排放標準。"
    with pytest.raises(ValueError, match="missing or ambiguous"):
        source_quote(source, "持續監察，符合全部排放標準。")
    with pytest.raises(ValueError, match="missing or ambiguous"):
        source_quote("same  text; same text", "same   text")


def test_whitespace_quote_alignment_rejects_overlapping_matches():
    from esg_extraction.pipeline.binding import source_quote
    with pytest.raises(ValueError, match="missing or ambiguous"):
        source_quote("a a a", "aa")


@pytest.mark.parametrize("unit,family,scale", [
    ("10,000 tce", "energy_tce", "10000"),
    ("10,000 kJ", "energy", "0.002777777780000"),
    ("10,000 m³", "volume", "10000"),
    ("Megaliters", "volume", "1000"),
])
def test_printed_scale_is_part_of_unit(unit, family, scale):
    from decimal import Decimal
    from esg_data.units import is_unit_expression, unit_dimension
    from esg_data.quantities import unit_scale
    assert is_unit_expression(unit)
    assert unit_scale(unit) == (family, Decimal(scale))
    assert unit_dimension(unit) is not None


def test_parenthesized_scale_is_not_a_source_measurement():
    from esg_document.document.literals import numeric_rows
    from esg_contracts.evidence import TextEvidence
    source = TextEvidence(object_id="caption", page_index=71,
        text="Total Water Consumption(10,000 m³) Ultra-Pure Water Usage (10,000 m³)")
    assert all(cell.candidate_kind == "identifier" for row in numeric_rows(source) for cell in row.cells)
    actual = TextEvidence(object_id="statement",page_index=71,
        text="Total water consumption was 10,000 m³.")
    assert numeric_rows(actual)[0].cells[0].candidate_kind == "measurement"


def test_abbreviation_and_quoted_endings_keep_complete_source_sentences():
    from esg_data.text_search import sentence_ranges
    source = 'We follow U.S. standards and consult Dr. Lee. "We monitor emissions." We keep results.'
    parts = [source[start:end] for start,end in sentence_ranges(source)]
    assert parts == ['We follow U.S. standards and consult Dr. Lee. ', '"We monitor emissions." ', 'We keep results.']
    assert ''.join(parts) == source


def test_reference_column_survives_raw_table_fallback():
    from esg_document.document.evidence_service import reference_columns
    assert reference_columns({'c0':['Measure'], 'c1':['GRI'], 'c2':['NSF 457'], 'c3':['2024']}) == {'c1','c2'}


def test_superscript_notes_do_not_hide_measurement_words_or_change_unit_powers():
    from esg_data.literals import strip_text_footnotes
    from esg_document.document.search_ranking import phrase_score
    assert strip_text_footnotes("Recycled Water³") == "Recycled Water"
    assert strip_text_footnotes("水循环量³") == "水循环量"
    assert strip_text_footnotes("m³ and CO₂ and CO²") == "m³ and CO₂ and CO²"
    assert phrase_score(["recycled water"], "Recycled Water³") > 0
    assert phrase_score(["recycled water"], "Recycled Water3") == 0


def test_newline_separates_figure_labels_from_printed_quantities():
    from esg_contracts.evidence import TextEvidence
    from esg_document.document.literals import numeric_rows
    source=TextEvidence(object_id='figure',page_index=20,text='Total\n4.477 百萬立方米')
    row=numeric_rows(source)[0]
    assert row.cells[0].candidate_kind=='measurement'
    assert row.cells[0].raw_text=='4.477' and row.unit_raw=='百萬立方米'
    assert row.unit_sources[0].text==source.text


def test_printed_percentage_suffix_is_bound_as_the_original_unit():
    from esg_extraction.pipeline.binding import _number
    parsed,_,unit=_number('29%')
    assert str(parsed['value'])=='29' and unit=='%'


def test_figure_reader_selects_an_actual_unit_span_without_overriding_row_units(documents,repository):
    from esg_contracts.evidence import TextEvidence,EvidenceContextLink
    from esg_document.document.literals import numeric_rows
    from esg_extraction.pipeline.reading_frame import printed_unit_sources,reading_frame
    from esg_extraction.pipeline.decisions import NumericMatch,decision_schema
    from esg_extraction.pipeline.binding import numeric_fact
    document=documents.document('ir-example')
    source=TextEvidence(object_id='figure',page_index=23,
        text='GHG emissions (Unit: tonnes of CO2 equivalent)\nTotal emissions\n5,011,000.40\nAir pollution (Unit: metric tonnes)\n8.5')
    frame=EvidenceFrame(object_id='figure',kind='text',page_index=23,digest='figure-units',
        source_snapshot=document.snapshot_id,texts=[source],rows=numeric_rows(source))
    sources=printed_unit_sources(frame)
    assert [item.text for item in sources.values()]==['tonnes of CO2 equivalent','metric tonnes']
    assert reading_frame(frame,data_kind='quantitative')['unit_sources'][0]['source_id']=='u1'
    schema=decision_schema('quantitative',frame)
    assert schema['properties']['matches']['items']['properties']['unit_source_id']['enum']==['u1','u2']
    row=next(row for row in frame.rows if row.cells[0].raw_text=='5,011,000.40')
    unit_start=source.text.index('(Unit: tonnes')
    unit_text='(Unit: tonnes of CO2 equivalent)'
    fragment=source.model_copy(update={'span_id':'ghg-unit','char_start':unit_start,'text':unit_text})
    frame=frame.model_copy(update={'texts':[source,fragment],'context_links':[
        EvidenceContextLink(cell_id=row.cells[0].cell_id,source_ids=['ghg-unit'],relation='region')]})
    match=NumericMatch(row_id=row.row_id,columns=['value'],unit_source_id='u1')
    clause=repository.get('E1-6_07')
    bound,_,_=numeric_fact(clause,document,frame,match,row,row.cells[0],'source-unit')
    assert bound.value.unit_raw=='tonnes of CO2 equivalent'
    evidence=next(item for item in bound.evidence if item.role=='unit')
    assert source.text[evidence.char_start:evidence.char_end]==evidence.excerpt
    assert evidence.object_id==source.object_id
    printed=row.model_copy(update={'unit_raw':'metric tonnes','unit_sources':[
        source.model_copy(update={'text':'metric tonnes'})]})
    retained,_,_=numeric_fact(clause,document,frame,match,printed,printed.cells[0],'row-unit')
    assert retained.value.unit_raw=='metric tonnes'


@pytest.mark.parametrize('relation,label_id,expected',[
    ('column','direct','match'),('region','direct','uncertain'),('column','indirect','uncertain'),
])
def test_numeric_label_requires_a_strong_relationship_to_the_exact_literal(documents,repository,relation,label_id,expected):
    from esg_contracts.evidence import EvidenceContextLink
    from esg_document.document.literals import numeric_rows
    from esg_extraction.pipeline.decisions import NumericMatch
    from esg_extraction.pipeline.binding import numeric_fact
    source=TextEvidence(object_id='figure',span_id='whole',page_index=2,
        text='Direct GHG emissions\nIndirect GHG emissions\n100tCO2e\nUnrelated entity method')
    direct=source.model_copy(update={'span_id':'direct','text':'Direct GHG emissions'})
    indirect=source.model_copy(update={'span_id':'indirect','char_start':21,'text':'Indirect GHG emissions'})
    row=next(row for row in numeric_rows(source) if row.cells[0].raw_text=='100')
    frame=EvidenceFrame(object_id='figure',kind='text',page_index=2,digest='linked',
        source_snapshot=documents.document('ir-example').snapshot_id,rows=[row],texts=[source,direct,indirect],
        context_links=[EvidenceContextLink(cell_id=row.cells[0].cell_id,source_ids=['direct'],relation=relation)])
    raw,link,error=numeric_fact(repository.get('E1-6_07'),documents.document('ir-example'),frame,
        NumericMatch(row_id=row.row_id,columns=['value'],label_source_id=label_id),row,row.cells[0],'linked')
    assert error is None and raw.value.value_raw=='100' and link.match==expected
    assert 'Unrelated entity' not in ' '.join(location.excerpt for location in raw.evidence if location.role!='value')
    if expected=='uncertain':
        assert 'numeric_context_application_unproven' in raw.flags


def test_closed_local_label_preserves_a_wrapped_nonrenewable_qualifier(documents,repository):
    from esg_contracts.evidence import EvidenceContextLink
    from esg_document.document.literals import numeric_rows
    from esg_extraction.pipeline.binding import numeric_fact
    from esg_extraction.pipeline.decisions import NumericMatch
    source=TextEvidence(object_id='wrapped',span_id='whole',page_index=2,
        text='Non-\nrenewable energy consumption\n100 MWh')
    prefix=source.model_copy(update={'span_id':'prefix','text':'Non-'})
    label=source.model_copy(update={'span_id':'label','text':'renewable energy consumption','char_start':5})
    row=next(row for row in numeric_rows(source) if row.cells[0].raw_text=='100')
    frame=EvidenceFrame(object_id='wrapped',kind='text',page_index=2,digest='closed',rows=[row],
        source_snapshot=documents.document('ir-example').snapshot_id,texts=[source,prefix,label],
        context_links=[EvidenceContextLink(cell_id=row.cells[0].cell_id,source_ids=['prefix','label'],relation='row')])
    raw,link,error=numeric_fact(repository.get('E1-5_05'),documents.document('ir-example'),frame,
        NumericMatch(row_id=row.row_id,columns=['value'],label_source_id='label'),row,row.cells[0],'closed')
    assert error is None and raw.value.value_raw=='100' and link.match=='different'
    assert raw.source_row_label=='Non-\nrenewable energy consumption'
    assert any(location.excerpt==raw.source_row_label for location in raw.evidence if location.role=='row')


def test_unparsed_text_quantity_survives_local_source_selection(documents,repository,monkeypatch):
    from esg_contracts.evidence import EvidenceContextLink
    from esg_document.document.literals import numeric_rows
    from esg_extraction.pipeline import binding
    from esg_extraction.pipeline.decisions import NumericMatch
    from esg_extraction.pipeline.selection import quantitative_candidates
    source=TextEvidence(object_id='scientific',span_id='clause',page_index=2,
        text='Direct GHG emissions were 1.2e5tCO2e.')
    row=next(row for row in numeric_rows(source) if row.cells[0].raw_text=='1.2e5')
    frame=EvidenceFrame(object_id='scientific',kind='text',page_index=2,digest='scientific',rows=[row],texts=[source],
        source_snapshot=documents.document('ir-example').snapshot_id,
        context_links=[EvidenceContextLink(cell_id=row.cells[0].cell_id,source_ids=['clause'],relation='same_clause')])
    clause=repository.get('E1-6_07')
    candidates,rejected=quantitative_candidates(clause,frame)
    assert len(candidates.rows)==1 and not rejected
    def unsupported(_):raise ValueError('Parser cannot interpret the printed literal')
    monkeypatch.setattr(binding,'_number',unsupported)
    raw,link,error=binding.numeric_fact(clause,documents.document('ir-example'),frame,
        NumericMatch(row_id=row.row_id,columns=['value'],label_source_id='clause'),row,row.cells[0],'unparsed')
    assert error is None and raw.value.value_raw=='1.2e5' and raw.value.parse_status=='unparsed'
    assert link.match=='match'


@pytest.mark.parametrize('unit', ['万吨CO2e', '万 吨 CO2e'])
def test_word_scale_keeps_the_printed_ghg_family_in_candidate_selection(repository, unit):
    from decimal import Decimal
    from esg_data.quantities import unit_scale
    from esg_extraction.pipeline.selection import quantitative_candidates
    row = EvidenceRow(row_id='total', label='温室气体排放总量（范围1、市场法范围2、范围3）',
        unit_raw=unit, cells=[EvidenceCell(cell_id='amount', column_id='2021', raw_text='1')])
    frame = EvidenceFrame(object_id='table', kind='table', page_index=1, digest='ghg', rows=[row])
    selected, rejected = quantitative_candidates(repository.get('E1-6_13'), frame)
    assert unit_scale(unit) == ('ghg', Decimal('1e4'))
    assert selected.rows == [row] and not rejected
    assert selected.rows[0].unit_raw == unit


@pytest.mark.parametrize('printed,expected', [
    ('1 January to 31 December 2021', '2021'),
    ('January 1 to December 31, 2021', '2021'),
    ('1 April to 30 September 2021', ('range', '2021-04-01', '2021-09-30')),
    ('April 1 to September 30, 2021', ('range', '2021-04-01', '2021-09-30')),
    ('1 April to 31 March 2021', ('unknown', None, None)),
    ('1 April 2020 to 31 March 2021', ('range', '2020-04-01', '2021-03-31')),
])
def test_report_reference_binds_shared_trailing_year_as_the_full_printed_period(printed, expected):
    from esg_contracts.evidence import DocumentReference
    from esg_extraction.pipeline.period_binding import bind_period
    document = DocumentReference(document_id='report', ir_run_id='ir', ir_revision=1,
        content_digest='report', reporting_year=2021, reporting_period_raw=printed,
        period_context={'basis':'reporting_period_statement', 'source_id':'about-report',
            'evidence':printed, 'page_index':1, 'inheritance':'explicit_reference_only'})
    row = EvidenceRow(row_id='actual', label='During the reporting period emissions were 100 tonnes.', cells=[])
    frame = EvidenceFrame(object_id='statement', kind='text', page_index=9, digest='period', rows=[row])
    field, flags = bind_period(frame, row=row, document=document)
    assert field.state == 'inherited' and field.raw_text == printed and not flags
    assert field.evidence[0].excerpt == printed
    if isinstance(expected, str):
        assert field.value == expected
    else:
        kind, start, end = expected
        assert field.value['kind'] == kind
        assert field.value.get('start') == start and field.value.get('end') == end


def test_report_period_reference_matches_both_written_chinese_forms():
    from esg_extraction.pipeline.period_binding import references_report_period
    for text in ["报告期内的用量","報告期內的用量","本報告期的用量","During the reporting period"]:
        assert references_report_period(text)
    assert not references_report_period("The comparison year is 2020")
