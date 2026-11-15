from esg_clauses.templates import new_clause
from esg_clauses.search import compile_search_plan


def test_clause_sdk_owns_capture_and_search_without_any_executor():
    clause=new_clause('TEST_01','TEST','quantitative','实际用量')
    clause.verify()
    assert clause.purposes.capture.missing_metadata=='retain'
    assert clause.purposes.enrichment.missing_action=='annotate'
    plan=compile_search_plan(clause)
    assert plan['page']['contract_digest']==clause.clause_digest
    assert plan['objects']['exact_terms']==clause.exact_labels
    assert set(clause.retrieval.model_dump()) <= set(plan['objects'])


def test_enrichment_selection_is_independent_of_the_display_columns():
    from esg_clauses.attributes import AttributeDefinition
    from esg_clauses.contract import EnrichmentPolicy
    clause=new_clause('TEST_02','TEST','quantitative','用量')
    attributes={**clause.attributes,'new_local_property':AttributeDefinition(
        label='本地属性',source='dimension',context_levels=['local'])}
    clause=clause.model_copy(update={
        'attributes':attributes,
        'purposes':clause.purposes.model_copy(update={
            'enrichment':EnrichmentPolicy(identity_axes=list(attributes))})}).seal()
    fields=clause.reading_card()['requested_identity_fields']
    assert [field['axis'] for field in fields]==list(attributes)
    assert fields[-1]['context_levels']==['local']
    assert 'new_local_property' not in clause.organization.presentation.columns
    hidden=clause.model_copy(update={'organization':clause.organization.model_copy(update={
        'presentation':clause.organization.presentation.model_copy(update={'columns':['raw_value']})})}).seal()
    assert hidden.reading_card()['requested_identity_fields']==fields
    assert hidden.enrichment_card()['requested_identity_fields']==fields
    assert 'comparison_axes' not in clause.model_dump(mode='json')['value']


def test_optional_attribute_value_pattern_preserves_legacy_frozen_shape():
    import pytest
    from esg_clauses.attributes import AttributeDefinition
    from esg_clauses.contract import ClauseContract
    original=new_clause('TEST_03','TEST','quantitative','原值')
    frozen=original.model_dump(mode='json')
    assert all('value_pattern' not in attribute for attribute in frozen['attributes'].values())
    loaded=ClauseContract.model_validate(frozen)
    loaded.verify()
    assert loaded.clause_digest==original.clause_digest
    attribute=AttributeDefinition(label='实际边界',source='identity',value_pattern=r'(?s).*[^\W\d_].*')
    assert attribute.accepts_value('Site 2030')
    assert attribute.accepts_value(['全球集团',{'site':'Factory 2'}])
    assert not attribute.accepts_value('2030')
    assert not attribute.accepts_value({'site':'2030'})
    assert not attribute.accepts_value([])
    open_attribute=AttributeDefinition(label='开放属性',source='dimension')
    assert all(open_attribute.accepts_value(value) for value in [23,True,{'code':23},['source']])
    with pytest.raises(ValueError,match='valid regular expression'):
        AttributeDefinition(label='无效约束',source='dimension',value_pattern='[')
