import json
import pytest
from fastapi.testclient import TestClient
from esg_clauses.repository import ClauseRepository,ClauseRepositoryError
from esg_clauses.contract import ClauseContract
from esg_extraction.pipeline.api import create_app
from esg_extraction.pipeline.state import RunStore
from esg_extraction.pipeline.adapters.artifacts import FileArtifactPort
from test_backbone import documents


@pytest.mark.parametrize('kind',['quantitative','qualitative'])
def test_new_clause_has_one_structure_and_stays_blocked_until_explicitly_completed(tmp_path,documents,kind):
    root=tmp_path/'standard';root.mkdir()
    (root/'_catalog.json').write_text(json.dumps({'schema_version':'clause-catalog-3.0','clauses':[]}))
    (root/'selections.json').write_text(json.dumps({'profiles':{}}))
    repo=ClauseRepository(root)
    app=create_app(lambda:(repo,RunStore(tmp_path/'runs.sqlite3'),FileArtifactPort(tmp_path/'artifacts'),documents))
    with TestClient(app) as client:
        assert client.get('/api/clause-contract').status_code==200
        assert client.get('/api/clauses').json()['clauses']==[]
        response=client.post('/api/clauses/template',json={'clause_key':'CUSTOM_01','group_id':'CUSTOM','data_kind':kind,'title':'示例条款'})
        assert response.status_code==200,response.text
        body=response.json();clause=ClauseContract.model_validate(body).verify()
        assert clause.readiness=='blocked' and clause.organization
        assert repo.catalog()['clauses']==[] # Generating/validating never saves it.
        valid=client.post('/api/clauses/validate',json=body)
        assert valid.status_code==200 and valid.json()['scientific_acceptance'] is False
        saved=client.put('/api/clauses/CUSTOM_01',json={'clause':body})
        assert saved.status_code==200
        assert repo.get('CUSTOM_01')==clause
        assert 'path' not in client.get('/api/clauses').json()['clauses'][0]
        assert client.get('/api/clause-groups').json()['groups'][0]['group_id']=='CUSTOM'
        with pytest.raises(ClauseRepositoryError):repo.select(['CUSTOM_01'])
        assert client.put('/api/clauses/CUSTOM_01',json={'clause':body}).status_code==409


def test_structure_navigation_and_eligibility_are_distinct_from_value_execution(tmp_path,documents):
    from pathlib import Path
    root=Path(__file__).resolve().parents[3]/'ClauseESG/clauses'
    repo=ClauseRepository(root)
    app=create_app(lambda:(repo,RunStore(tmp_path/'runs.sqlite3'),FileArtifactPort(tmp_path/'artifacts'),documents))
    with TestClient(app) as client:
        detail=client.get('/api/clauses/E2-4_01')
        assert detail.status_code==200 and detail.json()['schema_version']=='clause-descriptor-1.0'
        assert 'data_kind' not in detail.json()
        assert client.get('/api/clauses/E2-4_01/reading-card').status_code==409
        assert client.get('/api/clauses/E2-4_01/organization').status_code==409
        options=client.post('/api/clause-selection-options',json=['E2-4_01','E2-4_02']).json()
        assert options['eligible']==['E2-4_02'] and options['excluded'][0]['status']=='structural'
