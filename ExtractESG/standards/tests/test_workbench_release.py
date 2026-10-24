from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/workbench_release.py"
spec = spec_from_file_location("workbench_release_test", SCRIPT)
release = module_from_spec(spec)
spec.loader.exec_module(release)


def checkout(tmp_path):
    root = tmp_path / "workspace" / "ExtractESG"
    source = root / "document_ir_extract/backend/src/esg_v2/api.py"
    source.parent.mkdir(parents=True)
    source.write_text("VERSION = 'current'\n")
    return root, source


def test_old_liveness_and_capability_response_does_not_pass_release_handshake(tmp_path):
    root, _ = checkout(tmp_path)
    expected = release.release_identity(root)
    errors = release.check_compatibility({"status": "ok", "capabilities": {"report_sessions_v1": True}},
                                          expected, "document-backend")
    assert any("握手协议" in error for error in errors)
    assert any("运行构建" in error for error in errors)


def test_boot_fingerprint_is_frozen_and_reports_post_start_edits(tmp_path):
    root, source = checkout(tmp_path)
    runtime = release.RuntimeRelease(root, "document-backend")
    initial = runtime.snapshot()
    source.write_text("VERSION = 'updated-contract'\n")
    current = runtime.snapshot()
    assert current["build_fingerprint"] == initial["build_fingerprint"]
    assert current["current_disk_fingerprint"] != initial["build_fingerprint"]
    assert current["restart_required"] and current["source_changed_since_start"]


def test_release_identity_is_content_based_and_portable_between_checkouts(tmp_path):
    left, _ = checkout(tmp_path / "left")
    right, _ = checkout(tmp_path / "right")
    assert release.release_identity(left) == release.release_identity(right)


def test_current_build_with_unready_catalog_is_rejected(tmp_path):
    root, _ = checkout(tmp_path)
    expected = release.release_identity(root)
    payload = {"service": "targeted-backend", **expected,
               "readiness": {"ready": False, "blockers": ["41 个标准包未通过结构校验"]}}
    assert release.check_compatibility(payload, expected, "targeted-backend") == [
        "业务未就绪：41 个标准包未通过结构校验"]


def test_current_structural_readiness_does_not_claim_business_approval(tmp_path):
    root, _ = checkout(tmp_path)
    expected = release.release_identity(root)
    payload = {"service": "targeted-backend", **expected,
               "readiness": {"ready": True, "business_approval": "not_assessed", "blockers": []}}
    assert release.check_compatibility(payload, expected, "targeted-backend") == []
    assert expected["contracts"]["standard_validation"] == "structural_only"


def test_active_catalog_and_dictionary_changes_change_release_identity(tmp_path):
    root, _ = checkout(tmp_path)
    before = release.release_identity(root)
    package = root / "standard_packages/dist/package-id/2.1.1/package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({"format_version": "2.1"}))
    with_package = release.release_identity(root)
    assert with_package["components"]["catalog"] != before["components"]["catalog"]
    dictionary = root.parent / "PlatESG/catalog/metric-dictionary.v1.json"
    dictionary.parent.mkdir(parents=True)
    dictionary.write_text('{"dictionary_version":"1.1.0"}')
    assert release.release_identity(root)["build_fingerprint"] != with_package["build_fingerprint"]


def test_extension_contracts_and_registered_releases_are_frozen_but_drafts_are_not(tmp_path):
    root, _ = checkout(tmp_path)
    (root / 'standards').mkdir()
    base = release.release_identity(root)
    draft = root / 'standards/extensions/authoring/draft/proposal.json'
    draft.parent.mkdir(parents=True)
    draft.write_text('{"status":"proposed"}')
    assert release.release_identity(root) == base
    schema = root / 'standards/extensions/schemas/definition.schema.json'
    schema.parent.mkdir(parents=True)
    schema.write_text('{"type":"object"}')
    contracts = release.release_identity(root)
    assert contracts['components']['standard_contracts'] != base['components']['standard_contracts']
    registry = root / 'standards/extensions/registry.json'
    registry.write_text('{"extensions":[]}')
    registered = release.release_identity(root)
    assert registered['components']['catalog'] != contracts['components']['catalog']
    compiled = root / 'standards/extensions/releases/extension/1.0.0/release.json'
    compiled.parent.mkdir(parents=True)
    compiled.write_text('{"release_digest":"fixed"}')
    assert release.release_identity(root)['components']['catalog'] != registered['components']['catalog']


