"""Deterministic rules of the foundation-16 route (no model calls)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from esg_targeted.foundation.consolidate import consolidate
from esg_targeted.foundation.guards import check
from esg_targeted.foundation.kernel import bind, infer_unit, plan_table_tasks, scope2_method_from_text
from esg_targeted.foundation.tables import (
    build_table_view, composed_tables, entity_sum_role, explicit_total_column, table_context,
)
from esg_targeted.foundation.textnorm import fold


def make_table(rows: list[list[str]], *, table_id: str = "table-p0010-0001", block_id: str = "block-t") -> dict:
    cells = [
        {"cell_id": f"cell-{r}-{c}", "row_index": r, "col_index": c, "text": text, "row_span": 1, "col_span": 1}
        for r, row in enumerate(rows) for c, text in enumerate(row)
    ]
    return {"table_id": table_id, "page_index": 9, "row_count": len(rows), "column_count": max(map(len, rows)),
            "cells": cells, "block_id": block_id}


def page(*blocks: dict) -> dict:
    return {"page": {"page_index": 9, "page_number": 10}, "blocks": list(blocks)}


def block(block_id: str, text: str, box: tuple[float, float, float, float], block_type: str = "paragraph") -> dict:
    return {"block_id": block_id, "text": text, "block_type": block_type,
            "bbox": {"x0": box[0], "y0": box[1], "x1": box[2], "y1": box[3]}}


def observation(code: str, value: str, **extra) -> dict:
    base = {"code": code, "status": "accepted", "value": value, "value_raw": value, "unit_raw": "tCO2e",
            "unit_id": None, "period": "2024", "period_year": 2024, "page_number": 5, "flags": [],
            "entity_basis": "group", "member": None, "table_id": "t", "row_label": code, "cell_id": None,
            "column": "c1", "note_block_id": None}
    base.update(extra)
    return base


def test_misaligned_flat_header_binds_the_same_period_sum_column():
    # OCR flattened "2024" (spanning A | B | total) into its own header cell,
    # leaving the last header above an empty column.
    view = build_table_view(make_table([
        ["指标", "单位", "2022", "2023", "公司A", "2024", "公司B", "汇总"],
        ["总耗水", "立方米", "2,049,041.46", "2,131,501.14", "2,869,566.38", "2,604,230.13", "5,473,796.51", ""],
        ["每人耗水强度", "立方米/人", "125.55", "110.51", "", "", "360.50", ""],
    ]), page())
    assert view is not None and not view.headers_aligned
    assert view.value_cols == [2, 3, 4, 5, 6]
    assert "c6 = c4 + c5 in 1/1 rows" in view.arithmetic
    task = plan_table_tasks(view, ["water"])[0]
    payload = {"current_col": "c5", "current_year": "2024", "current_scope": "whole_reporting_group",
               "items": [{"row": "r1", "target": "E3-4_01", "note": None}]}
    observations, _ = bind(task, payload, 2024)
    assert observations[0].value_raw == "5,473,796.51"
    assert observations[0].entity_basis == "group"
    assert "column_switched_to_sum_of_parts" in observations[0].flags


def test_sum_across_years_is_not_an_entity_total():
    view = build_table_view(make_table([
        ["指标", "单位", "2022", "2023", "2024", "合计"],
        ["范围一排放", "吨二氧化碳当量", "10,120", "11,345", "12,567", "34,032"],
        ["范围二排放", "吨二氧化碳当量", "20,450", "21,330", "22,780", "64,560"],
    ]), page())
    assert view.sums, "the cumulative column is still reported as arithmetic"
    assert entity_sum_role(view, 4, view.lines[0]) == (None, None)


def test_entity_columns_under_one_year_mark_segment_and_total():
    view = build_table_view(make_table([
        ["指标", "单位", "2023", "2024", "2024", "2024"],
        ["", "", "", "境内", "境外", "合计"],
        ["范围一排放", "吨二氧化碳当量", "9,876", "5,222.86", "1,105.97", "6,328.83"],
        ["范围二排放", "吨二氧化碳当量", "60,111", "85,448.76", "55,480.75", "140,929.51"],
    ]), page())
    line = view.lines[0]
    assert entity_sum_role(view, 5, line) == ("sum", 5)
    assert entity_sum_role(view, 3, line) == ("part", 5)


def test_leading_serial_number_column_is_not_a_value_column():
    rows = [["编号", "议题", "股东", "员工"]] + [[str(i), f"议题{i}", "●", "●"] for i in range(1, 8)]
    assert build_table_view(make_table(rows), page()) is None


def test_title_comes_from_the_same_page_column():
    table = make_table([["", "2023", "2024"], ["TOTAL", "96,714", "150,651"]], block_id="block-t")
    blocks = [
        block("b1", "Biogas production emissions", (135, 114, 686, 158), "heading"),
        block("b2", "Indirect greenhouse gas emissions (Scope 2)", (899, 100, 1798, 156), "heading"),
        block("block-t", "<table/>", (154, 439, 838, 574), "table_markdown"),
        block("b3", "Electrical energy table follows", (887, 300, 1688, 340)),
    ]
    title, _, heading = table_context(blocks, "block-t")
    assert title == "Biogas production emissions"
    assert heading == "Biogas production emissions"
    view = build_table_view(table, page(*blocks))
    task = plan_table_tasks(view, ["ghg"])[0]
    payload = {"current_col": "c2", "current_year": "2024", "current_scope": "whole_reporting_group",
               "items": [{"row": "r1", "target": "E1-6_09", "note": None}]}
    observations, _ = bind(task, payload, 2024)
    assert observations[0].status == "rejected"


def test_unit_is_read_from_a_caption_segment():
    blocks = [block("cap", "Table 29. Total water use, m 3, Operating Segment, 2018–2020", (100, 100, 800, 130)),
              block("block-t", "<table/>", (100, 140, 800, 400), "table_markdown")]
    view = build_table_view(make_table([["", "2018", "2020"], ["TOTAL", "1,757,101", "1,870,150"]]), page(*blocks))
    assert infer_unit(view.lines[0], view, 2) == ("m 3", "context")


def test_scope3_upstream_and_downstream_are_categories():
    result = check("E1-6_11", "温室气体排放范围三上游总排放量", None, "tCO2e", "ghg-emissions")
    assert not result.ok and "label_is_scope3_category" in result.flags
    assert check("E1-6_11", "温室气体排放量范围三", None, "tCO2e", "ghg-emissions").ok


def test_parts_that_add_up_to_the_total_are_not_conflicts():
    records = [{"observations": [
        observation("E1-6_11", "27635147.16"),
        observation("E1-6_11", "25021274.00", row_label="上游"),
        observation("E1-6_11", "2613873.17", row_label="下游"),
    ]}]
    conclusion = next(c for c in consolidate(records, 2024) if c["code"] == "E1-6_11")
    assert conclusion["status"] == "found"
    member = conclusion["members"][0]
    assert member["value"] == "27635147.16"
    assert not member["conflicting_sources"]
    assert len(member["component_sources"]) == 2


def test_the_same_total_under_two_reporting_scopes_is_two_measurements():
    """A group total and a production-base total are different measurements of
    the same clause, not a conflicting value."""
    records = [{"observations": [
        observation("E1-6_12", "117026505.54", period="2025", period_year=2025, attributes={"boundary_label": "group"}),
        observation("E1-6_12", "1751599.73", period="2025", period_year=2025, attributes={"boundary_label": "battery_production_bases"}),
    ]}]
    conclusion = next(c for c in consolidate(records, 2025) if c["code"] == "E1-6_12")
    assert conclusion["status"] == "found"
    assert conclusion["members"][0]["value"].startswith("117026505")
    views = conclusion.get("views") or []
    assert [view["boundary_label"] for view in views] == ["battery_production_bases"]
    assert views[0]["value_raw"] == "1751599.73"
    assert not conclusion["members"][0]["conflicting_sources"]


def test_a_scope_word_in_the_row_label_names_the_boundary():
    from esg_targeted.foundation.tables import TableView, boundary_label
    view = TableView("t", 0, 1, 2, 2, [], [], [1], None, [0], [], {}, False, {}, [], None)
    assert boundary_label(view, "集团排放总量") == "group"
    assert boundary_label(view, "电池生产基地排放强度") == "battery_production_bases"
    assert boundary_label(view, "范围一温室气体排放总量") is None


def test_different_totals_remain_a_conflict():
    records = [{"observations": [observation("E1-6_07", "321428"), observation("E1-6_07", "25829")]}]
    conclusion = next(c for c in consolidate(records, 2024) if c["code"] == "E1-6_07")
    assert conclusion["status"] == "conflict"


def test_traditional_chinese_totals_fold_to_simplified():
    assert fold("匯總 範圍1 溫室氣體") == "汇总 范围1 温室气体"


def test_a_rejected_alternative_method_is_not_a_second_method():
    text = "The location-based method was chosen to calculate emissions (Scope 2) instead of the market-based method."
    assert scope2_method_from_text(text) == "E1-6_09"
    assert scope2_method_from_text("范围二采用基于市场的方法，而非地点法") == "E1-6_10"
    assert scope2_method_from_text("Scope 2 is reported location-based and market-based") is None


def test_a_one_row_table_takes_its_title_as_subject():
    blocks = [block("cap", "Indirect greenhouse gas emissions (Scope 2)", (100, 100, 800, 130), "heading"),
              block("block-t", "<table/>", (100, 140, 800, 300), "table_markdown"),
              block("n", "Scope 2 emissions use the location-based method.", (100, 320, 800, 360))]
    view = build_table_view(make_table([["", "2019", "2020"], ["Electrical energy", "236,556", "232,302"]]), page(*blocks))
    task = plan_table_tasks(view, ["ghg"])[0]
    payload = {"current_col": "c2", "current_year": "2020", "current_scope": "whole_reporting_group",
               "items": [{"row": "r1", "target": "SCOPE2_METHOD_UNSTATED", "note": None}]}
    observations, _ = bind(task, payload, 2020)
    assert [(o.code, o.value_raw) for o in observations] == [("E1-6_09", "232,302")]
    assert observations[0].status != "rejected"


def test_wide_grouped_headers_are_rebuilt_from_reading_order():
    from esg_targeted.foundation.tables import rebuild_column_paths
    # OCR attached "2025" to the label area and shifted every group label right.
    header_lines = [[(1, 1, "Unit"), (2, 1, "2025"), (3, 3, "Total"), (6, 3, "Site A"), (9, 3, "Site B")],
                    [(3, 1, "2024"), (4, 1, "2023"), (5, 1, "2025"), (6, 1, "2024"), (7, 1, "2023"),
                     (8, 1, "2025"), (9, 1, "2024"), (10, 1, "2023")]]
    paths = rebuild_column_paths(header_lines, list(range(3, 12)), 1, [0])
    assert paths[3] == ["Total", "2025"] and paths[5] == ["Total", "2023"] and paths[6] == ["Site A", "2025"]
    assert rebuild_column_paths(header_lines, list(range(3, 13)), 1, [0]) is None


def test_footnote_marks_after_figures_are_not_part_of_the_number():
    from esg_targeted.foundation.tables import number_text, parse_number
    assert parse_number("8,432,549 4") == Decimal("8432549")
    assert parse_number("2,557 $ ^{(7)} $") == Decimal("2557")
    assert number_text("1 234") == "1 234" and parse_number("2025 3") is None


def test_text_layer_fixes_qualifiers_and_decimal_separators():
    from esg_targeted.foundation.textlayer import PageText, reconcile_view
    printed = PageText("間接溫室氣體排放總量 百万吨 35.18 32.26 32.09 （範圍二）（綠電抵扣）\n"
                       "間接溫室氣體排放總量 百万吨 35.27 34.16 35.02 （範圍二）（綠电未抵扣）\n NO x tonnes 1.274 1.024")
    view = build_table_view(make_table([
        ["指标", "单位", "2023", "2024", "2025"],
        ["間接溫室氣體排放總量 （範圍二）（線電無抵扣）", "百万吨", "35.18", "32.26", "32.09"],
        ["間接溫室氣體排放總量 （範圍二）（線电未抵扣）", "百万吨", "35.27", "34.16", "35.02"],
        ["NO,", "tonnes", "1,274", "1,024", "/"],
    ]), page())
    reconcile_view(view, printed)
    assert [line.label for line in view.lines][:2] == ["間接溫室氣體排放總量 （範圍二）（綠電抵扣）",
                                                       "間接溫室氣體排放總量 （範圍二）（綠电未抵扣）"]
    assert sorted(view.corrected_cells.values()) == ["1.024", "1.274"]
    task = plan_table_tasks(view, ["ghg"])[0]
    payload = {"current_col": "c4", "current_year": "2025", "current_scope": "whole_reporting_group",
               "items": [{"row": "r1", "target": "E1-6_09", "note": None}]}
    observations, _ = bind(task, payload, 2025)
    assert [(o.code, o.value_raw) for o in observations] == [("E1-6_10", "32.09"), ("E1-6_09", "35.02")]
    assert "scope2_method_model_overruled" in observations[0].flags
    assert observations[1].status == "review" and "scope2_other_method_row_beside_mapped_row" in observations[1].flags


def test_a_lone_scope3_category_is_offered_for_review():
    records = [{"observations": [observation("E1-6_11", "11592", status="rejected", rejected_by="label_is_scope3_category")]}]
    conclusion = next(c for c in consolidate(records, 2024) if c["code"] == "E1-6_11")
    assert conclusion["status"] == "review"
    assert "scope3_only_named_categories_reported" in conclusion["members"][0]["flags"]
    two = [{"observations": [observation("E1-6_11", "1205", status="rejected", rejected_by="label_is_scope3_category"),
                             observation("E1-6_11", "2246", status="rejected", rejected_by="label_is_scope3_category")]}]
    assert next(c for c in consolidate(two, 2024) if c["code"] == "E1-6_11")["status"] == "only_rejected_or_other_period"


def test_a_footnote_cited_by_a_pollutant_row_is_its_method_note_unless_it_is_about_ghg():
    from esg_targeted.foundation.guards import is_method_text
    assert is_method_text("根據規定的排放量計算公式進行理論值估算。")
    assert not is_method_text("范围一涵盖化石能源排放，排放因子参考IPCC温室气体清单指南计算。")


def test_identical_requests_are_answered_from_the_cache_without_a_model_call():
    import json
    from types import SimpleNamespace
    from esg_targeted.foundation.engine import FoundationEngine

    view = build_table_view(make_table([
        ["指标", "单位", "2023", "2024"],
        ["范围一排放", "吨二氧化碳当量", "9,876", "10,120"],
    ]), page())

    class Model:
        calls = 0

        def generate_structured(self, **kwargs):
            Model.calls += 1
            payload = {"current_col": "c3", "current_year": "2024", "current_scope": "whole_reporting_group",
                       "items": [{"row": "r1", "target": "E1-6_07", "note": None}]}
            return json.dumps(payload), {"input_tokens": 10, "generation_tokens": 5}

    engine = FoundationEngine.__new__(FoundationEngine)
    engine.ir = SimpleNamespace(pages=[], run_id="ir-x")
    engine.period = SimpleNamespace(year=2024)
    engine.tasks = plan_table_tasks(view, ["ghg"])
    first = engine.execute(Model())
    assert Model.calls == 1 and first[0]["request_sha"]
    cache = {r["request_sha"]: r for r in first if r.get("request_sha")}
    second = engine.execute(Model(), cache=cache)
    assert Model.calls == 1
    assert second[0]["reused"] == "cache"
    assert [o["value_raw"] for o in second[0]["observations"]] == ["10,120"]


def test_a_standard_code_year_is_not_the_reporting_period():
    """`ISO 14067:2018` names a norm. Reading 2018 as the reporting period used
    to push a 2025 report's rows into 2018."""
    from esg_targeted.foundation.period import detect_reporting_period

    def pages_of(*texts):
        return [{"page_index": i, "blocks": [{"block_id": f"b{i}", "text": text}]}
                for i, text in enumerate(texts)]

    only_code = detect_reporting_period({}, pages_of("报告期内，我们采用基于ISO 14067:2018 标准核算产品碳足迹。"))
    assert only_code.year is None and only_code.basis == "unknown"
    with_statement = detect_reporting_period({}, pages_of(
        "报告期内，我们采用基于ISO 14067:2018 标准核算产品碳足迹。",
        "本报告涵盖 2025 年 1 月 1 日至 2025 年 12 月 31 日的绩效。"))
    assert with_statement.year == 2025
    labelled = detect_reporting_period({"document_label": "紫金矿业2025年可持续发展报告.pdf"}, pages_of("前言"))
    assert labelled.year == 2025 and labelled.basis == "report_file_label"


