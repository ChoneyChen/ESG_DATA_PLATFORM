import pytest
from esg_contracts.evidence import ObjectReference
from esg_contracts.facts import RunRequest, PipelineBudget
from esg_extraction.pipeline.engine import ExtractionBackbone
from esg_extraction.pipeline.adapters.artifacts import FileArtifactPort
from esg_extraction.models.errors import ModelRunFailure
from esg_extraction.models.mlx_runtime import PromptBudgetExceeded
from test_structure_contract import frame, document
from test_backbone import repository, quantitative


@pytest.mark.parametrize('failure',['generation_incomplete','input_budget'])
def test_output_or_input_limit_splits_rows_and_retains_all_cells(repository,tmp_path,failure):
    original=frame({'a':'2023','b':'2024'})
    original=original.model_copy(update={'rows':[
        original.rows[0].model_copy(update={'row_id':f'r-{i}','cells':[
            cell.model_copy(update={'cell_id':cell.cell_id+str(i)}) for cell in original.rows[0].cells]})
        for i in range(2)]})
    class Docs:
        def document(self,_):return document()
        def search(self,*_):return [ObjectReference(object_id='t',kind='table',page_index=0,score=1)]
        def read(self,*_):return original
    class Reader:
        def complete(self,**kwargs):
            bounded=kwargs['context']['frame']
            if len(bounded['rows'])>1:
                if failure=='input_budget':raise PromptBudgetExceeded('bounded request is too large')
                raise ModelRunFailure('output limit',category=failure,raw_output='{"matches":[')
            assert bounded['fragment']['completeness']=='fragment'
            assert kwargs['context']['execution']['attempt']==1
            assert kwargs['context']['execution']['parent_task_id'].endswith('/calls/1')
            return {'matches':[{'row_id':row['row_id'],'columns':[cell['column_id'] for cell in row['cells']],
                'selection':'measurement','reason':'reported cells'} for row in bounded['rows']]},{}
    clause=quantitative(repository);artifacts=FileArtifactPort(tmp_path)
    result=ExtractionBackbone(Docs(),Reader(),artifacts).run('split',RunRequest(ir_run_id='ir',clause_keys=[clause.clause_key]),{clause.clause_key:clause})
    assert len(result['facts'])==4
    assert result['summary']['model_calls']==3
    assert not result['summary']['errors']
    assert result['summary']['assessment']['execution_state']=='completed'
    assert result['summary']['assessment']['periods']=={'2023':2,'2024':2}
    assert result['coverage'][0]['inspected_objects']==1
    trace=artifacts.read('split','diagnostics/model-calls.json')
    assert trace[0]['status']=='split_retry' and len(trace[0]['retry_frames'])==2
    assert trace[1]['parent_call_index']==1 and trace[2]['parent_call_index']==1


def test_fragmentation_preserves_text_characters_and_parent_identity():
    from esg_contracts.evidence import EvidenceFrame,TextEvidence
    from esg_extraction.pipeline.frames import split_frame
    source='Complete policy statement. '*40
    original=EvidenceFrame(object_id='p',kind='text',page_index=2,digest='original',
        texts=[TextEvidence(object_id='p',span_id='span',text=source,page_index=2,char_start=10)])
    pieces=split_frame(original)
    assert ''.join(piece.texts[0].text for piece in pieces)==source
    for piece in pieces:
        text=piece.texts[0]
        offset=text.char_start-10
        assert source[offset:offset+len(text.text)]==text.text
        assert piece.fragment.parent_digest=='original'
