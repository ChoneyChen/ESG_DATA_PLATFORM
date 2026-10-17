"""First-run projections must not activate drafts or erase recovery evidence."""
import importlib.util
import json
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / 'ExtractESG/scripts/bootstrap_extension_runtime.py'
spec = importlib.util.spec_from_file_location('extension_bootstrap', SOURCE)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def test_first_run_is_empty_idempotent_and_preserves_drafts(tmp_path):
    draft = tmp_path / 'proposals/draft.json'
    draft.parent.mkdir()
    draft.write_text('{"unapproved":true}')
    assert bootstrap.initialize(tmp_path) == {'created': True, 'registration_count': 0}
    first = (tmp_path / 'registry.json').read_bytes()
    assert json.loads(first)['registrations'] == []
    assert bootstrap.initialize(tmp_path) == {'created': False, 'registration_count': 0}
    assert (tmp_path / 'registry.json').read_bytes() == first
    assert draft.read_text() == '{"unapproved":true}'


def test_corrupt_registry_is_not_replaced(tmp_path):
    path = tmp_path / 'registry.json'
    path.write_text('{"schema_version":"old","registrations":null}')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='invalid'):
        bootstrap.initialize(tmp_path)
    assert path.read_bytes() == before


def test_orphan_release_requires_recovery_instead_of_empty_reset(tmp_path):
    path = tmp_path / 'releases/ext/1.0.0/release.json'
    path.parent.mkdir(parents=True)
    path.write_text('{"historical_release":true}')
    with pytest.raises(ValueError, match='without a registry'):
        bootstrap.initialize(tmp_path)
    assert not (tmp_path / 'registry.json').exists()
    assert json.loads(path.read_text()) == {'historical_release': True}
