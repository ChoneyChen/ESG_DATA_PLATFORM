"""Read-only evidence action projection; execution endpoints remain authoritative."""
from copy import deepcopy
from itertools import islice
import json
from pathlib import Path
import re

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_MANIFEST_LIMIT = 4 * 1024 * 1024


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _ids(value):
    return {v for v in _list(value) if isinstance(v, str) and v.strip() and len(v) <= 512}


def _id(value):
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _manifest(root, run_id):
    if not _id(run_id):
        return None
    directory = root / run_id
    path = directory / "manifest.json"
    try:
        if directory.is_symlink() or path.is_symlink() or directory.resolve().parent != root or not path.is_file():
            return None
        if path.stat().st_size > _MANIFEST_LIMIT:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("run_id") != run_id:
            return None
        if not isinstance(value.get("schema_version"), str) or not value["schema_version"].startswith("document-ir-v0."):
            return None
        return value
    except (OSError, ValueError, TypeError):
        return None


def _source_sha(manifest):
    value = _dict(manifest.get("source")).get("pdf_sha256") or _dict(manifest.get("source")).get("sha256")
    return value if isinstance(value, str) and _SHA.fullmatch(value) else None


def _same_source(parent, child, document):
    """A direct repair must keep the exact PDF and declared producer lineage."""
    sha = _source_sha(parent)
    if not sha or _source_sha(child) != sha:
        return False
    expected = document.get("source_sha256")
    if expected is not None and expected != sha:
        return False
    for field in ("document_id", "lineage_id", "ocr_run_id"):
        old = parent.get(field) or (_dict(parent.get("source")).get(field) if field == "ocr_run_id" else None)
        new = child.get(field) or (_dict(child.get("source")).get(field) if field == "ocr_run_id" else None)
        if (old is not None or new is not None) and (not isinstance(old, str) or not old or new != old):
            return False
    return True


def report_evidence_actions(plan, native, existing_actions, *, ir_root, execution_status=None,
                            integrity_check=None, max_candidates=256):
    """Add bounded repair/transition suggestions only for a real current IR.

    No task is queued, no artifact changes, and no execution authority is inferred
    from a suggested action. ``integrity_check`` optionally accepts an IR run ID.
    """
    plan, native = _dict(plan), _dict(native)
    actions = deepcopy([a for a in _list(existing_actions) if isinstance(a, dict)])
    documents = _list(plan.get("documents"))
    document = _dict(documents[0]) if documents else {}
    current_id = _id(document.get("ir_run_id"))
    root = Path(ir_root).resolve()
    # A missing parent is not a root-revision relation: never scan None == None.
    if not current_id:
        return [a for a in actions if a.get("action") not in {"local_repair", "evidence_transition"}]
    parent = _manifest(root, current_id)
    if not parent:
        return [a for a in actions if a.get("action") not in {"local_repair", "evidence_transition"}]
    expected = document.get("source_sha256")
    if expected is not None and _source_sha(parent) != expected:
        return [a for a in actions if a.get("action") not in {"local_repair", "evidence_transition"}]

    config = _dict(plan.get("config"))
    policy = _dict(document.get("evidence_policy"))
    targets = _ids(policy.get("excluded_target_ids"))
    for gap in _list(native.get("gaps")):
        gap = _dict(gap)
        if gap.get("kind") in {"source_reference", "column_header", "header", "source"} or gap.get("next_action") == "request_local_repair":
            targets.update(_ids(gap.get("object_ids")))
            if isinstance(gap.get("object_id"), str) and gap["object_id"].strip():
                targets.add(gap["object_id"])
    for job in _list(native.get("jobs")):
        for gap in _list(_dict(_dict(_dict(job).get("summary")).get("agent")).get("gaps")):
            gap = _dict(gap)
            if gap.get("next_action") in {"local_repair", "repair_ir", "repair"}:
                targets.update(_ids(gap.get("object_ids")))
    provider = config.get("review_provider")
    if targets and (document.get("status") == "waiting_review" or document.get("native_session_id")) and provider in {"local_qwen", "local_nuextract", "qiniu"} and not any(a.get("action") == "local_repair" for a in actions):
        actions.append({"action": "local_repair", "label": "修复受影响证据", "scope": {"ir_run_id": current_id, "target_ids": sorted(targets)},
            "endpoint": f"/api/document-ir/jobs/{current_id}/repair", "calls_model": True, "incurs_cost": provider == "qiniu",
            "payload": {"parent_ir_run_id": current_id, "target_ids": sorted(targets), "reason_code": "report_evidence_gap",
                "requested_by": "report-session-user", "execute_vlm_reviews": True, "review_provider": provider}})
    elif document.get("status") == "waiting_review" and not targets and not any(a.get("action") == "review" for a in actions):
        actions.append({"action": "review", "label": "查看文档问题", "scope": {"ir_run_id": current_id},
            "endpoint": "/api/document-ir/review-worklist", "method": "GET", "calls_model": False, "incurs_cost": False})

    status = execution_status or native.get("execution_status")
    if status in {"queued", "running", "stopping"} or not (targets or not document.get("native_session_id")) or any(a.get("action") == "evidence_transition" for a in actions):
        return actions
    # Bound directory enumeration as well as manifest reads on every list view.
    limit = min(1024, max(1, max_candidates)) if isinstance(max_candidates, int) and not isinstance(max_candidates, bool) else 256
    try:
        directories = list(islice(root.iterdir(), limit + 1))
    except OSError:
        return actions
    if len(directories) > limit:
        return actions  # Do not suggest a guessed revision from a truncated search.
    for directory in sorted(directories, key=lambda p: p.name, reverse=True):
        candidate_id = _id(directory.name)
        if not candidate_id or candidate_id == current_id or not directory.is_dir():
            continue
        candidate = _manifest(root, candidate_id)
        if not candidate or candidate.get("parent_ir_run_id") != current_id:
            continue
        usable = candidate.get("can_build_evidence") is True or (config.get("allow_limited_evidence") is True
            and candidate.get("can_build_limited_evidence") is True and _dict(candidate.get("evidence_policy")).get("mode") == "limited")
        if not usable or not _same_source(parent, candidate, document):
            continue
        if integrity_check:
            try:
                checked = integrity_check(candidate_id)
                if checked is False:
                    continue
            except (OSError, ValueError, TypeError, KeyError):
                continue
        # Both endpoint branches resume the extraction plan after adoption;
        # this can start a first extraction as well as re-run existing projections.
        actions.append({"action": "evidence_transition", "label": "采用修复后的证据版本",
            "endpoint": f"/api/report-sessions/{plan.get('plan_id')}/evidence-transition",
            "scope": {"old_ir_run_id": current_id, "new_ir_run_id": candidate_id, "target_ids": sorted(targets)},
            "calls_model": True, "incurs_cost": config.get("semantic_provider") == "qiniu_vlm",
            "payload": {"new_ir_run_id": candidate_id, "affected_object_ids": sorted(targets),
                "reason": "Adopt the completed local repair of the specified evidence objects", "requested_by": "report-session-user"}})
        break
    return actions
