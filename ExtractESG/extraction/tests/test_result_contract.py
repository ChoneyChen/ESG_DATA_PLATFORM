"""Delivery validation is independent of the report parser, clause repository and reader."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from esg_contracts.results import FactBundle


def bundle():
    document={'document_id':'doc','ir_run_id':'ir','ir_revision':1,'content_digest':'f'*64}
    def fact(key,value,role):return {'source_fact_id':key,'source_anchor_id':key+'-anchor','interpretation_id':'reading-'+key,'document_id':'doc','ir_run_id':'ir','ir_revision':1,'value':value,'evidence':[{'object_id':key,'object_type':'cell' if role=='value' else 'text_block','page_index':0,'excerpt':'0' if role=='value' else '原文陈述','role':role}]}
    return {'engine_version':'test','engine_identity':{'version':'test'},'run_id':'run','document':document,'clause_targets':{'TEST':'a'*64},
        'facts':[fact('number',{'kind':'quantitative','value_raw':'0'},'value'),fact('statement',{'kind':'assertion','statement_raw':'原文陈述'},'statement')],
        'clause_mappings':[{'clause_key':'TEST','clause_digest':'a'*64,'source_fact_id':'number','match':'uncertain','reason':'identity not supplied'}], 'coverage':[],
        'summary':{'selected_clauses':1,'processed_clauses':0,'source_facts':2,'quantitative':1,'qualitative':1,'model_calls':2,'elapsed_seconds':1},
        'organization':{'status':'pending','url':'/api/runs/run/organized'}}


def test_both_value_contracts_share_one_validated_delivery_without_required_metadata():
    result=FactBundle.model_validate(bundle())
    assert result.facts[0].value.value_raw=='0' and result.facts[0].value.unit_raw is None
    assert result.facts[1].value.statement_raw=='原文陈述'


@pytest.mark.parametrize('change',[
 lambda b:b['facts'][0].update(document_id='foreign'),
 lambda b:b['clause_mappings'][0].update(source_fact_id='absent'),
 lambda b:b['clause_mappings'][0].update(clause_digest='b'*64),
 lambda b:b['summary'].update(source_facts=3),
 lambda b:b['facts'].append(deepcopy(b['facts'][0])),
 lambda b:b['coverage'].append({'clause_key':'OTHER','clause_digest':'a'*64,'state':'not_found'}),
])
def test_collection_rejects_broken_identity_links_and_false_counts(change):
    body=bundle();change(body)
    with pytest.raises(ValidationError):FactBundle.model_validate(body)
