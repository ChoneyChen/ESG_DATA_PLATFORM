"""Reserved intake plan between CrawlESG downloads and this chain.

CrawlESG downloads ESSEF packages and writes a ledger. This module never starts
the crawler, edits its files or imports a package. It persists operator settings
and computes a dry-run intake plan from the read-only ledger. The actual
read/derivation/extraction workers can consume this plan in a later stage.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "watch_downloads": True,
    "auto_read_esef": True,
    "auto_create_report": False,
    "auto_queue_extraction": False,
    "interval_minutes": 60,
    "country": "GB",
    "years": [2024, 2025],
    "esg_only": True,
    "extraction_profile": "foundation16",
}
_PROFILES = {"foundation16", "platform_major", "quantitative", "base16", "priority32"}
_MAX_SEEN = 5000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CrawlIntake:
    def __init__(self, crawl, root: str | Path | None = None):
        default = Path(__file__).resolve().parents[6] / ".local" / "crawl-intake"
        self.crawl = crawl
        self.root = Path(root or os.getenv("ESG_CRAWL_INTAKE_DIR", default)).expanduser()
        self.settings_path = self.root / "settings.json"
        self.state_path = self.root / "state.json"

    def _load(self, path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
        if not path.is_file():
            return dict(fallback)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else dict(fallback)
        except (OSError, ValueError):
            return dict(fallback)

    def _save(self, path: Path, value: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def settings(self) -> dict[str, Any]:
        stored = self._load(self.settings_path, DEFAULT_SETTINGS)
        return {**DEFAULT_SETTINGS, **{key: stored[key] for key in DEFAULT_SETTINGS if key in stored}}

    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(payload) - set(DEFAULT_SETTINGS))
        if unknown:
            raise ValueError(f"未知设置：{', '.join(unknown)}")
        settings = {**self.settings(), **payload}
        for key in ("enabled", "watch_downloads", "auto_read_esef", "auto_create_report",
                    "auto_queue_extraction", "esg_only"):
            if not isinstance(settings[key], bool):
                raise ValueError(f"{key} 必须是布尔值")
        try:
            interval = int(settings["interval_minutes"])
        except (TypeError, ValueError) as exc:
            raise ValueError("interval_minutes 必须是整数") from exc
        if interval < 5 or interval > 10080:
            raise ValueError("interval_minutes 必须在 5–10080 之间")
        settings["interval_minutes"] = interval
        years = settings.get("years")
        if not isinstance(years, list) or any(not isinstance(year, int) or year < 1900 or year > 2200 for year in years):
            raise ValueError("years 必须是 1900–2200 的年份列表")
        settings["years"] = sorted(set(years))
        country = str(settings.get("country") or "").strip().upper()
        if len(country) > 8:
            raise ValueError("country 代码过长")
        settings["country"] = country
        if settings.get("extraction_profile") not in _PROFILES:
            raise ValueError("extraction_profile 不在支持的提取配置中")
        settings["updated_at"] = _now()
        self._save(self.settings_path, settings)
        return settings

    def scan(self, *, mark_seen: bool = False) -> dict[str, Any]:
        settings = self.settings()
        state = self._load(self.state_path, {"seen": [], "last_scan_at": None})
        seen = [item for item in state.get("seen", []) if isinstance(item, str)]
        seen_set = set(seen)
        listing = self.crawl.downloads(limit=1000)
        packages = listing.get("items") or []
        new = [item for item in packages if item.get("source_report_id") not in seen_set]
        candidates, filtered = [], []
        for item in new:
            if settings["country"] and str(item.get("country_code") or "").upper() != settings["country"]:
                filtered.append(item)
                continue
            if settings["years"] and item.get("fiscal_year") not in settings["years"]:
                filtered.append(item)
                continue
            if settings["esg_only"] and item.get("esg_flag") is not True:
                filtered.append(item)
                continue
            candidates.append(item)
        actions = []
        for item in candidates:
            report_id = item["source_report_id"]
            if settings["auto_read_esef"]:
                actions.append({"action": "read_esef", "source_report_id": report_id,
                                "package_filename": item.get("package_filename"),
                                "reason": "本地 ESEF 包已下载并校验"})
            if settings["auto_create_report"]:
                actions.append({"action": "create_report_draft", "source_report_id": report_id,
                                "reason": "预留：创建报告身份草稿，仍需人工确认发布主体与报告期"})
            if settings["auto_queue_extraction"]:
                actions.append({"action": "queue_extraction", "source_report_id": report_id,
                                "profile": settings["extraction_profile"],
                                "reason": "预留：ESEF 读取成功后送入提取计划"})
        if mark_seen:
            merged = (seen + [item["source_report_id"] for item in new])[-_MAX_SEEN:]
            self._save(self.state_path, {"seen": merged, "last_scan_at": _now()})
        return {
            "settings": settings,
            "scanned_at": _now(),
            "mode": "plan_only",
            "enabled": settings["enabled"],
            "new_packages": new,
            "candidates": candidates,
            "filtered_out": filtered,
            "actions": actions,
            "marked_seen": mark_seen,
            "note": ("自动派发尚未启用；这里只生成计划，不下载、不读取、不调用模型、不写候选。"
                     if not settings["enabled"] else
                     "设置已启用但仍需接入执行 worker；扫描结果仍是计划，不会自动写库。"),
        }
