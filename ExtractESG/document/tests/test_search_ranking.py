"""A shared topic heading must not outrank an actual labelled measurement."""
from types import SimpleNamespace
from esg_contracts.evidence import SearchIntent
from esg_document.document.search_ranking import query_terms, table_score, text_score


def view(label, value="16,864", unit=None):
    return SimpleNamespace(title="Absolute Scope 1, 2 and 3 Emissions", caption=None,
        heading_above=None, column_paths={}, notes=[], lines=[SimpleNamespace(
            label=label, section=None, unit=unit,
            values={1:SimpleNamespace(text=value)} if value is not None else {})])


def test_shared_scope_heading_keeps_unlabelled_tables_below_readable_prose():
    intent=SearchIntent(target_id="test",contract_digest="d"*64,positive_terms=["Scope 1"],source_priority=["table","text"],
        data_kind="quantitative",unit_dimensions=["ghg-emissions"])
    terms=query_terms(intent.positive_terms)
    prose=text_score(intent,terms,"Scope 1 emissions were 16,864 tCO2e.")
    weak=table_score(intent,terms,{"cells":[]},view(""))
    explicit=table_score(intent,terms,{"cells":[]},view("Scope 1",unit="tCO2e"))
    assert weak <= prose < explicit
    assert table_score(intent,terms,{"cells":[]},view("Scope 1",value=None)) == 0


def test_unreadable_table_stays_a_candidate_without_container_priority():
    intent=SearchIntent(target_id="test",contract_digest="d"*64,positive_terms=["Scope 1"],source_priority=["table","text"],
        data_kind="quantitative")
    terms=query_terms(intent.positive_terms)
    raw={"cells":[{"text":"Scope 1"},{"text":"16,864"}]}
    assert 0 < table_score(intent,terms,raw,None) <= text_score(intent,terms,"Scope 1 16,864")


def test_sibling_sentence_does_not_penalize_a_direct_measurement():
    intent=SearchIntent(target_id="test",contract_digest="d"*64,positive_terms=["Scope 1"],
        negative_terms=["Scope 2"],data_kind="quantitative")
    terms=query_terms(intent.positive_terms)
    direct="Scope 1 emissions were 16,864 tCO2e. "
    mixed=direct+"Scope 2 emissions were 1,788,759 tCO2e."
    assert text_score(intent,terms,mixed) == text_score(intent,terms,direct)


def test_unparsed_scientific_literal_remains_retrievable():
    intent=SearchIntent(target_id="test",contract_digest="d"*64,positive_terms=["Scope 1"],
        data_kind="quantitative")
    assert table_score(intent,query_terms(intent.positive_terms),{"cells":[]},
        view("Scope 1",value="1.23×10⁵")) > 0
