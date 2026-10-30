"""Scientific counterexamples: provenance alone cannot make a digit a quantity."""
import pytest
from esg_contracts.evidence import TextEvidence, EvidenceFrame, EvidenceCell, EvidenceRow
from esg_contracts.facts import RunRequest, ReportedField
from esg_extraction.pipeline.engine import ExtractionBackbone
from esg_extraction.pipeline.binding import context_from_claims, mapping_for
from esg_extraction.pipeline.decisions import IdentityProposal
from esg_extraction.pipeline.adapters.artifacts import FileArtifactPort
from esg_document.document.literals import numeric_rows
from test_backbone import repository, documents, Model, quantitative
from test_organization import fact, mapping, reported


@pytest.mark.parametrize('text,raw,kind', [
    ('公司使用六氟化硫（SF6）气体设置报警装置。', '6', 'formula'),
    ('2由于子公司改进，氮氧化物排放量下降。', '2', 'footnote'),
    ('共有2家工厂实施监测。', '2', 'unresolved'),
    ('报告期排放0吨。', '0', 'measurement'),
    ('报告值为1-2吨。', '1-2', 'measurement'),
    ('2025年排放20吨。', '2025', 'date'),
])
def test_literal_typing_keeps_true_unitless_counts(text, raw, kind):
    rows = numeric_rows(TextEvidence(object_id='p', text=text, page_index=0))
    assert next(c for r in rows for c in r.cells if c.raw_text == raw).candidate_kind == kind


def test_semantic_digit_rejections_never_become_facts(documents, repository, tmp_path):
    clause = quantitative(repository)
    texts = ['SF6气体报警', '2由于子公司改进，污染物排放量下降。', '工厂数量2']
    frames = [EvidenceFrame(object_id=str(i), kind='text', page_index=i,
        rows=numeric_rows(TextEvidence(object_id=str(i), text=text, page_index=i)), digest=str(i)) for i,text in enumerate(texts)]
    class Docs:
        document = documents.document
        def search(self, *args):
            from esg_contracts.evidence import ObjectReference
            return [ObjectReference(object_id=f.object_id, kind='text', page_index=f.page_index, score=1) for f in frames]
        def read(self, run_id, candidate, chunk): return frames[int(candidate.object_id)]
    class Reader(Model):
        def complete(self, **kwargs):
            self.calls.append(kwargs)
            return {'matches': [{'row_id':r['row_id'], 'columns':['value'], 'reason':'real unitless count', 'selection':'measurement'} for r in kwargs['context']['frame']['rows']]}, {}
    model = Reader(); artifacts=FileArtifactPort(tmp_path/'out')
    result=ExtractionBackbone(Docs(), model, artifacts).run('test', RunRequest(ir_run_id='ir-example', clause_keys=[clause.clause_key]), {clause.clause_key:clause})
    assert [f['value']['value_raw'] for f in result['facts']] == ['2']
    assert len(model.calls) == 1
    assert len(artifacts.read('test','diagnostics/candidates.json')) == 2
    assessment=result['summary']['assessment']
    assert assessment['execution_state']=='completed'
    assert assessment['metadata_completeness']=='partial'
    assert assessment['rejected_candidates']==2


def test_final_bound_identity_resolves_prior_attempt_warnings():
    source=TextEvidence(object_id='text',text='2025 SO2',page_index=0)
    frame=EvidenceFrame(object_id='frame',kind='text',page_index=0,digest='x',texts=[source])
    attempts=[]
    context, flags=context_from_claims(frame,[
        IdentityProposal(axis='pollutant',value='SO2',quote='invented',source_id='text'),
        IdentityProposal(axis='pollutant',value='SO2',quote='SO2',source_id='text'),
    ],attempts=attempts)
    assert context.dimensions['pollutant'].value=='SO2'
    assert 'unbound_identity:pollutant' not in flags
    assert 'unbound_identity:pollutant' in attempts


def test_sparse_context_is_received_but_known_medium_conflict_is_separate(documents, repository):
    document=documents.document('ir-example');clause=repository.get('E2-4_02')
    sparse=fact(document,'sparse','1,530.14')
    link=mapping_for(clause,document,sparse,'match','air pollutant')
    assert link.match=='match' and not link.scope_ids
    water=fact(document,'water','175.61',dimensions={'pollution_medium':reported('water')})
    link=mapping_for(clause,document,water,'match','water measurement')
    assert link.match=='different'


def test_matrix_coordinates_ignore_inherited_vs_stated_without_losing_rows(documents,repository):
    from esg_extraction.pipeline.organization import build_organized_dataset
    document=documents.document('ir-example');clause=repository.get('E2-4_02')
    one=fact(document,'one','1',dimensions={'pollutant':reported('SO2')})
    two=fact(document,'two','2',dimensions={'pollutant':reported('SO2')})
    two=two.model_copy(update={'identity':two.identity.model_copy(update={'reporting_period':two.identity.reporting_period.model_copy(update={'state':'inherited'})})})
    table=build_organized_dataset('run',document,{clause.clause_key:clause},[one,two],[mapping(clause,f) for f in [one,two]]).tables[0]
    assert len(table.matrix.columns)==1
    assert len(table.matrix.cells[0].row_ids)==2
    assert len(table.rows)==2
