import json
from pathlib import Path

from fastapi.testclient import TestClient

from PlatESG.backend.main import create_app


def test_import_only_registry_uses_authenticated_admin_and_fixed_allowlist(tmp_path):
    operators=[{'token':'fixture-read','operator_ref':'fixture-reader','roles':['read']},
               {'token':'fixture-admin','operator_ref':'fixture-admin','roles':['read','admin']}]
    app=create_app({'PLATESG_DATA_DIR':str(tmp_path/'data'),'PLATESG_AUTO_PUBLISH':'0',
                    'PLATESG_OPERATORS_JSON':json.dumps(operators),'PLATESG_LIFECYCLE_STORE_SCOPE':'isolated'})
    index=json.loads((Path(__file__).resolve().parents[1]/'catalog/import-contracts/index.json').read_text())
    allowed=index['releases'][0]
    body={'contract_id':allowed['contract_id'],'expected_release_sha256':allowed['sha256'],'reason':'隔离认证注册验收'}
    with TestClient(app) as client:
        before=client.get('/v1/datasets').json()
        assert client.get('/internal/v1/import-catalogs').status_code==401
        assert client.post('/internal/v1/import-catalogs/register',json=body).status_code==401
        read={'Authorization':'Bearer fixture-read'}
        admin={'Authorization':'Bearer fixture-admin'}
        assert client.get('/internal/v1/import-catalogs',headers=read).json()['items']==[]
        assert client.post('/internal/v1/import-catalogs/register',headers=read,json=body).status_code==403
        assert client.post('/internal/v1/import-catalogs/register',headers=admin,json={**body,'dictionary_path':'/tmp/fake'}).status_code==422
        assert client.post('/internal/v1/import-catalogs/register',headers=admin,json={**body,'operator_ref':'forged'}).status_code==422
        result=client.post('/internal/v1/import-catalogs/register',headers=admin,json=body)
        assert result.status_code==200, result.text
        assert result.json()['registered_by']=='fixture-admin'
        assert result.json()['permission']=='import_only'
        assert client.get('/internal/v1/import-catalogs',headers=read).json()['items'][0]['dictionary_version']=='1.0.0'
        unknown=client.post('/internal/v1/import-catalogs/register',headers=admin,json={**body,'contract_id':'../../unknown'})
        assert unknown.status_code==422
        assert unknown.json()['code']=='import_catalog_unregistered'
        assert client.get('/v1/datasets').json()==before
