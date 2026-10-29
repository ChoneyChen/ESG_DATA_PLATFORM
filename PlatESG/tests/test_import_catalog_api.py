from fastapi.testclient import TestClient
from PlatESG.backend.main import create_app


def test_retired_ingress_cannot_reactivate_package_catalogs(tmp_path):
    app=create_app({'PLATESG_DATA_DIR':str(tmp_path/'data'),'PLATESG_AUTO_PUBLISH':'0','PLATESG_REVIEW_TOKEN':'fixture-admin'})
    with TestClient(app) as client:
        auth={'Authorization':'Bearer fixture-admin'}
        before=client.get('/v1/datasets').json()
        assert client.get('/internal/v1/import-catalogs').status_code==401
        assert client.get('/internal/v1/import-catalogs',headers=auth).json()=={'allowed':[],'items':[]}
        response=client.post('/internal/v1/import-catalogs/register',headers=auth,json={'contract_id':'retired','expected_release_sha256':'0'*64,'reason':'retirement check'})
        assert response.status_code==410 and response.json()['detail']['code']=='legacy_production_retired'
        for endpoint in ['/internal/v1/import-batches','/internal/v1/deliveries']:
            assert client.post(endpoint,headers=auth,json={}).status_code==410
        assert client.get('/v1/datasets').json()==before