def test_a_clause_printed_in_another_family_table_is_still_judged_by_its_guard():
    """The pollutant table also prints total wastewater discharge. The table's
    family decides what the model is asked; a row whose own label satisfies a
    clause guard is still a candidate for that clause."""
    view = build_table_view(make_table([
        ["污染物排放指标", "指标", "单位", "2024", "2025"],
        ["废水", "废水排放总量", "吨", "174.83", "112.39"],
        ["", "化学需氧量（COD）排放量", "千克", "8.74", "1.91"],
    ]), page())
    task = plan_table_tasks(view, ["water_pollutants"])[0]
    payload = {"current_col": "c4", "current_year": "2025", "current_scope": "whole_reporting_group",
               "items": [], "method_notes": []}
    assert not any(o.code == "E3-4_12" for o in bind(task, payload, 2025)[0])
    widened, _ = bind(task, payload, 2025, ("E3-4_12",))
    discharge = next(o for o in widened if o.code == "E3-4_12")
    assert discharge.value_raw == "112.39"
    # A group row that only says "废水" is not a discharge total.
    assert all(o.row_label.strip() != "废水" for o in widened)

def test_a_row_named_after_a_target_is_proposed_when_the_call_skips_it():
    view = build_table_view(make_table([
        ["指标", "单位", "2024", "2025"],
        ["取水总量¹", "立方米", "35,080,458.72", "49,244,155.42"],
        ["耗水总量", "立方米", "/", "32,075,664.77"],
        ["耗水强度²", "立方米/兆瓦时", "/", "42.88"],
    ]), page())
    task = plan_table_tasks(view, ["water"])[0]
    payload = {"current_col": "c3", "current_year": "2025", "current_scope": "whole_reporting_group",
               "items": [{"row": "r2", "target": "E3-4_01", "note": None}]}
    observations, _ = bind(task, payload, 2025)
    by_code = {o.code: o for o in observations}
    assert by_code["E3-4_01"].status == "accepted"
    assert by_code["E3-4_11"].value_raw == "49,244,155.42"
    assert by_code["E3-4_11"].status == "review" and "row_label_names_datapoint" in by_code["E3-4_11"].flags
    # Without a current column the column is derived from the header year, so a
    # printed row is still proposed instead of the whole table being dropped.
    derived, _ = bind(task, {**payload, "current_col": None, "items": []}, 2025)
    by_code = {o.code: o for o in derived}
    assert by_code["E3-4_11"].value_raw == "49,244,155.42"
    assert "column_from_header_year" in by_code["E3-4_11"].flags



