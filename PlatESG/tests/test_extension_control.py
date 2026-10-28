from copy import deepcopy
import json
from pathlib import Path

import pytest

from PlatESG.backend.extension_control import ExtensionControl
from PlatESG.backend.extension_dictionary import build_extension_dictionary
from esg_standard_packages.clause_lifecycle import (
    ClauseRef, evaluate_run, extension_contract_digest, record_acceptance,
)
from esg_standard_packages.extensions import ExtensionProposal

ROOT=Path(__file__).resolve().parents[2]
STANDARDS=ROOT/'ExtractESG/standards'
DRAFT=STANDARDS/'extensions/authoring/esrs/2023-set1/e1-6/scope1-scope2-total/1.0.0/proposal.json'


class AuthorityBridge:
    def __init__(self):
        self.proposals=[];self.decisions=[];self.releases=[]

    def call(self,method,payload):
        if method=='extension_registry':
            return deepcopy({'proposals':self.proposals,'decisions':self.decisions,'releases':self.releases})
        if method=='extension_propose':
            self.proposals.append({'proposal':deepcopy(payload['proposal']),'submitted_by':payload['submitted_by']})
            return {'proposal_id':payload['proposal']['proposal_id']}
        if method=='extension_decide':
            self.decisions.append({'approval_id':payload['receipt']['approval_id'],'receipt':deepcopy(payload['receipt'])})
            return {'approval_id':payload['receipt']['approval_id']}
        if method=='extension_register':
            self.releases.append({'release':deepcopy(payload['release'])})
            return {'registered':True}
        raise AssertionError(method)


def proposal():
    value=json.loads(DRAFT.read_text())
    value['proposer_kind']='human';value['proposer_ref']='authenticated-admin'
    return ExtensionProposal.model_validate(value)


def control(tmp_path):
    bridge=AuthorityBridge()
    return ExtensionControl(bridge,standards_root=STANDARDS,runtime_root=tmp_path/'runtime'),bridge


def test_proposal_and_definition_approval_do_not_activate_extraction(tmp_path):
    service,bridge=control(tmp_path)
    principal={'operator_ref':'authenticated-admin','roles':['admin']}
    p=proposal();service.propose(p,principal)
    result=service.decide(p.proposal_id,decision='approved',reason='核对原点关系、方法和公式后批准定义',principal=principal)
    assert result['approval']['actor_ref']=='authenticated-admin'
    assert result['registration_state']=='not_registered'
    assert bridge.releases==[] and not (service.runtime_root/'registry.json').exists()
    registered=service.register(p.proposal_id,result['approval']['approval_id'])
    assert registered['runtime_projection_state']=='ready'
    assert len(json.loads((service.runtime_root/'registry.json').read_text())['registrations'])==1
    assert service.catalog_view()['runtime_projection']['state']=='ready'


def test_untrusted_actor_or_unstored_release_cannot_project(tmp_path):
    service,_=control(tmp_path)
    with pytest.raises(ValueError,match='authenticated actor'):
        service.propose(proposal(),{'operator_ref':'different-actor'})
    with pytest.raises(ValueError,match='must exist'):
        service.register('unsubmitted','fabricated-approval')


def test_rejected_definition_cannot_compile_or_register(tmp_path):
    service,bridge=control(tmp_path)
    principal={'operator_ref':'authenticated-admin','roles':['admin']}
    p=proposal();service.propose(p,principal)
    result=service.decide(p.proposal_id,decision='rejected',reason='方法条件仍需补充',principal=principal)
    with pytest.raises(ValueError,match='approval'):
        service.register(p.proposal_id,result['approval']['approval_id'])
    assert bridge.releases==[]


def test_platform_selection_preserves_original_definitions_and_missing_context(tmp_path):
    service,bridge=control(tmp_path)
    principal={'operator_ref':'authenticated-admin','roles':['admin']}
    p=proposal();service.propose(p,principal)
    a=service.decide(p.proposal_id,decision='approved',reason='隔离的合同验收批准',principal=principal)
    service.register(p.proposal_id,a['approval']['approval_id'])
    original=json.loads((ROOT/'PlatESG/catalog/metric-dictionary.v1.json').read_text())
    before=deepcopy(original)
    dictionary=build_extension_dictionary(original,bridge.releases,[p.definition.extension_id],'1.1.0')
    assert original==before and dictionary['metrics'][:len(before['metrics'])]==before['metrics']
    extension=dictionary['metrics'][-1]
    assert extension['source_datapoint_id'] is None
    assert extension['related_source_datapoint_ids']==['E1-6_07','E1-6_09','E1-6_10']
    assert 'amount_basis' not in extension['required_dimensions']
    assert 'scope_2_method' not in extension['fixed_dimensions']
    assert extension['comparison_allowed_values']['amount_basis']==['gross']
    assert extension['extension_binding']['parent_source_digest']==p.definition.parent_source_digest
    with pytest.raises(ValueError,match='new dictionary'):
        build_extension_dictionary(original,bridge.releases,[p.definition.extension_id],original['dictionary_version'])
    with pytest.raises(ValueError,match='registered'):
        build_extension_dictionary(original,[],[p.definition.extension_id],'1.1.0')


def _real_run(metric_id: str) -> dict:
    return {
        'records': {
            'reporting_tasks': [{'task_id': 'task-1', 'metric_id': metric_id, 'task_status': 'completed',
                                 'found_status': 'reported', 'ir_run_id': 'ir-1', 'document_id': 'doc-1'}],
            'quantitative_observations': [{'observation_id': 'obs-1', 'task_id': 'task-1',
                                           'value_raw': '1234.5', 'value_numeric': '1234.5',
                                           'unit_raw': '兆瓦时', 'reporting_year': 2025,
                                           'reporting_boundary': 'group', 'evidence_set_id': 'evset-1'}],
            'evidence_references': [{'evidence_id': 'evidence-1', 'evidence_set_id': 'evset-1',
                                     'ir_object_id': 'cell-p0010-t0001-r0004-c0003', 'pdf_page_index': 9}],
        },
        'source_facts': {'gaps': []},
        'summary': {'document': {'document_id': 'doc-1', 'document_label': '测试报告'}},
    }


def test_extension_needs_real_acceptance_before_major_selection_or_import(tmp_path):
    service,bridge=control(tmp_path)
    service.ledger_path=tmp_path/'ledger.json'
    principal={'operator_ref':'authenticated-admin','roles':['admin']}
    p=proposal();service.propose(p,principal)
    approval=service.decide(p.proposal_id,decision='approved',reason='隔离的合同验收批准',principal=principal)
    service.register(p.proposal_id,approval['approval_id'])
    with pytest.raises(ValueError,match='真实验收'):
        service.require_accepted([p.definition.extension_id],purpose='平台主要配置')
    assert service.catalog_view()['major_eligible_extension_ids']==[]
    clause=ClauseRef(kind='extension',extension_id=p.definition.extension_id,
                     definition_version=p.definition.definition_version)
    acceptance=evaluate_run(_real_run(p.definition.extension_id),clause=clause,
                            metric_ids=[p.definition.extension_id],
                            contract_digest_value=extension_contract_digest(p.definition),
                            run_ref='tx-fixture',recorded_by='operator',reason='真实报告试提取')
    assert acceptance.outcome=='accepted'
    record_acceptance(service.ledger_path,acceptance)
    service.require_accepted([p.definition.extension_id],purpose='平台主要配置')
    view=service.catalog_view()
    assert view['major_eligible_extension_ids']==[p.definition.extension_id]
    assert view['lifecycle'][0]['publishable'] is True
