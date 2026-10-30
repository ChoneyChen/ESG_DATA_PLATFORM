import json,shutil
from pathlib import Path
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from esg_data.organization import OrganizationField,FieldBinding,OrganizationSpec,Presentation,OrganizedDataset
from esg_clauses.repository import ClauseRepository
from esg_contracts.facts import SourceFact,QuantitativePayload,ReportedContext,ReportedField,EvidenceLocation,ClauseMapping,RunRequest
from esg_contracts.facts import QualitativePayload
from esg_extraction.pipeline.organization import build_organized_dataset
from esg_extraction.pipeline.engine import ExtractionBackbone
from esg_extraction.pipeline.adapters.artifacts import FileArtifactPort
from esg_extraction.pipeline.api import create_app
from esg_contracts.evidence import SearchIntent
from esg_extraction.pipeline.state import RunStore
from test_backbone import repository,documents,Model,quantitative


def reported(value,raw=None):
    text=raw if raw is not None else str(value)
    return ReportedField(state='stated',raw_text=text,value=value,evidence=[EvidenceLocation(object_id='context-'+text,object_type='text_block',page_index=2,excerpt=text,role='table')])


def fact(document,key,raw='0',year='2025',dimensions=None,extensions=None):
    return SourceFact(source_fact_id=key,source_anchor_id='anchor-'+key,document_id=document.document_id,
        ir_run_id=document.ir_run_id,ir_revision=document.ir_revision,interpretation_id='test',report_resource=document.report_resource,
        value=QuantitativePayload(value_raw=raw),source_row_label='来源行 '+key,
        identity=ReportedContext(reporting_period=reported(year),dimensions=dimensions or {}),
        evidence=[EvidenceLocation(object_id='cell-'+key,object_type='cell',page_index=2,excerpt=raw,role='value')],extensions=extensions or {})


def mapping(clause,f):
    return ClauseMapping(clause_key=clause.clause_key,clause_digest=clause.clause_digest,source_fact_id=f.source_fact_id,match='match',reason='synthetic contract test')


def test_every_clause_is_parallel_and_priority32_owns_local_fields(repository):
    all_rows=repository.catalog()['clauses']
    for row in all_rows:
        c=repository.get(row['clause_key'])
        c.organization.validate_kind(c.data_kind)
    chosen=repository.select(selection='priority32')
    assert len(chosen)==32 and all(c.organization.curation=='sample' for c in chosen.values())
    assert len({c.schema_version for c in chosen.values()})==1
    assert any(f.binding.key=='pollutant' and f.binding.source=='dimension' for f in chosen['E2-4_02'].organization.fields)
    assert any(f.binding.key=='energy_carrier' for f in chosen['E1-5_01'].organization.fields)
    assert chosen['E2-4_09'].organization.presentation.view=='statements'


@pytest.mark.parametrize('source,role,key',[('target','reported_dimension','fixed_axes'),('resource','reported_identity','company'),('value','raw_value','value_numeric')])
def test_table_roles_cannot_disguise_targets_or_parsed_values(source,role,key):
    with pytest.raises(ValueError):OrganizationField(key='bad',label='Invalid',role=role,binding=FieldBinding(source=source,key=key))


def test_table_projection_keeps_rows_periods_zero_unknown_and_extra_dimensions(documents,repository):
    document=documents.document('ir-example');c=repository.get('E2-4_02')
    c=c.model_copy(update={'organization':c.organization.model_copy(update={'presentation':c.organization.presentation.model_copy(update={'group_by':['observed_pollutant','reporting_period']})})}).seal()
    one=fact(document,'one','0','2025',{'pollutant':reported('SO2','二氧化硫'),'production_line':reported('line-1','一号产线')})
    two=fact(document,'two','1-2','2024',{'pollutant':reported('SO2','二氧化硫')})
    unknown=fact(document,'unknown','约十三万吨','2025')
    inputs=[one,two,unknown];before=[f.model_dump(mode='json') for f in inputs]
    result=build_organized_dataset('run',document,{c.clause_key:c},inputs,[mapping(c,f) for f in inputs])
    assert not result.unorganized_fact_ids and len(result.tables[0].rows)==3
    table=result.tables[0];by_id={r.source_fact_id:r for r in table.rows}
    assert by_id['one'].cells['raw_value'].value=='0'
    assert by_id['two'].cells['raw_value'].value=='1-2'
    assert by_id['unknown'].cells['raw_value'].value=='约十三万吨'
    assert by_id['unknown'].cells['observed_pollutant'].state=='not_read'
    assert by_id['one'].cells['observed_pollution_medium'].state=='not_read'
    assert by_id['one'].cells['target_pollution_medium'].value=='air'
    assert by_id['one'].cells['target_pollution_medium'].origin=='target'
    additional=next(f for f in table.definition.fields if f.binding.key=='production_line')
    assert by_id['one'].cells[additional.key].raw_text=='一号产线'
    assert by_id['one'].cells['observed_pollutant'].evidence[0].container=='dimension'
    assert by_id['one'].cells['observed_pollutant'].raw_text=='二氧化硫'
    assert len(table.groups)==3 and sum(len(g.row_ids) for g in table.groups)==3
    assert [f.model_dump(mode='json') for f in inputs]==before
    OrganizedDataset.model_validate_json(result.model_dump_json())


