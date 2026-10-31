"""One canonical row mapping for physical continuations and extraction evidence."""
from esg_document.document.contracts import TableIR,CellIR,SourceTrace,DocumentIR
from esg_document.document.logical_table_builder import LogicalTableBuilder
from esg_document.document.physical import composed_tables


def table(identity,page,rows):
    trace=SourceTrace(parser='synthetic')
    return TableIR(table_id=identity,page_index=page,order=0,row_count=len(rows),column_count=len(rows[0]),
        header_row_indices=[0],source_trace=trace,continuation_group_id='group',continuation_axis='horizontal',
        cells=[CellIR(cell_id=f'{identity}-{r}-{c}',table_id=identity,page_index=page,row_index=r,col_index=c,text=text,
            is_header=r<2,source_trace=trace) for r,row in enumerate(rows) for c,text in enumerate(row)])


def compile(left,right):
    left.continues_to_table_id=right.table_id;right.continues_from_table_id=left.table_id
    return LogicalTableBuilder._compile(type('Context',(),{'spreads':[]})(),logical_table_id='logical-one',group_id='group',axis='horizontal',tables=[left,right],previous=None)


def test_omitted_blank_section_bands_do_not_shift_measurements_to_adjacent_labels():
    left=table('left',0,[['Item','Group A'],['','2023'],['First','1'],['Section',''],['Second','0'],['Another section',''],['Third','3']])
    right=table('right',1,[['Group B','Group C'],['2023','2024'],['10','11'],['20','21'],['30','31']])
    result=compile(left,right)
    assert result.status=='derived'
    mapping={cell.source_cell_id:cell.logical_row_index for cell in result.cell_mappings}
    assert [mapping[f'right-{r}-0'] for r in (2,3,4)]==[2,4,6]
    projected=composed_tables([left.model_dump(mode='json'),right.model_dump(mode='json')],[result.model_dump(mode='json')])
    cells={(c['row_index'],c['col_index']):c for c in projected[0]['cells']}
    assert cells[(4,0)]['text']=='Second' and cells[(4,2)]['text']=='20'
    assert cells[(4,2)]['cell_id']=='right-3-0' and cells[(4,2)]['page_index']==1
    assert len(cells)==len(left.cells)+len(right.cells)


def test_unresolved_join_cannot_reappear_through_a_separate_evidence_alignment():
    left=table('left',0,[['Item','A'],['','2023'],['First','1'],['Section',''],['Second','2']])
    right=table('right',1,[['B','C'],['2023','2024'],['10','11']])
    result=compile(left,right)
    assert result.status=='review_required'
    physical=[left.model_dump(mode='json'),right.model_dump(mode='json')]
    assert composed_tables(physical,[result.model_dump(mode='json')])==physical
    assert composed_tables(physical,[])==physical


def test_period_groups_preserve_full_parent_header_with_all_physical_sources():
    from esg_document.document.physical import build_table_view,column_header_sources
    physical=table('wide',0,[['Item','Ports','and Related','Services','','Retail',''],['','2023','2024','2025','2023','2024','2025'],['Actual amount','1','2','3','0','4','5']])
    body=physical.model_dump(mode='json');view=build_table_view(body,None)
    sources,grouped=column_header_sources(body,view.header_rows)
    assert grouped
    assert view.column_paths[1]==['Ports and Related Services','2023']
    assert view.column_paths[4]==['Retail','2023']
    assert [cell['cell_id'] for cell in sources[1] if cell['row_index']==0]==['wide-0-1','wide-0-2','wide-0-3']
