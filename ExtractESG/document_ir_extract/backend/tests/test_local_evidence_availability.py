from types import SimpleNamespace as Obj
from esg_v2.document.validator import DocumentIrValidator


def document():
    return Obj(pages=[Obj(page_index=0,page_id="p0"),Obj(page_index=1,page_id="p1")], blocks=[], tables=[],figures=[],spreads=[],sections=[],logical_tables=[],review_tasks=[],conflict_groups=[])


def test_local_review_failure_excludes_its_page_not_whole_document():
    doc = document()
    doc.review_tasks = [Obj(blocking=True,status="deferred",target_id="p0",scope=[],review_plan=None)]
    policy = DocumentIrValidator._evidence_policy(doc, [Obj(severity="error",code="review_chain_repair_required")], False)
    assert policy["mode"] == "limited"
    assert policy["excluded_page_indices"] == [0]
    assert policy["available_page_count"] == 1
    assert doc.review_tasks[0].status == "deferred"


def test_global_integrity_failure_cannot_be_waived():
    policy = DocumentIrValidator._evidence_policy(document(), [Obj(severity="blocking",code="duplicate_entity_ids")], False)
    assert policy["mode"] == "blocked"


def test_local_bad_table_preserves_reliable_table_on_same_page():
    doc = document()
    doc.tables = [Obj(table_id="bad", page_index=0, bbox=None, cells=[]),
                  Obj(table_id="good", page_index=0, bbox={"x": 1}, cells=[])]
    doc.review_tasks = [Obj(blocking=True,status="deferred",target_id="bad",scope=[],review_plan=None)]
    policy = DocumentIrValidator._evidence_policy(doc, [Obj(severity="error", code="table_geometry_incomplete")], False)
    assert policy["schema_version"] == "evidence-availability-v2"
    assert policy["excluded_target_ids"] == ["bad"]
    assert policy["excluded_page_indices"] == []
    objects = {a["object_id"]: a for a in policy["object_availability"]}
    assert objects["good"]["capabilities"]["cell_grid"]
    assert not objects["bad"]["capabilities"]["cell_grid"]
    assert objects["p0"]["capabilities"]["page_image"]
    assert policy["mode"] == "limited"


def test_missing_table_geometry_retains_logical_reference_but_not_image_location():
    doc = document()
    doc.tables = [Obj(table_id="logical-only",page_index=0,bbox=None,cells=[Obj(cell_id="c1",bbox=None)])]
    policy = DocumentIrValidator._evidence_policy(doc,[Obj(severity="error",code="table_geometry_incomplete")],False)
    caps = next(row["capabilities"] for row in policy["object_availability"] if row["object_id"] == "logical-only")
    assert policy["excluded_target_ids"] == [] and policy["mode"] == "limited"
    assert caps["source_reference"] and caps["cell_grid"]
    assert not caps["source_location"] and not caps["cell_geometry"] and not caps["table_region"]


def test_suspect_section_titles_do_not_block_page_and_text_sources():
    doc = document()
    doc.sections = [Obj(section_id="section", page_indices=[0], title="suspicious")]
    policy = DocumentIrValidator._evidence_policy(doc, [Obj(severity="error", code="section_semantic_titles_invalid")], False)
    assert policy["mode"] == "limited"
    objects = {a["object_id"]: a for a in policy["object_availability"]}
    assert objects["p0"]["capabilities"]["text"]
    assert not objects["section"]["capabilities"]["header"]
