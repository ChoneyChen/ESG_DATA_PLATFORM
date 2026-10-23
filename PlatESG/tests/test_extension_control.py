from copy import deepcopy
import json
from pathlib import Path

import pytest

from PlatESG.backend.extension_control import ExtensionControl
from PlatESG.backend.extension_dictionary import build_extension_dictionary
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