def test_local_vocabulary_flags_wrong_species_without_discarding_raw(documents,repository):
    document=documents.document('ir-example');c=repository.get('E2-4_02')
    wrong=fact(document,'wrong','3',dimensions={'pollutant':reported('SO2','氮氧化物')})
    novel=fact(document,'novel','4',dimensions={'pollutant':reported('PFAS','PFAS')})
    result=build_organized_dataset('run',document,{c.clause_key:c},[wrong,novel],[mapping(c,wrong),mapping(c,novel)])
    rows={r.source_fact_id:r for r in result.tables[0].rows}
    assert rows['wrong'].cells['observed_pollutant'].state=='conflict'
    assert rows['wrong'].cells['raw_value'].value=='3'
    assert rows['novel'].cells['observed_pollutant'].value=='PFAS'
    assert rows['novel'].cells['raw_value'].value=='4'
    assert any('vocabulary_unlisted' in flag for flag in rows['novel'].flags)
    assert c.organization.confirmation_flags(wrong.identity.model_dump(mode='json'))
    assert not result.unorganized_fact_ids


def test_local_classification_requires_a_literal_and_never_invents_a_category(repository):
    field=next(f for f in repository.get('E2-4_02').organization.fields if f.binding.key=='pollutant')
    for value,raw in [('SO₂','SO₂ 排放量'),('二氧化硫','二氧化硫排放量'),('SO2','二氧化硫')]:
        match=field.category_match(reported(value,raw).model_dump(mode='json'))
        assert match.state=='recognized' and match.code=='SO2'
    for value,raw,state in [('NO2','二氧化氮','unlisted'),('PFAS','PFAS','unlisted'),('NOx','SO₂','conflict'),('SO2','污染物','unproven'),('SO2','SO2、NOx 合并报告','unproven')]:
        match=field.category_match(reported(value,raw).model_dump(mode='json'))
        assert match.state==state and match.code is None


def test_matrix_preserves_multiple_values_and_does_not_fill_missing_coordinates(documents,repository):
    document=documents.document('ir-example');c=repository.get('E2-4_02')
    facts=[fact(document,'a','1.1','2025',{'pollutant':reported('SO2','二氧化硫')}),
           fact(document,'b','2.2','2025',{'pollutant':reported('SO₂','SO₂')}),
           fact(document,'c','3','2024',{'pollutant':reported('NOx','氮氧化物')})]
    table=build_organized_dataset('run',document,{c.clause_key:c},facts,[mapping(c,f) for f in facts]).tables[0]
    matrix=table.matrix
    assert matrix is not None and matrix.aggregation_performed is False
    assert len(matrix.rows)==2 and len(matrix.columns)==2
    assert len(matrix.cells)==2 # The two missing intersections are absent, never zero.
    assert sorted(len(cell.row_ids) for cell in matrix.cells)==[1,2]
    assert {row for cell in matrix.cells for row in cell.row_ids}=={row.row_id for row in table.rows}
    assert {row.cells['raw_value'].value for row in table.rows}=={'1.1','2.2','3'}
    assert sorted(len(group.row_ids) for group in table.groups)==[1,2]
    assert all(cell.extensions['grouping_key_only'] and not cell.evidence for axis in matrix.rows for cell in axis.keys.values())
    assert any(row.cells['observed_pollutant'].raw_text=='SO₂' for row in table.rows)


