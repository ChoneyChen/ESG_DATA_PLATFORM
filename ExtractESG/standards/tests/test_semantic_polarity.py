import hashlib
import json
from pathlib import Path
import shutil

import pytest

from esg_standard_packages.compiler import StandardPackageCompiler
from esg_standard_packages.errors import PackageIntegrityError
from esg_standard_packages.contracts import MeasurementContract

ROOT = Path(__file__).resolve().parents[1]
COMPILER = StandardPackageCompiler()


@pytest.mark.parametrize('code', ['e1-5','e1-6','e2-4'])
def test_legacy_measurement_contract_serialization_preserves_wire_fields(code):
    raw=json.loads((ROOT/f'releases/esrs.2023-set1.{code}/2.1.1/package.json').read_text())
    for metric in raw['metrics']:
        prior=metric['measurement_contract']
        contract=MeasurementContract.model_validate(prior)
        assert contract.model_dump(mode='json')==prior
        assert contract.semantic_contract_version=='legacy-unpartitioned-v1'


@pytest.mark.parametrize('code,count', [('e1-5',23),('e1-6',35),('e2-4',20)])
def test_partitioned_revision_preserves_ids_and_all_positive_guidance(code,count):
    old_path=ROOT/f'releases/esrs.2023-set1.{code}/2.1.1/package.json'
    before=old_path.read_bytes()
    old=COMPILER.validate_compiled(old_path)
    new=COMPILER.validate_compiled(ROOT/f'releases/esrs.2023-set1.{code}/2.1.2/package.json')
    assert len(old.metrics)==len(new.metrics)==count
    assert [m.source_datapoint_id for m in old.metrics]==[m.source_datapoint_id for m in new.metrics]
    assert len(COMPILER.semantic_diagnostics(old))==count
    assert COMPILER.semantic_diagnostics(new)==[]
    for prior,metric in zip(old.metrics,new.metrics):
        contract=metric.measurement_contract
        assert prior.description==metric.description
        assert prior.description not in contract.exclusions
        assert prior.description in [contract.positive_definition,*contract.interpretation_guidance]
        assert contract.semantic_contract_version=='partitioned-semantics-v1'
        assert prior.provenance==metric.provenance
    assert old_path.read_bytes()==before
    index=json.loads((ROOT/'registry/releases.json').read_text())
    registered=next(r for r in index['releases'] if r['package_id']==old.manifest.package_id and r['package_version']=='2.1.1')
    assert hashlib.sha256(before).hexdigest()==registered['sha256']


@pytest.mark.parametrize('mutation', ['positive_as_exclusion','missing_positive','legacy_in_new_revision'])
def test_new_authoring_cannot_reintroduce_self_exclusion_or_unpartitioned_semantics(tmp_path,mutation):
    source=tmp_path/'source';shutil.copytree(ROOT/'authoring/esrs/2023-set1/e1-5/2.1.2',source)
    path=source/'metrics.json';metrics=json.loads(path.read_text());contract=metrics[0]['measurement_contract']
    if mutation=='positive_as_exclusion':
        contract['exclusions'].append(contract['positive_definition']+'。')
    elif mutation=='missing_positive':contract['positive_definition']=' '
    else:contract['semantic_contract_version']='legacy-unpartitioned-v1'
    path.write_text(json.dumps(metrics,ensure_ascii=False))
    core_version=json.loads((source/'manifest.json').read_text())['core_schema_version']
    with pytest.raises(PackageIntegrityError,match='semantic contract failed'):
        COMPILER.compile(core_path=ROOT/f'schemas/core/{core_version}/core.json',module_dir=source)
