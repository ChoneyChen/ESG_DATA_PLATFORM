"""Structural first: a table is decided without the model only when it is unambiguous."""
from esg_targeted.foundation.kernel import TableTask
from esg_targeted.foundation.kernel import build_request
from esg_targeted.foundation.structural import code_names, structural_column, structural_decision
from esg_targeted.foundation.tables import CellRef, DataLine, TableView


def make_view(rows, *, headers=(("2025年", "2024年"),), arithmetic=(), title=None, headings=()):
    lines = []
    for index, (label, values) in enumerate(rows, 1):
        lines.append(DataLine(row=index, rid=f"r{index:04d}", label=label, unit=None, section=None,
                              values={column: CellRef(cell_id=f"c{index}{column}", row=index, col=column,
                                                      text=value, row_span=1, col_span=1)
                                      for column, value in values.items()}))
    value_cols = list(range(1, len(headers[0]) + 1))
    return TableView(
        table_id="t1", page_index=0, page_number=1, n_rows=len(rows) + 1,
        n_cols=len(value_cols) + 1, header_rows=[0],
        header_lines=[[(column, 0, text) for column, text in enumerate(headers[0], 1)]],
        value_cols=value_cols, unit_col=None, label_cols=[0], lines=lines, sections={},
        headers_aligned=True,
        column_paths={column: [text] for column, text in enumerate(headers[0], 1)},
        headings=list(headings), title=title, caption=None, notes=[], arithmetic=list(arithmetic),
    )


def task(view, codes=("E1-5_01",)):
    return TableTask(view=view, lines=view.lines, codes=list(codes), groups=["energy"])


def test_exact_clause_name_with_one_period_column_is_decided():
    view = make_view([("综合能耗", {1: "1,234", 2: "1,100"})])
    decision = structural_decision(task(view), 2025)
    assert decision is not None
    assert decision.payload["current_col"] == "c1"
    assert decision.payload["items"] == [{"row": "r0001", "target": "E1-5_01", "note": None}]
    assert decision.payload["current_scope"] == "whole_reporting_group"


def test_two_period_columns_without_arithmetic_go_to_the_model():
    view = make_view([("综合能耗", {1: "1", 2: "2"})], headers=(("2025年 集团", "2025年 子公司"),))
    assert structural_decision(task(view), 2025) is None


def test_arithmetic_total_resolves_two_same_period_columns():
    view = make_view([("综合能耗", {1: "1", 2: "2"})],
                     headers=(("2025年 子公司", "2025年 集团"),), arithmetic=["c2 = c1 + c1"])
    decision = structural_decision(task(view), 2025)
    assert decision is not None and decision.payload["current_col"] == "c2"


def test_a_row_that_only_mentions_the_clause_needs_the_model():
    view = make_view([("综合能耗（含车辆燃料）", {1: "1", 2: "2"})])
    assert structural_decision(task(view), 2025) is None


def test_no_exact_row_needs_the_model():
    view = make_view([("能源使用情况说明", {1: "1", 2: "2"})])
    assert structural_decision(task(view), 2025) is None


def test_no_period_column_needs_the_model():
    view = make_view([("综合能耗", {1: "1", 2: "2"})], headers=(("本期", "上期"),))
    assert structural_decision(task(view), 2025) is None


def test_subset_title_marks_the_scope_as_part_of_group():
    view = make_view([("综合能耗", {1: "1"})], headers=(("2025年",),), title="某生产基地能源消耗")
    decision = structural_decision(task(view), 2025)
    assert decision is not None and decision.payload["current_scope"] == "part_of_group"


def test_clause_names_come_from_the_label_and_its_accepted_aliases():
    names = code_names("E1-5_01")
    assert {"综合能耗", "能耗总量", "能源消耗总量"} <= names
    assert "direct" not in names


def test_a_sibling_total_column_leaves_the_choice_to_the_model():
    # 2025 子公司 | 合计 | 2024 子公司: the only year column is not the group total.
    view = make_view([("综合能耗", {1: "1", 2: "3", 3: "2"})],
                     headers=(("2025年 子公司", "合计", "2024年 子公司"),))
    assert structural_column(task(view), 2025) is None


def test_single_period_column_is_selected_structurally():
    view = make_view([("综合能耗", {1: "1"})], headers=(("2025年",),))
    assert structural_column(task(view), 2025) == 1


def test_decided_column_is_stated_in_the_prompt():
    view = make_view([("综合能耗", {1: "1"})], headers=(("2025年",),))
    without, _, _, _ = build_request(task(view), 2025)
    with_column, context, schema, _ = build_request(task(view), 2025, 1)
    assert "already decided" in with_column and "already decided" not in without
    assert with_column != without and context in with_column + context