def test_qualitative_organization_keeps_original_text_and_unknown_actual_identity(documents,repository):
    document=documents.document('ir-example');c=repository.get('E2-4_09');text='本年度采取连续监测，并按季度核查排放数据。'
    source=SourceFact(source_fact_id='statement',source_anchor_id='anchor',document_id=document.document_id,
        ir_run_id=document.ir_run_id,ir_revision=document.ir_revision,interpretation_id='test',
        value=QualitativePayload(statement_raw=text,extensions={'new_note':'方法附注'}),
        identity=ReportedContext(),evidence=[EvidenceLocation(object_id='paragraph',object_type='text_block',page_index=2,excerpt=text,role='statement')])
    table=build_organized_dataset('run',document,{c.clause_key:c},[source],[mapping(c,source)]).tables[0]
    assert table.definition.presentation.view=='statements'
    row=table.rows[0]
    assert row.cells['raw_value'].value==text and row.cells['raw_value'].evidence[0].container=='fact'
    assert row.cells['reporting_entity'].state=='not_read' and row.cells['reporting_entity'].value is None
    assert not any(field.role=='parsed_value' for field in table.definition.fields)
    extra=next(field for field in table.definition.fields if field.binding.attribute=='new_note')
    assert row.cells[extra.key].value=='方法附注'


def test_resource_identity_and_target_definitions_never_fill_actual_identity(documents,repository):
    from esg_reports.contract import ReportResource,Company
    resource=ReportResource(pdf_id='pdf-'+'a'*24,asset_id='pdf-'+'a'*24,sha256='a'*64,original_filename='report.pdf',
        company=Company(name='资源声明企业'),publication_year=2026,reporting_year=2025)
    document=documents.document('ir-example').model_copy(update={'report_resource':resource})
    c=repository.get('E2-4_02');source=fact(document,'unknown').model_copy(update={'identity':ReportedContext()})
    table=build_organized_dataset('run',document,{c.clause_key:c},[source],[mapping(c,source)]).tables[0]
    cells=table.rows[0].cells
    assert cells['report_company'].value=='资源声明企业' and cells['publication_year'].value==2026
    assert cells['report_coverage_year'].value==2025 and cells['target_pollution_medium'].value=='air'
    for field in ['reporting_entity','reporting_period','reporting_boundary','observed_pollution_medium']:
        assert cells[field].value is None and cells[field].state=='not_read'


def test_each_selected_clause_can_be_used_without_related_clause_files(tmp_path,repository,documents):
    c=repository.get('E1-5_09');c=c.model_copy(update={'relationships':c.relationships.model_copy(update={'parent_clause':'MISSING_PARENT','share_partner':'MISSING_PARTNER','siblings':['MISSING_SIBLING'],'related_clauses':['MISSING_RELATED']})}).seal()
    folder=tmp_path/c.group_id/c.clause_key;folder.mkdir(parents=True);(folder/'clause.json').write_text(c.model_dump_json())
    (tmp_path/'selections.json').write_text(json.dumps({'profiles':{'one':[c.clause_key]}}))
    isolated=ClauseRepository(tmp_path);isolated.reindex();selected=isolated.select([c.clause_key])
    assert list(selected)==[c.clause_key]
    card=c.reading_card();assert 'relationships' not in card and 'organization' not in card
    assert 'MISSING_PARENT' not in json.dumps(card)
    result=ExtractionBackbone(documents,Model(),FileArtifactPort(tmp_path/'artifacts')).run('run',RunRequest(ir_run_id='ir-example',clause_keys=[c.clause_key]),selected)
    assert result['summary']['selected_clauses']==1


def test_frozen_layout_is_not_replaced_by_a_later_edit(repository,documents):
    c=repository.get('E2-4_02');frozen=repository.freeze({c.clause_key:c})
    original=c.organization.fields[0].label
    changed=c.model_copy(update={'organization':c.organization.model_copy(update={'fields':[c.organization.fields[0].model_copy(update={'label':'new local display label'}),*c.organization.fields[1:]]})}).seal()
    assert changed.clause_digest!=c.clause_digest
    preserved=repository.thaw(frozen)[c.clause_key]
    assert preserved.organization.fields[0].label==original


