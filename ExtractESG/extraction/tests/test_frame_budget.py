from esg_contracts.evidence import ObjectSearchPage
import pytest
from esg_contracts.evidence import ObjectReference
from esg_contracts.facts import RunRequest, PipelineBudget
from esg_extraction.pipeline.engine import ExtractionBackbone
from esg_extraction.pipeline.adapters.artifacts import FileArtifactPort
from esg_extraction.models.errors import ModelRunFailure
from esg_extraction.models.mlx_runtime import PromptBudgetExceeded
from test_structure_contract import frame, document
from test_backbone import repository, quantitative, run_backbone


@pytest.mark.parametrize('failure',['generation_incomplete','input_budget'])
def test_output_or_input_limit_splits_rows_and_retains_all_cells(repository,tmp_path,failure):
    original=frame({'a':'2023','b':'2024'})
    original=original.model_copy(update={'rows':[
        original.rows[0].model_copy(update={'row_id':f'r-{i}','cells':[
            cell.model_copy(update={'cell_id':cell.cell_id+str(i)}) for cell in original.rows[0].cells]})
        for i in range(2)]})
    class Docs:
        def document(self,_):return document()
        def search_page(self,*_):return ObjectSearchPage(objects=[ObjectReference(object_id='t',kind='table',page_index=0,score=1)],total=1,returned=1,query_digest='fixed',scope={'available_pages':[0]})
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
            return {'matches':[{'row_id':row['row_id'],'columns':[cell['column_id'] for cell in row['cells']]}
                for row in bounded['rows']]},{}
    clause=quantitative(repository);artifacts=FileArtifactPort(tmp_path)
    clause=clause.model_copy(update={'purposes':clause.purposes.model_copy(update={
        'enrichment':clause.purposes.enrichment.model_copy(update={'identity_axes':[]})})}).seal()
    events=[]
    result=run_backbone(Docs(),Reader(),artifacts,'split',RunRequest(ir_run_id='ir',clause_keys=[clause.clause_key]),{clause.clause_key:clause},on_progress=events.append)
    assert any(e.get('fact_count')==2 and e.get('current')==0 for e in events)
    assert [e['model_calls'] for e in events if e['stage']=='reading']==[1,2,3]
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


def test_single_sentence_without_boundary_is_not_split_mid_statement():
    from esg_contracts.evidence import EvidenceFrame,TextEvidence
    from esg_extraction.pipeline.frames import split_frame
    source='公司通过连续在线监测及定期委托检测落实污染物排放管理并保存监测记录'*20
    original=EvidenceFrame(object_id='p',kind='text',page_index=3,digest='whole-statement',
        texts=[TextEvidence(object_id='p',span_id='span',text=source,page_index=3,char_start=73)])
    assert split_frame(original)==[]
    assert original.texts[0].text==source and original.texts[0].char_start==73


@pytest.mark.parametrize('boundary',['。','！','？','. ','! ','? ','\n\n'])
def test_sentence_boundary_fragments_keep_exact_source_offsets(boundary):
    from esg_contracts.evidence import EvidenceFrame,TextEvidence
    from esg_extraction.pipeline.frames import split_frame
    first='排放监测流程及实际检测记录'*14+boundary
    second='所有检测结果均按要求记录并保持可追溯'*14
    source=first+second
    original=EvidenceFrame(object_id='original-paragraph',kind='text',page_index=7,digest='original',
        texts=[TextEvidence(object_id='original-paragraph',span_id='span',text=source,page_index=7,char_start=137)])
    pieces=split_frame(original)
    assert len(pieces)==2
    assert [piece.texts[0].text for piece in pieces]==[first,second]
    assert [piece.texts[0].char_start for piece in pieces]==[137,137+len(first)]
    for piece in pieces:
        text=piece.texts[0]
        start=text.char_start-137
        assert source[start:start+len(text.text)]==text.text
        assert text.object_id=='original-paragraph' and text.page_index==7
        assert piece.fragment.parent_digest=='original'


def test_numeric_split_and_json_checkpoint_preserve_full_context_and_original_links():
    from esg_contracts.evidence import EvidenceFrame,TextEvidence,EvidenceContextLink
    from esg_document.document.literals import numeric_rows
    from esg_extraction.pipeline.frames import split_frame
    text=TextEvidence(object_id='figure',span_id='whole',page_index=0,
        text='Direct GHG emissions 100tCO2e\nIndirect GHG emissions 200tCO2e\nOther entity information')
    rows=[row for row in numeric_rows(text) if row.cells[0].candidate_kind in {'measurement','unresolved'}]
    first=text.model_copy(update={'span_id':'first','text':'Direct GHG emissions'})
    second=text.model_copy(update={'span_id':'second','char_start':text.text.index('Indirect'),'text':'Indirect GHG emissions'})
    links=[EvidenceContextLink(cell_id=row.cells[0].cell_id,source_ids=[source],relation='column')
        for row,source in zip(rows,['first','second'])]
    frame=EvidenceFrame(object_id='figure',kind='text',page_index=0,digest='physical',
        rows=rows,texts=[text,first,second],context_links=links)
    pieces=split_frame(frame,quantitative=True)
    assert len(pieces)==2
    for piece in pieces:
        restored=EvidenceFrame.model_validate_json(piece.model_dump_json())
        assert restored.texts==frame.texts
        assert len(restored.rows)==1 and len(restored.context_links)==1
        assert restored.context_links[0] in frame.context_links
        assert restored.context_links[0].cell_id==restored.rows[0].cells[0].cell_id
        assert restored.fragment.parent_digest==frame.digest


def test_decision_arrays_are_bounded_by_actual_source_choices():
    from esg_contracts.evidence import EvidenceFrame, EvidenceRow, EvidenceCell, TextEvidence
    from esg_extraction.pipeline.decisions import decision_schema
    frame=EvidenceFrame(object_id="source",kind="text",page_index=0,digest="d"*64,rows=[
        EvidenceRow(row_id="r1",label="Water",cells=[
            EvidenceCell(cell_id="c1",column_id="value",raw_text="4.477")])],
        texts=[TextEvidence(object_id="source",object_type="text_block",page_index=0,text="Water 4.477")])
    matches=decision_schema("quantitative",frame)["properties"]["matches"]
    assert matches["maxItems"] == 1 and matches["uniqueItems"]
    assert matches["items"]["properties"]["columns"]["maxItems"] == 1
    assert decision_schema("qualitative",frame)["properties"]["matches"]["maxItems"] == 1
