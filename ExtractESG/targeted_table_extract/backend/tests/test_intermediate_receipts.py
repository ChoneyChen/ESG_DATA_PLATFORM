"""Synthetic journal browsing, never project results or real task execution."""
import json
import pytest
from esg_runtime.lifecycle import LifecycleBlocked
from .test_intermediate_cleanup import fixture, execute


def test_receipts_empty_index_is_read_only(tmp_path):
    f = fixture(tmp_path)
    result = f.cleaner.receipts(f.sid)
    assert result == {'session_id': f.sid, 'expected_revision': 2, 'items': []}
    assert not (f.output / '.lifecycle').exists()


def test_receipt_summary_supports_recovery_after_page_refresh_without_plan_payload(tmp_path):
    f = fixture(tmp_path); receipt = execute(f)
    result = f.cleaner.receipts(f.sid)
    assert result['expected_revision'] == 2 and len(result['items']) == 1
    row = result['items'][0]
    assert row['cleanup_id'] == receipt['cleanup_id'] and row['state'] == 'quarantined'
    assert row['moved_count'] == len(receipt['moved']) and row['bytes_size'] == receipt['plan']['total_size_bytes']
    assert row['available_actions'][0]['enabled'] is True
    assert 'plan' not in row and 'operator_ref' not in row and row['permanent_deletion'] is False


def test_receipts_are_scoped_and_restored_journal_disables_repeat_restore(tmp_path):
    f = fixture(tmp_path); receipt = execute(f)
    assert f.cleaner.receipts('report-' + 'b' * 32)['items'] == []
    f.cleaner.restore(f.sid, cleanup_id=receipt['cleanup_id'], expected_revision=2,
        reason='合成恢复', operator_ref='synthetic-admin')
    row = f.cleaner.receipts(f.sid)['items'][0]
    assert row['state'] == 'restored' and row['available_actions'][0]['enabled'] is False


def test_tampered_plan_identity_does_not_become_a_restoration_entry(tmp_path):
    f = fixture(tmp_path); receipt = execute(f)
    path = f.cleaner._receipt_path(receipt['cleanup_id'])
    receipt['plan']['candidates'][0]['relative_path'] = 'packets/other.agent.json'
    path.write_text(json.dumps(receipt))
    with pytest.raises(LifecycleBlocked, match='回收日志不可核实'):
        f.cleaner.receipts(f.sid)


def test_symlink_index_does_not_follow_external_journals(tmp_path):
    f = fixture(tmp_path); external = tmp_path / 'external'; external.mkdir()
    f.cleaner.root.mkdir(parents=True); (f.cleaner.root / 'receipts').symlink_to(external, target_is_directory=True)
    with pytest.raises(LifecycleBlocked, match='受控普通目录'):
        f.cleaner.receipts(f.sid)
