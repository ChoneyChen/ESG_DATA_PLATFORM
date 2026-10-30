import json
import pytest
from esg_contracts.evidence import SearchIntent
from esg_contracts.facts import RunRequest, PipelineBudget
from esg_extraction.pipeline.engine import ExtractionBackbone
from esg_extraction.pipeline.adapters.artifacts import FileArtifactPort
from test_backbone import documents, repository, quantitative, Model


def add_sources(documents):
    path=documents.resolve('ir-example')/'page.json'
    body=json.loads(path.read_text())
    body['blocks'].extend({'block_id':f'quantity-{i}','block_type':'text','text':f'能源消耗量为{i+1} MWh。'} for i in range(4))
    path.write_text(json.dumps(body))


def test_paged_search_exposes_full_total_and_binds_ir_revision(documents,repository):
    add_sources(documents)
    clause=quantitative(repository)
    intent=SearchIntent(target_id=clause.clause_key,contract_digest=clause.clause_digest,data_kind='quantitative',positive_terms=['能源'],limit=2)
    first=documents.search_page('ir-example',intent)
    assert first.total==5 and first.returned==2 and first.next_cursor
    seen={row.object_id for row in first.objects};cursor=first.next_cursor
    while cursor:
        page=documents.search_page('ir-example',intent.model_copy(update={'cursor':cursor}))
        assert page.query_digest==first.query_digest and page.scope['available_pages']==[0]
        assert not seen.intersection(row.object_id for row in page.objects)
        seen.update(row.object_id for row in page.objects);cursor=page.next_cursor
    assert len(seen)==5
    path=documents.resolve('ir-example')/'page.json';body=json.loads(path.read_text());body['blocks'][0]['text']+='修订';path.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='cursor'):
        documents.search_page('ir-example',intent.model_copy(update={'cursor':first.next_cursor}))


def test_explicit_object_budget_marks_search_incomplete(documents,repository,tmp_path):
    add_sources(documents)
    clause=quantitative(repository)
    request=RunRequest(ir_run_id='ir-example',clause_keys=[clause.clause_key],budget=PipelineBudget(max_objects_per_clause=2))
    result=ExtractionBackbone(documents,Model(),FileArtifactPort(tmp_path)).run('bounded',request,{clause.clause_key:clause})
    coverage=result['coverage'][0]
    assert coverage['search']['total']>=5 and coverage['search']['truncated'] is True
    assert 'object_budget_exhausted' in coverage['reasons']
    assert result['summary']['assessment']['search_coverage']=='incomplete'
