import json
import os
from pathlib import Path
import shutil
import subprocess
from esg_database.client import DatabaseBridge, DatabaseError
import pytest


def test_capture_worker_cold_starts_with_no_legacy_dictionary(tmp_path):
    root=Path(__file__).resolve().parents[1]
    environment={key:value for key,value in os.environ.items() if key not in {
        'ESG_DATABASE_URL','PLATESG_DATABASE_URL','PLATESG_DATA_DIR','PLATESG_REVIEW_TOKEN','PLATESG_OPERATORS_JSON','ESG_DATABASE_WORKER'}}
    environment.update(ESG_DATABASE_DATA_DIR=str(tmp_path/'capture'),ESG_DATABASE_PROFILE='capture',ESG_DATABASE_MODULE_ROOT=str(root))
    node=shutil.which('node')
    assert node
    result=subprocess.run([node,str(root/'runtime/manage.mjs'),'init','--profile','capture'],
        env=environment,capture_output=True,text=True,check=True)
    status=json.loads(result.stdout)
    assert status['ready'] and status['products_ready']
    bridge=DatabaseBridge(environment)
    try:
        bridge.start()
        assert bridge.call('health')['profile']=='capture'
        assert bridge.call('ingestion_health')['ready']
        with pytest.raises(DatabaseError) as caught:
            bridge.call('candidates',{})
        assert caught.value.code=='unknown_command'
    finally:bridge.close()
