from esg_v2.control_plane.crawl_intake import CrawlIntake


class FakeCrawl:
    def __init__(self, items):
        self.items = items

    def downloads(self, limit=1000):
        return {"items": self.items[:limit], "total": len(self.items)}


def item(report_id, country="GB", year=2024, esg=True):
    return {"source_report_id": report_id, "country_code": country, "fiscal_year": year,
            "esg_flag": esg, "package_available": True,
            "package_filename": f"{report_id}.zip", "download_status": "ok"}


def test_intake_scan_is_plan_only_and_keeps_crawl_separate(tmp_path):
    crawl = FakeCrawl([item("R1"), item("R2", year=2023, esg=False), item("R3", country="FR")])
    intake = CrawlIntake(crawl, root=tmp_path)
    plan = intake.scan()
    assert plan["mode"] == "plan_only" and plan["enabled"] is False
    assert [row["source_report_id"] for row in plan["candidates"]] == ["R1"]
    assert [row["source_report_id"] for row in plan["filtered_out"]] == ["R2", "R3"]
    assert [action["action"] for action in plan["actions"]] == ["read_esef"]
    assert plan["marked_seen"] is False
    assert len(plan["new_packages"]) == 3


def test_settings_select_planned_actions_and_mark_seen_is_explicit(tmp_path):
    crawl = FakeCrawl([item("R1")])
    intake = CrawlIntake(crawl, root=tmp_path)
    saved = intake.update({"enabled": True, "auto_create_report": True,
                           "auto_queue_extraction": True, "years": [2024], "country": "GB"})
    assert saved["enabled"] is True
    plan = intake.scan(mark_seen=True)
    assert [action["action"] for action in plan["actions"]] == [
        "read_esef", "create_report_draft", "queue_extraction"]
    assert plan["actions"][-1]["profile"] == "foundation16"
    again = intake.scan()
    assert again["new_packages"] == [] and again["actions"] == []
    assert (tmp_path / "settings.json").is_file() and (tmp_path / "state.json").is_file()


def test_settings_reject_unknown_or_invalid_values(tmp_path):
    intake = CrawlIntake(FakeCrawl([]), root=tmp_path)
    for payload in ({"unknown": True}, {"interval_minutes": 1}, {"years": ["2024"]},
                    {"extraction_profile": "unknown"}):
        try:
            intake.update(payload)
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid settings: {payload}")
