"""One canonical row mapping for physical continuations and extraction evidence."""
import pytest


def test_printed_thousands_spacing_preserves_quantities_and_unit_columns():
    from decimal import Decimal
    from esg_data.literals import parse_number
    from esg_document.document.physical import build_table_view
    physical=table('spaced-numbers',0,[['Metric','Unit','2022','2021','2020'],
        ['Scope 1','tCO2 e','158, 202','94, 750','92, 665'],
        ['Scope 2','tCO2 e','2, 930, 501','3, 057, 224','2, 480, 349']])
    view=build_table_view(physical.model_dump(mode='json'),None)
    assert view.unit_col==1 and view.value_cols==[2,3,4]
    assert view.lines[0].label=='Scope 1' and view.lines[0].unit=='tCO2 e'
    assert view.lines[0].values[2].text=='158, 202'
    assert parse_number('158, 202')==Decimal('158202')
    assert parse_number('2, 930, 501')==Decimal('2930501')
    assert parse_number('2023 2024') is None


def test_literal_inventory_preserves_spaced_digits_and_explicit_note_roles():
    from esg_document.document.literals import numeric_rows
    from esg_contracts.evidence import TextEvidence
    quantity=TextEvidence(object_id='actual-cell',object_type='cell',page_index=1,
        text='Emissions were 158, 202 tCO2e.')
    row=numeric_rows(quantity)[0]
    assert row.cells[0].raw_text=='158, 202'
    assert row.cells[0].source_object_type=='cell'
    assert quantity.text[row.cells[0].char_start:row.cells[0].char_end]=='158, 202'
    for text in ['Note 1 Direct emissions','Footnote (2) Indirect emissions','附註3 排放方法',
                 '(2) Direct emissions (Scope 1) are calculated according to the protocol.']:
        assert numeric_rows(TextEvidence(object_id='note',page_index=1,text=text))[0].cells[0].candidate_kind=='footnote'
    actual=TextEvidence(object_id='actual',page_index=1,text='Note: 1 tonne was emitted.')
    assert numeric_rows(actual)[0].cells[0].candidate_kind=='measurement'
    unknown=TextEvidence(object_id='count',page_index=1,text='工厂数量1')
    assert numeric_rows(unknown)[0].cells[0].candidate_kind=='unresolved'


def test_printed_word_multipliers_agree_across_language_and_unit_spellings():
    from decimal import Decimal
    from esg_data.quantities import unit_scale
    assert unit_scale('million cubic metres')==('volume',Decimal('1e6'))
    assert unit_scale('百萬立方米')==unit_scale('million cubic metres')
    assert unit_scale('million liters')==('volume',Decimal('1e3'))
    assert unit_scale('billion kWh')==('energy',Decimal('1e6'))
    assert unit_scale('thousand metric tonnes')==('mass',Decimal('1e3'))
    assert unit_scale('千克')==('mass',Decimal('1e-3'))
    assert unit_scale('千瓦时')==('energy',Decimal('1e-3'))
    assert unit_scale('百万千瓦时')==('energy',Decimal('1e3'))
    assert unit_scale('million cubic metres 百萬立方米')==('volume',Decimal('1e6'))
    assert unit_scale('10,000 tce 万吨标准煤')==('energy_tce',Decimal('1e4'))
    assert unit_scale('MWh噸') is None
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


def test_long_printed_unit_column_preserves_measurement_labels():
    from esg_document.document.physical import build_table_view
    physical=table('units',0,[['Item','','2024'],
        ['Direct emissions','tonnes of CO2 equivalent','123'],
        ['Indirect emissions','tonnes of CO2 equivalent','456']])
    view=build_table_view(physical.model_dump(mode='json'),None)
    assert view.unit_col==1
    assert [(line.label,line.unit) for line in view.lines]==[
        ('Direct emissions','tonnes of CO2 equivalent'),
        ('Indirect emissions','tonnes of CO2 equivalent')]
    assert [line.values[2].text for line in view.lines]==['123','456']