def test_import_only_allowlist_and_historical_artifacts_are_boot_frozen(tmp_path):
    root, _ = checkout(tmp_path)
    initial = release.release_identity(root)
    index = root.parent / 'PlatESG/catalog/import-contracts/index.json'
    index.parent.mkdir(parents=True)
    index.write_text('{"contracts":[{"contract_id":"historical-import"}]}')
    registered = release.release_identity(root)
    assert registered['components']['catalog'] != initial['components']['catalog']
    historical = root.parent / 'PlatESG/catalog/history/metric-dictionary.1.0.0.json'
    historical.parent.mkdir(parents=True)
    historical.write_text('{"dictionary_version":"1.0.0"}')
    runtime = release.RuntimeRelease(root, 'document-backend')
    historical.write_text('{"dictionary_version":"tampered-history"}')
    assert runtime.snapshot()['restart_required'] is True
    assert runtime.started['components']['catalog'] != registered['components']['catalog']
    state = root.parent / 'PlatESG/.local/runtime/import-contract-registry.json'
    state.parent.mkdir(parents=True)
    state.write_text('{"database_state":true}')
    before = release.release_identity(root)
    state.write_text('{"database_state":"changed"}')
    assert release.release_identity(root) == before


def test_scientific_unit_rule_json_is_boot_frozen(tmp_path):
    root, _ = checkout(tmp_path)
    rules = root.parent / 'database/scripts/unit-conversions.1.2.0.json'
    rules.parent.mkdir(parents=True)
    rules.write_text('{"rule_version":"esg-units-1.2.0","conversions":[]}')
    runtime = release.RuntimeRelease(root, 'publication-backend')
    rules.write_text('{"rule_version":"esg-units-1.2.0","conversions":["tampered"]}')
    assert runtime.snapshot()['restart_required'] is True


def test_release_scope_follows_each_service_and_shared_contracts(tmp_path):
    root, source = checkout(tmp_path)
    document = release.RuntimeRelease(root, 'document-backend')
    publication = release.RuntimeRelease(root, 'publication-backend')
    frontend = release.RuntimeRelease(root, 'workbench-frontend')
    source.write_text("VERSION = 'unrelated-backend-edit'\n")
    assert document.snapshot()['restart_required'] is True
    assert publication.snapshot()['restart_required'] is False
    assert frontend.snapshot()['restart_required'] is False
    (root.parent / 'PlatESG/backend').mkdir(parents=True)
    (root.parent / 'PlatESG/backend/main.py').write_text("VERSION = 'publish-edit'\n")
    assert publication.snapshot()['restart_required'] is True
    shared = release.RuntimeRelease(root, 'publication-backend')
    (root / 'standards/src').mkdir(parents=True)
    (root / 'standards/src/datapoint.py').write_text("CONTRACT = 'shared'\n")
    assert shared.snapshot()['restart_required'] is True
    assert release.release_identity(root, 'workbench-frontend') != release.release_identity(root, 'document-backend')


def test_peer_check_compares_shared_contracts_not_service_files(tmp_path):
    root, _ = checkout(tmp_path)
    document = release.release_identity(root, 'document-backend')
    targeted = release.release_identity(root, 'targeted-backend')
    assert document['build_fingerprint'] != targeted['build_fingerprint']
    payload = {'service': 'targeted-backend', **targeted, 'readiness': {'ready': True, 'blockers': []}}
    assert release.check_peer(payload, document, 'targeted-backend') == []
    drifted = {**payload, 'components': {**targeted['components'], 'catalog': '0' * 64}}
    assert any('共享合同' in error for error in release.check_peer(drifted, document, 'targeted-backend'))
    stale = {**payload, 'restart_required': True}
    assert any('协调重启' in error for error in release.check_peer(stale, document, 'targeted-backend'))