def test_reused_equipment_is_not_recycled_water():
    assert check("E3-4_03", "废弃物 › 电子设备回用量（吨）", None, "吨", "mass").flags == ("label_not_about_water",)
    assert check("E3-4_03", "中水回用量", None, "吨", "mass").ok


def test_notes_come_from_the_table_column_on_a_two_column_page():
    blocks = [
        block("b-left", "¹温室气体排放总量包括范围一、范围二温室气体排放。", (40, 700, 300, 720)),
        block("block-t", "", (320, 100, 600, 300), "table"),
        block("b-right", "¹取水总量包括市政用水和地下水。", (320, 320, 600, 340)),
    ]
    view = build_table_view(make_table([
        ["指标", "单位", "2025"],
        ["取水总量¹", "立方米", "49,244,155.42"],
    ]), page(*blocks))
    assert [note.block_id for note in view.notes] == ["b-right"]


def test_superscript_footnote_marks_are_not_part_of_the_number():
    from esg_targeted.foundation.tables import number_text
    assert number_text("8,432,549⁴") == "8,432,549"
    assert number_text("587,454.16²,³") == "587,454.16"


def test_notes_of_a_table_continued_across_columns_come_from_the_other_column():
    blocks = [
        block("block-t", "", (40, 100, 300, 600), "table"),
        block("b-26", "²⁶溫室氣體排放核算參照《工業企業溫室氣體排放核算和報告通則》。", (320, 700, 600, 720)),
        block("b-1", "¹取水總量包括市政用水。", (320, 730, 600, 750)),
    ]
    view = build_table_view(make_table([
        ["指標名稱", "單位", "2025"],
        ["溫室氣體排放總量（範圍一+範圍二）²⁶", "百萬噸", "32.28"],
    ]), page(*blocks))
    assert [note.block_id for note in view.notes] == ["b-26"]


