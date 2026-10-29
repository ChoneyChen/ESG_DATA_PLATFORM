"""A real persisted adapter restart must not consume a held publication event."""
from PlatESG.backend.bridge import DatabaseBridge
from PlatESG.tests.frozen_fixture import seed_frozen_database


def test_restart_preserves_pending_reviews_until_explicit_publication(tmp_path):
    bridge=DatabaseBridge({"PLATESG_DATA_DIR":str(tmp_path/"data"),"PLATESG_AUTO_PUBLISH":"0",
                           "PLATESG_ALLOW_DEMO_SEED":"1","PLATESG_AUTO_IMPORT":"0"})
    seed_frozen_database(bridge.environment)
    bridge.start()
    try:
        original=bridge.call("observations",{"limit":1000})
        internal=bridge.call("internal_detail",{"observation_id":original["items"][0]["observation_id"]})
        bridge.call("review",{"disclosure_id":internal["sources"][0]["disclosure_id"],"decision":"rejected",
                              "operator_ref":"isolated-fixture-reviewer","reason":"持久化重启故障测试，虚构报告"})
        pending=bridge.call("publication_status",{})
        assert any(event["status"]=="pending" for event in pending["events"])
        bridge.close()
        bridge=DatabaseBridge(bridge.environment).start()
        assert bridge.call("observations",{"limit":1000})==original
        after=bridge.call("publication_status",{})
        assert [(e["event_id"],e["status"]) for e in after["events"]]==[(e["event_id"],e["status"]) for e in pending["events"]]
        bridge.call("publish",{})
        current=bridge.call("observations",{"limit":1000})
        assert current["dataset_version"]>original["dataset_version"]
        assert len(current["items"])<len(original["items"])
        assert bridge.call("observations",{"limit":1000,"dataset_version":original["dataset_version"]})==original
    finally:
        bridge.close()