def test_organization_failure_does_not_erase_bound_source_facts(documents,repository,tmp_path,monkeypatch):
    from esg_extraction.pipeline import engine
    def fail(*args):raise RuntimeError('synthetic layout failure')
    monkeypatch.setattr(engine,'build_organized_dataset',fail)
    c=quantitative(repository);store=FileArtifactPort(tmp_path/'out')
    result=ExtractionBackbone(documents,Model(),store).run('run',RunRequest(ir_run_id='ir-example',clause_keys=[c.clause_key]),{c.clause_key:c})
    assert result['facts'] and result['organization']['status']=='failed'
    assert store.read('run','results/facts.json')['facts']==result['facts']
    for f in result['facts']:assert store.read('run','facts/'+f['source_fact_id']+'.json')==f


def test_current_organization_interfaces_are_schema_driven(repository,documents,tmp_path):
    app=create_app(lambda:(repository,RunStore(tmp_path/'state.db'),FileArtifactPort(tmp_path/'out'),documents))
    with TestClient(app) as client:
        assert client.get('/api/organization-contract').status_code==200
        data=client.get('/api/clauses/E2-4_02/organization').json()
        assert data['organization']['curation']=='sample'
        assert data['relationships_are_execution_dependencies'] is False
        assert client.get('/api/clauses/E2-4_02/reading-card').json()['requested_identity_fields']
        response=client.post('/api/runs',json={'request':{'ir_run_id':'ir-example','clause_keys':['E2-4_02']}})
        assert response.status_code==201,response.text
        snapshot=client.get('/api/runs/'+response.json()['run_id']+'/targets').json()
        assert snapshot['clauses']['E2-4_02']['organization']==data['organization']
        pending=client.get('/api/runs/'+response.json()['run_id']+'/organized')
        assert pending.status_code==202 and pending.json()['status']=='pending'


def test_required_lexical_groups_apply_to_pages_objects_and_real_word_boundaries(documents):
    from esg_document.ocr.page_selection import ClausePageSelector
    service=documents;folder=Path(service.resolve('ir-example'))
    page=json.loads((folder/'page.json').read_text())
    page['blocks'].append({'block_id':'quality','block_type':'text','text':'Measurement System Analysis training for supplier quality'})
    page['blocks'].append({'block_id':'glossary','block_type':'text','text':'术语释义：pollution 污染；measurement 测量。'})
    (folder/'page.json').write_text(json.dumps(page,ensure_ascii=False))
    groups=[['污染','pollution'],['监测','measurement']]
    intent=SearchIntent(target_id='independent-method',contract_digest='a'*64,data_kind='qualitative',positive_terms=['污染','measurement'],required_term_groups=groups,excluded_object_terms=['术语释义'],source_priority=['text'])
    hits=service.search('ir-example',intent)
    assert 'b1' in {hit.object_id for hit in hits} and not {'quality','glossary'}&{hit.object_id for hit in hits}
    selector=ClausePageSelector(search_targets=[{'target_id':intent.target_id,'contract_digest':intent.contract_digest,'data_kind':intent.data_kind,'positive_terms':intent.positive_terms,'required_term_groups':groups}])
    assert selector.score_page(0,page['blocks'][0]['text']).score>0
    assert selector.score_page(1,page['blocks'][1]['text']).score==0
    assert service._score(['PM'],'company development')==0
    assert service._score(['NOx'],'No NOx emissions')>0


def test_reading_port_uses_local_instructions_and_medium_specific_vocabulary(repository):
    from esg_clauses.reading import check_reading
    air=repository.get('E2-4_02');water=repository.get('E2-4_03');method=repository.get('E2-4_09')
    card=water.reading_card()
    assert card['reading']['instructions']==water.reading.instructions
    assert 'rules' not in card['reading'] and 'retrieval' not in card
    vocab=next(field for field in card['requested_identity_fields'] if field['axis']=='pollutant')['vocabulary']
    assert 'COD' in {v['code'] for v in vocab} and 'SO2' not in {v['code'] for v in vocab}
    assert check_reading(water,'化学需氧量（COD）','废水污染物排放','吨','mass').ok
    assert not check_reading(water,'二氧化硫（SO2）','空气污染物排放','吨','mass').ok
    assert check_reading(air,'二氧化硫（SO2）','空气污染物排放','吨','mass').ok
    assert check_reading(method,'开展土壤、地下水背景环境质量调查，工厂投产后按规定开展自行监测。',None,None,None).ok
    assert not check_reading(method,'供应商质量测试系统分析与培训。',None,None,None).ok