def test_a_narrative_figure_for_one_site_is_a_rejected_candidate():
    from esg_targeted.foundation.text_pass import TextBlock, bind_text
    block = TextBlock("b1", "block-1", 74, "中國移動呼和浩特數據中心作為運營商最大單體智算中心，綠電比例已近90%。")
    payload = {"facts": [{"block": "b1", "target": "E1-5_09", "value": "90%", "unit": None}]}
    (obs,) = bind_text(payload, [block], 2025)
    assert obs.status == "rejected" and obs.rejected_by == "sentence_limited_to_part_of_group"
    assert "value_is_approximate" in obs.flags


def test_footnote_marks_are_spelt_out_for_the_model_but_exponents_stay():
    from esg_targeted.foundation.tables import prompt_label
    assert prompt_label("Energy indirect (Scope 2) emissions: market-based⁴,¹⁰") == \
        "Energy indirect (Scope 2) emissions: market-based [note 4,10]"
    assert prompt_label("替代水源 - 中水使用量 $ ^{{38}} $") == "替代水源 - 中水使用量 [note 38]"
    assert prompt_label("Water consumption (m³)") == "Water consumption (m³)"
    assert prompt_label("Water (m $ ^{3} $)") == "Water (m $ ^{3} $)"


def test_facing_page_columns_are_appended_and_the_explicit_total_column_wins():
    """A wide table split over a spread: left labels, right entity columns.

    The right page repeats the period headers without row labels, and its
    Total group is headed by one label over three year columns. The group
    value is the Total column, not whichever entity column the model picked.
    """
    left = make_table([
        ["Environmental KPIs", "Unit", "Ports", "", "", "Retail", "", ""],
        ["", "", "2023", "2024", "2025", "2023", "2024", "2025"],
        ["GHG emissions", "", "", "", "", "", "", ""],
        ["Scope 1 GHG emissions", "tonne CO2 e", "363,356", "365,334", "356,545", "116,984", "87,581", "101,164"],
    ], table_id="table-p0001-0001")
    right = make_table([
        ["", "Infrastructure", "", "", "Telecommunications", "(3)", "", "Total", ""],
        ["2023", "2024", "2025", "2023", "2024", "2025", "2023", "2024", "2025"],
        ["1,172,147", "1,244,603", "1,162,747", "619,800", "521,015", "339,096", "2,572,342", "2,538,397", "2,236,657"],
    ], table_id="table-p0002-0001")
    right.update(page_index=10, continuation_group_id="g", continuation_axis="horizontal",
                 continues_from_table_id=left["table_id"])
    left.update(page_index=9, continuation_group_id="g", continuation_axis="horizontal",
                continues_to_table_id=right["table_id"])
    view = build_table_view(composed_tables([left, right])[0], page())
    assert view is not None and view.n_cols == 17
    line = next(item for item in view.lines if "Scope 1" in item.label)
    total_col = explicit_total_column(view, 4, line, 2025)
    assert total_col is not None and line.values[total_col].text == "2,236,657"