def test_rowspan_labels_and_units_retain_original_parent_cell_ids():
    from esg_document.document.physical import build_table_view
    physical=table('spans',0,[['Category','Source','Unit','2024'],
        ['Renewable','Hydro','GJ','100'],['','Solar','','200']])
    physical.cells=[cell for cell in physical.cells if cell.cell_id not in {'spans-2-0','spans-2-2'}]
    for cell in physical.cells:
        if cell.cell_id in {'spans-1-0','spans-1-2'}:
            cell.row_span=2
    view=build_table_view(physical.model_dump(mode='json'),None)
    assert [(line.label,line.unit) for line in view.lines]==[
        ('Renewable › Hydro','GJ'),('Renewable › Solar','GJ')]
    assert view.lines[1].label_cell_ids==['spans-1-0','spans-2-1']
    assert view.lines[1].unit_cell_ids==['spans-1-2']
    assert view.lines[1].values[3].cell_id=='spans-2-3'


def test_thousands_separator_is_not_a_year_header():
    from esg_document.document.physical import build_table_view,is_number_text
    assert is_number_text('1,999') and is_number_text('2,025')
    assert is_number_text('1999') and is_number_text('2025')
    physical=table('quantity',0,[['Pollutant','Unit','2024'],
        ['Nitrogen oxides','tonnes','1,999'],['Sulphur dioxide','tonnes','605']])
    view=build_table_view(physical.model_dump(mode='json'),None)
    assert view.header_rows==[0]
    assert view.lines[0].label=='Nitrogen oxides'
    assert view.lines[0].values[2].text=='1,999'


@pytest.mark.parametrize('raw',['1999','2000','2024','1,999'])
def test_year_shaped_quantity_is_retained_under_an_actual_period_header(raw):
    from esg_document.document.physical import build_table_view
    physical=table('year-shaped-value',0,[['Metric','Unit','2024'],['Employees','persons',raw]])
    view=build_table_view(physical.model_dump(mode='json'),None)
    assert view.header_rows==[0] and view.column_paths[2]==['2024']
    assert view.lines[0].values[2].text==raw
    assert view.lines[0].unit=='persons'


def test_explicit_year_column_is_not_a_measurement_column():
    from esg_document.document.physical import build_table_view
    physical=table('year-axis',0,[['Metric','Year','Unit','Amount'],
        ['Employees','2023','persons','1999'],['Employees','2024','persons','2000']])
    view=build_table_view(physical.model_dump(mode='json'),None)
    assert view.value_cols==[3]
    assert [line.values[3].text for line in view.lines]==['1999','2000']
    assert all('c1' not in line.values for line in view.lines)


def test_disclosure_years_remain_dates_in_a_company_register():
    from esg_document.document.physical import build_table_view
    physical=table('register',0,[['Company','Disclosure year','Platform'],
        ['Site A','2025','Register'],['Site B','2025','Register']])
    assert build_table_view(physical.model_dump(mode='json'),None) is None


def test_separate_chart_years_do_not_become_quantity_rows():
    from esg_document.document.physical import build_table_view
    physical=table('chart',0,[['Measure','2023','2024'],
        ['Employees','100','200'],['','2022','2023']])
    view=build_table_view(physical.model_dump(mode='json'),None)
    assert len(view.lines)==1 and view.lines[0].label=='Employees'


def test_wrapped_measure_label_and_unit_multiplier_keep_both_sources():
    from esg_document.document.physical import build_table_view
    physical=table('wrapped',0,[['Indicator','Unit','2024'],
        ['Total non-','10,000',''],['renewable energy consumption','kJ','150'],
        ['Total renewable','10,000',''],['energy consumption','kJ','200']])
    view=build_table_view(physical.model_dump(mode='json'),None)
    assert [(line.label,line.unit) for line in view.lines]==[
        ('Total nonrenewable energy consumption','10,000 kJ'),
        ('Total renewable energy consumption','10,000 kJ')]
    assert view.lines[0].label_cell_ids==['wrapped-1-0','wrapped-2-0']
    assert view.lines[0].unit_cell_ids==['wrapped-1-1','wrapped-2-1']


