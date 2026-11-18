"""Positive local requirements reduce irrelevant calls without discarding raw values."""
from esg_contracts.evidence import EvidenceContextLink, EvidenceFrame, TextEvidence
from esg_document.document.literals import numeric_rows
from esg_extraction.pipeline.selection import quantitative_candidates
from test_backbone import repository


def text_frame(text, **changes):
    source = TextEvidence(object_id='source', span_id='sentence', page_index=0, text=text)
    return EvidenceFrame(object_id='source', kind='text', page_index=0, digest='source',
        rows=numeric_rows(source), texts=[source], **changes)


def test_unrelated_local_sentence_does_not_start_an_energy_read(repository):
    frame = text_frame('Water consumption was 20 cubic metres.')
    selected, excluded = quantitative_candidates(repository.get('E1-5_01'), frame)
    assert selected.rows == []
    assert excluded and all(item['reason'] == 'label_not_total_energy_consumption' for item in excluded)


def test_local_values_survive_missing_unit_and_period(repository):
    frame = text_frame('Total energy consumption was 20.')
    selected, excluded = quantitative_candidates(repository.get('E1-5_01'), frame)
    assert selected.rows == frame.rows and not excluded


def test_multiple_values_in_the_same_printed_sentence_remain_readable(repository):
    frame = text_frame('Renewable electricity was 5 MWh and solar power was 7 MWh.')
    selected, excluded = quantitative_candidates(repository.get('E1-5_05'), frame)
    assert [cell.raw_text for row in selected.rows for cell in row.cells] == ['5', '7']
    assert not excluded


def test_sibling_text_cannot_supply_an_unrelated_literal_positive_requirement(repository):
    frame = text_frame('Water consumption was 20 cubic metres.')
    sibling = TextEvidence(object_id='other', page_index=0, text='Renewable energy consumption was 50 MWh.')
    frame = frame.model_copy(update={'texts': [*frame.texts, sibling]})
    selected, _ = quantitative_candidates(repository.get('E1-5_05'), frame)
    assert selected.rows == []


def test_actual_graph_label_and_region_title_supply_local_positive_evidence(repository):
    frame = text_frame('Direct emission\n1,482,798')
    label = frame.texts[0].model_copy(update={'span_id': 'label', 'text': 'Direct emission'})
    title = TextEvidence(object_id='title', span_id='title', page_index=0,
        text='2019 Greenhouse Gases Emission Data')
    cell = frame.rows[0].cells[0]
    frame = frame.model_copy(update={'texts': [*frame.texts, label, title], 'context_links': [
        EvidenceContextLink(cell_id=cell.cell_id, source_ids=['label'], relation='column'),
        EvidenceContextLink(cell_id=cell.cell_id, source_ids=['title'], relation='region')]})
    selected, excluded = quantitative_candidates(repository.get('E1-6_07'), frame)
    assert selected.rows == frame.rows and not excluded


def test_local_negative_semantics_are_left_for_selected_literal_binding(repository):
    frame = text_frame('Non-renewable energy intensity was 3 MWh/RMB.')
    selected, excluded = quantitative_candidates(repository.get('E1-5_05'), frame)
    assert selected.rows == frame.rows and not excluded


def test_global_solar_capacity_remains_readable_before_target_relation_binding(repository):
    frame = text_frame('Canadian Solar supplied solar modules with a global capacity of 250 GW.')
    selected, excluded = quantitative_candidates(repository.get('E1-5_05'), frame)
    assert selected.rows == frame.rows and not excluded
