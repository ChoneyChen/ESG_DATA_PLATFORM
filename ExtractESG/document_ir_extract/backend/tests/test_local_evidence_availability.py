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