def test_a_footnote_marker_over_a_heading_does_not_end_the_header_band():
    view = build_table_view(make_table([
        ["", "Segment", "(3)", "Total", ""],
        ["", "2023", "2024", "2025", "2025"],
        ["Scope 1", "1", "2", "3", "4"],
    ]), page())
    assert view is not None and view.header_rows == [0, 1]


def test_a_label_column_ending_in_a_unit_is_not_a_unit_column():
    """A row label may carry its unit in parentheses; that does not make the
    column a unit column, which would leave the table with no row labels at
    all and make every row unmappable."""
    view = build_table_view(make_table([
        ["业绩指标", "2023", "2024", "2025"],
        ["氮氧化物（NOx）排放量（万吨）", "9.72", "9.51", "8.2"],
        ["节水量（万立方米）", "854", "824", "804"],
    ]), page())
    assert view is not None
    assert view.label_cols == [0] and view.unit_col is None
    assert view.lines[0].label.startswith("氮氧化物")
    assert view.lines[0].values[3].text == "8.2"


def test_pollutant_rows_the_call_left_unmapped_are_proposed_by_label():
    """A performance table lists one pollutant per row. When the model returns
    no mapping, the program proposes the labelled rows and the ordinary guards
    decide; it never invents a value."""
    view = build_table_view(make_table([
        ["业绩指标", "2024", "2025"],
        ["氮氧化物（NOx）排放量（万吨）", "9.51", "8.2"],
        ["节能量（万吨标准煤）", "78", "75"],
    ]), page())
    task = plan_table_tasks(view, ["air"])[0]
    payload = {"current_col": "c2", "current_year": "2025", "current_scope": "whole_reporting_group",
               "items": [], "method_notes": []}
    observations, _ = bind(task, payload, 2025)
    by_member = {o.member: o for o in observations}
    assert "NOx" in by_member and by_member["NOx"].value_raw == "8.2"
    assert "row_label_names_pollutant" in by_member["NOx"].flags
    assert not any("节能量" in o.row_label for o in observations)


