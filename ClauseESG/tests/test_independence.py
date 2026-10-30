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
    from esg_clauses.contract import EnrichmentPolicy
    clause=new_clause('TEST_02','TEST','quantitative','用量')
    clause=clause.model_copy(update={'purposes':clause.purposes.model_copy(update={'enrichment':EnrichmentPolicy(identity_axes=['new_local_property'])})}).seal()
    fields=clause.reading_card()['requested_identity_fields']
    assert [field['axis'] for field in fields]==['new_local_property']
    assert 'comparison_axes' not in clause.model_dump(mode='json')['value']