def test_printed_unit_header_and_adjacent_fragments_outrank_partial_unit_column():
    from esg_document.document.physical import build_table_view
    physical=table('unit-parts',0,[['Indicator','','Unit','','2024'],
        ['Scope 1','Tonne','of CO2','equivalent','100'],
        ['Scope 2','Tonne','of CO2','equivalent','200'],
        ['Other emission','','tonnes','','300']])
    view=build_table_view(physical.model_dump(mode='json'),None)
    assert view.unit_col==2
    assert [(line.label,line.unit) for line in view.lines]==[
        ('Scope 1','Tonne of CO2 equivalent'),('Scope 2','Tonne of CO2 equivalent'),
        ('Other emission','tonnes')]
    assert view.lines[0].unit_cell_ids==['unit-parts-1-1','unit-parts-1-2','unit-parts-1-3']


def test_shifted_unit_header_does_not_override_recognized_unit_data():
    from esg_document.document.physical import build_table_view
    physical=table('shifted',0,[['Indicator','Unit','','2024'],
        ['First','Site A','tonnes','100'],['Second','Site B','tonnes','200']])
    view=build_table_view(physical.model_dump(mode='json'),None)
    assert view.unit_col==2
    assert [(line.label,line.unit) for line in view.lines]==[
        ('First › Site A','tonnes'),('Second › Site B','tonnes')]


def continuation_document():
    from esg_document.document.contracts import BoundingBox,DocumentIRMetadata,PageIR
    left=table('before',0,[['Metric','2022','2023','Boundary'],
        ['Energy (MWh)','100','200','Group'],['Water (m3)','300','400','Group']])
    right=table('after',1,[['Waste (tonnes)','500','600','Group'],
        ['Other waste (tonnes)','700','800','Group']])
    left.bbox=BoundingBox(x0=50,y0=100,x1=550,y1=740,unit='points')
    right.bbox=BoundingBox(x0=50,y0=50,x1=550,y1=500,unit='points')
    for item in (left,right):
        item.continuation_axis=None
        item.continuation_group_id=None
    trace=SourceTrace(parser='test')
    return DocumentIR(metadata=DocumentIRMetadata(run_id='ir-test',ocr_run_id='ocr-test'),
        tables=[left,right],pages=[PageIR(page_id=f'p{i}',page_index=i,page_number=i+1,
            width=600,height=800,source_trace=trace) for i in (0,1)])


def test_headerless_vertical_continuation_preserves_first_data_row_and_source_headers():
    from esg_document.document.table_graph_builder import TableGraphBuilder
    from esg_document.document.physical import build_table_view,column_header_sources
    document=continuation_document()
    original_cells={(c.cell_id,c.row_index,c.col_index,c.text) for t in document.tables for c in t.cells}
    TableGraphBuilder().build(document)
    assert document.tables[1].continues_from_table_id=='before'
    projected=composed_tables([t.model_dump(mode='json') for t in document.tables],[])
    view=build_table_view(projected[1],None)
    assert view.column_paths[1]==['2022'] and view.column_paths[2]==['2023']
    assert view.lines[0].row==0 and view.lines[0].values[1].text=='500'
    sources,_=column_header_sources(projected[1],view.header_rows)
    assert sources[1][0]['cell_id']=='before-0-1' and sources[1][0]['page_index']==0
    assert {(c.cell_id,c.row_index,c.col_index,c.text) for t in document.tables for c in t.cells}==original_cells
    assert all(c.row_index>=0 for t in document.tables for c in t.cells)


def test_same_page_continuation_inherits_printed_headers_in_the_same_column():
    from esg_document.document.contracts import BoundingBox
    from esg_document.document.table_graph_builder import TableGraphBuilder
    from esg_document.document.physical import build_table_view
    document=continuation_document()
    first,second=document.tables
    second.page_index=0
    for cell in second.cells:
        cell.page_index=0
    first.bbox=BoundingBox(x0=50,y0=100,x1=550,y1=350,unit='points')
    second.bbox=BoundingBox(x0=50,y0=360,x1=550,y1=700,unit='points')
    TableGraphBuilder().build(document)
    assert second.continues_from_table_id==first.table_id
    projected=composed_tables([t.model_dump(mode='json') for t in document.tables],[])
    view=build_table_view(projected[1],None)
    assert view.column_paths[1]==['2022'] and view.column_paths[2]==['2023']
    assert view.lines[0].values[1].text=='500'
    assert all(cell.row_index>=0 for cell in second.cells)