def test_an_unseen_clause_is_judged_by_its_declared_spec_without_new_code():
    from esg_targeted.foundation.specs import SPECS, ClauseSpec
    SPECS["E1-5_03_test"] = ClauseSpec(
        "E1-5_03_test", "能源强度",
        require_terms=("能源强度", "energy intensity"),
        exclude_terms=("总量", "total"),
        dimension="ratio",
    )
    ok, reason = SPECS["E1-5_03_test"].evaluate("能源强度（兆瓦时/百万元）", None, "MWh/CNY m", "ratio")
    assert ok and reason is None
    rejected, reason = SPECS["E1-5_03_test"].evaluate("能源消耗总量（兆瓦时）", None, "MWh", "energy")
    assert not rejected and reason == "label_not_this_concept"
    del SPECS["E1-5_03_test"]


def test_metric_sets_are_data_and_never_refer_to_a_report():
    from esg_targeted.foundation.catalog import BY_CODE, PROFILES, metrics_for

    assert len(PROFILES["base16"]) == 16
    assert len(PROFILES["priority32"]) == 32
    assert set(PROFILES["base16"]) < set(PROFILES["priority32"])
    assert all(code in BY_CODE for code in PROFILES["priority32"])
    with pytest.raises(ValueError):
        metrics_for("a-report-name")


