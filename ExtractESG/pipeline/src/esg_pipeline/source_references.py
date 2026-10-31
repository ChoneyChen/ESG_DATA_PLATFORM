"""Durable references owned by the current extraction coordinator."""
import json
from esg_contracts.sessions import TERMINAL


def extraction_references(db, *, asset_id=None, source_sha256=None, processing_id=None, run_ids=()):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='clause_extraction_plans'").fetchone():
        return []
    matches=[]
    for identity, raw in db.execute("SELECT id,body FROM clause_extraction_plans"):
        try:
            state=json.loads(raw)
            if not isinstance(state,dict) or state.get("session_id")!=identity or not isinstance(state.get("request"),dict) or not state.get("status"):
                raise ValueError("Invalid extraction session")
            if state["status"] in TERMINAL:
                continue
            source=state["request"].get("input",{})
            if ((asset_id and source.get("asset_id")==asset_id)
                or (source_sha256 and state.get("source_sha256")==source_sha256)
                or (processing_id and state.get("processing_id")==processing_id)
                or (state.get("ir_run_id") and state["ir_run_id"] in run_ids)):
                matches.append({"session_id":identity,"status":state["status"]})
        except (ValueError,TypeError,AttributeError) as exc:
            raise ValueError("提取任务来源引用无法核实；请修复任务记录后再清理。") from exc
    return matches
