from pathlib import Path
import pytest
from esg_clauses.repository import ClauseRepository, ClauseRepositoryError
from esg_clauses.templates import new_clause


def test_single_clause_needs_no_named_selection_or_group_file(tmp_path):
    clause = new_clause("LOCAL_01", "LOCAL", "quantitative", "独立测量").model_copy(
        update={"readiness": "ready", "blocking_reasons": []}).seal()
    path = tmp_path / "LOCAL" / "LOCAL_01"
    path.mkdir(parents=True)
    (path / "clause.json").write_text(clause.model_dump_json())
    repo = ClauseRepository(tmp_path)
    repo.reindex()
    assert repo.selections() == {}
    selected = repo.select([clause.clause_key])
    assert selected[clause.clause_key].clause_digest == clause.clause_digest
    assert repo.thaw(repo.freeze(selected))[clause.clause_key] == clause
    with pytest.raises(ClauseRepositoryError, match="Unknown clause selection"):
        repo.select(selection="not-installed")