def test_a_component_clause_declares_its_parent():
    from esg_targeted.foundation.catalog import BY_CODE

    assert BY_CODE["E1-5_05"].parent_code == "E1-5_01"
    assert BY_CODE["E5-5_15"].parent_code == "E5-5_07"
    assert BY_CODE["E1-6_07"].parent_code == "E1-6_12"
    assert BY_CODE["E1-5_01"].parent_code is None


def test_extended_clause_guards_reject_the_confusable_neighbour():
    from esg_targeted.foundation import guards as g

    cases = [
        ("E1-5_02", "化石能源消耗量", "吨标准煤", "energy", True),
        ("E1-5_02", "可再生能源消耗量", "兆瓦时", "energy", False),
        ("E1-6_12", "范围一排放量", "吨CO2e", "ghg-emissions", False),
        ("E5-5_07", "有害废弃物产生量", "吨", "mass", False),
        ("E5-5_15", "无害废弃物总量", "吨", "mass", False),
        ("S1-6_02", "员工流失率", "%", "ratio", False),
        ("S1-6_02", "平均员工人数", "人", "count", False),
        ("S1-13_04", "商业道德人均培训时长", "小时", None, False),
        ("S1-13_04", "全年人均受训小时数", "小时", None, True),
        ("S1-16_01", "薪酬比", "倍", "ratio", False),
        ("G1-4_01", "腐败事件数量", "件", "count", False),
    ]
    for code, label, unit, dimension, expected in cases:
        assert g.check(code, label, None, unit, dimension).ok is expected, (code, label)


def test_declared_spec_judges_a_clause_without_a_bespoke_guard():
    """Adding a concept is a data change: the spec is authoritative here."""
    from esg_targeted.foundation import specs

    specs.SPECS["E9-9_01"] = specs.ClauseSpec(
        "E9-9_01", "合成物质排放量",
        require_terms=("合成物质",), exclude_terms=("强度",), dimension="mass",
    )
    try:
        assert check("E9-9_01", "合成物质排放量", None, "吨", "mass").ok is True
        rejected = check("E9-9_01", "合成物质排放强度", None, "吨/人", "mass")
        assert rejected.ok is False and rejected.flags
    finally:
        specs.SPECS.pop("E9-9_01", None)