def test_same_page_distinct_columns_cannot_share_a_header():
    from esg_document.document.contracts import BoundingBox
    from esg_document.document.table_graph_builder import TableGraphBuilder
    document=continuation_document()
    first,second=document.tables
    second.page_index=0
    first.bbox=BoundingBox(x0=20,y0=100,x1=270,y1=350,unit='points')
    second.bbox=BoundingBox(x0=320,y0=360,x1=570,y1=700,unit='points')
    TableGraphBuilder().build(document)
    assert second.continues_from_table_id is None


@pytest.mark.parametrize('difference',['title','geometry','columns','multiple_tables'])
def test_headerless_neighbor_requires_same_layout_and_no_new_section(difference):
    from esg_document.document.table_graph_builder import TableGraphBuilder
    from esg_document.document.contracts import BlockIR,BoundingBox
    document=continuation_document()
    if difference=='title':
        document.blocks=[BlockIR(block_id='new-title',page_index=1,order=0,block_type='heading',
            text='A separate section',bbox=BoundingBox(x0=50,y0=20,x1=550,y1=40,unit='points'),
            source_trace=SourceTrace(parser='test'))]
    elif difference=='geometry':
        document.tables[1].bbox.x0=100
    elif difference=='columns':
        for cell in document.tables[1].cells:
            if cell.col_index==2:
                cell.text='A different subject'
    else:
        document.tables.append(table('separate',1,[['Item','One'],['Entry','2']]))
    TableGraphBuilder().build(document)
    assert document.tables[1].continues_from_table_id is None


@pytest.mark.parametrize("value", ["1999", "2000", "2024"])
def test_bare_measurement_equal_to_a_year_retains_quantity_context(value):
    from esg_document.document.physical import build_table_view
    physical = table('bare-count',0,[['Metric','Unit','2024'],['Employees','persons',value]])
    view = build_table_view(physical.model_dump(mode='json'),None)
    assert view.header_rows == [0]
    assert view.lines[0].values[2].text == value
    assert view.column_paths[2] == ['2024']


def test_explicit_year_column_is_context_for_unitless_measurements():
    from esg_document.document.physical import build_table_view
    physical = table('by-year',0,[['Year','Amount'],['2023','2000'],['2024','2100']])
    view = build_table_view(physical.model_dump(mode='json'),None)
    assert view.header_rows == [0] and view.value_cols == [1]
    assert [(line.label,line.values[1].text) for line in view.lines] == [('2023','2000'),('2024','2100')]


def test_unlabelled_unit_prefix_row_is_bound_to_the_following_measurement():
    from esg_document.document.physical import build_table_view
    physical=table('scaled-unit',0,[['Indicator','Unit','2022','2023'],
        ['Natural gas consumption','m3','10','20'],
        ['','10,000','',''],
        ['Total energy consumption','kJ','1,996,302,695.40','3,284,713,462.23']])
    view=build_table_view(physical.model_dump(mode='json'),None)
    row=next(line for line in view.lines if line.label=='Total energy consumption')
    assert row.unit=='10,000 kJ'
    assert row.unit_cell_ids==['scaled-unit-2-1','scaled-unit-3-1']
    assert row.values[2].text=='1,996,302,695.40'


def test_a_numeric_measurement_does_not_become_the_next_rows_unit_multiplier():
    from esg_document.document.physical import build_table_view
    physical=table('independent-rows',0,[['Indicator','Unit','2022'],
        ['Number of sites','10,000','7'],
        ['Total energy consumption','kJ','120']])
    view=build_table_view(physical.model_dump(mode='json'),None)
    row=next(line for line in view.lines if line.label=='Total energy consumption')
    assert row.unit=='kJ' and row.unit_cell_ids==['independent-rows-2-1']
