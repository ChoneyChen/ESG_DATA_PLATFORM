"""CLI: run the foundation-16 engine over one fixed Document IR (lab and batch use)."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from esg_targeted.foundation.engine import FoundationEngine, read_calls


def run(ir_root: Path, out_dir: Path, *, model_path: Path | None = None, dry_run: bool = False,
        profile: str = "base16", only_tables: list[str] | None = None, replay: Path | None = None,
        reuse: Path | None = None, rerun: list[str] | None = None) -> dict:
    started = time.perf_counter()
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = FoundationEngine.from_path(ir_root, profile=profile, only_tables=only_tables)
    (out_dir / "plan.json").write_text(json.dumps(engine.plan(), ensure_ascii=False, indent=2))
    if dry_run:
        return engine.plan()
    if replay is not None:
        records = engine.replay(read_calls(replay))
    else:
        from esg_targeted.config import Settings
        from esg_targeted.models.qwen import QwenMlxModel

        model = QwenMlxModel(model_path or Settings.load().qwen_model_path, max_tokens=4096, timeout_seconds=240)
        with (out_dir / "calls.jsonl").open("w") as sink:
            def on_record(record):
                sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                sink.flush()
                payload = record.get("payload") or {}
                print(f"[{record['task_id']}] p{record.get('page_number')} {record['status']} in={record.get('input_tokens')} "
                      f"out={record.get('output_tokens')} {record.get('elapsed_seconds')}s "
                      f"items={len(payload.get('items') or payload.get('facts') or [])}", flush=True)
            saved = {r["task_id"]: r for r in read_calls(reuse) if r.get("status") == "ok"
                     and r["task_id"] not in (rerun or [])} if reuse else {}
            # Lab calls predating request digests are trusted unless named in --rerun.
            records = engine.execute(model, on_record=on_record, reuse=saved, strict_reuse=False)
        model.close()
    summary = engine.conclude(records, elapsed_seconds=time.perf_counter() - started)
    (out_dir / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (out_dir / "result.md").write_text(render_markdown(summary))
    return summary


def render_markdown(summary: dict) -> str:
    period = summary["reporting_period"]
    lines = [f"# {summary['document_label']}",
             f"报告期 {period['year']}（{period['basis']}）；模型调用 {summary['model_calls']} 次，输入 {summary['input_tokens']}、"
             f"输出 {summary['output_tokens']} token，总耗时 {summary['elapsed_seconds']} 秒。", "",
             "|原编号|状态|值|单位|期间|来源|备注|", "|---|---|---|---|---|---|---|"]
    for c in summary["conclusions"]:
        if c.get("members"):
            for m in c["members"]:
                src = m["source"]
                extra = ", ".join(m["flags"]) + (f"; 一致来源{len(m['agreeing_sources'])}" if m["agreeing_sources"] else "") + \
                    (f"; 冲突{[(s['value_raw'], s['unit_raw'], s['page_number']) for s in m['conflicting_sources']]}" if m["conflicting_sources"] else "")
                label = c["code"] + (f" · {m['member']}" if m.get("member") else "")
                lines.append(f"|{label}|{m['status']}|{m['value_raw']}|{m['unit_raw']}|{m['period']}|p{src['page_number']} "
                             f"{(src['row_label'] or '')[:28]}|{extra}|")
        elif c.get("statements"):
            s = c["statements"][0]
            lines.append(f"|{c['code']}|{c['status']}|（方法陈述）| | |p{s['page_number']} {s['block_id']}|{s['text'][:60]}|")
        else:
            lines.append(f"|{c['code']}|{c['status']}| | | | |候选{len(c.get('candidates') or [])}|")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ir_root", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--profile", default="base16", help="metric set: base16 | priority32")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tables", nargs="*")
    parser.add_argument("--replay", type=Path, help="re-bind saved calls.jsonl payloads without model calls")
    parser.add_argument("--reuse", type=Path, help="run the model only for tasks without a saved ok call")
    parser.add_argument("--rerun", nargs="*", help="task ids to call again even when --reuse has them")
    args = parser.parse_args()
    summary = run(args.ir_root, args.out_dir, model_path=args.model, dry_run=args.dry_run, profile=args.profile,
                  only_tables=args.tables, replay=args.replay, reuse=args.reuse, rerun=args.rerun)
    print(render_markdown(summary) if not args.dry_run else json.dumps(summary, ensure_ascii=False, indent=2)[:3000])


if __name__ == "__main__":
    main()
