"""One directory is one clause. Group folders contain presentation metadata only."""

from __future__ import annotations
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
from .base import digest
from .contract import ClauseContract
from .descriptor import ClauseDescriptor, parse_record


class ClauseRepositoryError(ValueError):
    pass


def _write_json(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".clause-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(body, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ClauseRepository:
    def __init__(self, root):
        self.root = Path(root).resolve()

    @contextmanager
    def _locked(self, *, write=False):
        # All public readers and writers share this lock. Private methods never lock.
        with (self.root / ".repository.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _catalog(self):
        return json.loads((self.root / "_catalog.json").read_text(encoding="utf-8"))

    def catalog(self):
        with self._locked():
            return self._catalog()

    def _get(self, key, catalog):
        if not isinstance(key, str) or "/" in key or "\\" in key or ".." in key:
            raise ClauseRepositoryError("Invalid clause identity")
        row = next((r for r in catalog["clauses"] if r["clause_key"] == key), None)
        if row is None:
            raise ClauseRepositoryError(f"Unknown clause {key}")
        path = (self.root / row["path"] / "clause.json").resolve()
        if not path.is_relative_to(self.root):
            raise ClauseRepositoryError("Clause path escapes repository")
        clause = parse_record(json.loads(path.read_text(encoding="utf-8")))
        if clause.clause_key != key or clause.clause_digest != row["clause_digest"]:
            raise ClauseRepositoryError(
                "Clause identity or content differs from its index; reindex explicitly"
            )
        return clause

    def get(self, key):
        with self._locked():
            return self._get(key, self._catalog())

    def _groups(self):
        return [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(self.root.glob("*/group.json"))
        ]

    def groups(self):
        with self._locked():
            return self._groups()

    def _selections(self):
        return json.loads((self.root / "selections.json").read_text(encoding="utf-8"))[
            "profiles"
        ]

    def selections(self):
        with self._locked():
            return self._selections()

    def select(self, keys=None, *, selection=None):
        with self._locked():
            if selection is not None:
                profiles = self._selections()
                if selection not in profiles:
                    raise ClauseRepositoryError(f"Unknown clause selection {selection}")
                if keys:
                    raise ClauseRepositoryError(
                        "Provide clause identities or a named selection"
                    )
                keys = profiles[selection]
            if not keys:
                raise ClauseRepositoryError("Select at least one independent clause")
            catalog = self._catalog()
            clauses = {key: self._get(key, catalog) for key in dict.fromkeys(keys)}
            blocked = [
                key for key, clause in clauses.items() if clause.readiness != "ready"
            ]
            if blocked:
                raise ClauseRepositoryError(f"Blocked clause contracts: {blocked}")
            return clauses

    def selection_options(self, keys):
        """An explicit preview; never execute or silently skip ineligible entries."""
        with self._locked():
            catalog = self._catalog()
            rows = [self._get(key, catalog) for key in dict.fromkeys(keys)]
            return {"eligible": [row.clause_key for row in rows if row.readiness == "ready"],
                    "excluded": [{"clause_key": row.clause_key, "status": row.readiness,
                        "reasons": [row.disposition_reason] if isinstance(row, ClauseDescriptor) else row.blocking_reasons}
                        for row in rows if row.readiness != "ready"], "executes": False}

    def freeze(self, clauses):
        if any(not isinstance(clause, ClauseContract) or clause.readiness != "ready" for clause in clauses.values()):
            raise ClauseRepositoryError("Only executable value clauses can be frozen for extraction")
        payload = {
            "schema_version": "clause-selection-1.0",
            "clauses": {
                key: clause.model_dump(mode="json") for key, clause in clauses.items()
            },
        }
        return {**payload, "digest": digest(payload)}

    @staticmethod
    def thaw(snapshot):
        if snapshot.get("schema_version") != "clause-selection-1.0" or snapshot.get(
            "digest"
        ) != digest({key: value for key, value in snapshot.items() if key != "digest"}):
            raise ClauseRepositoryError("Frozen clause selection digest differs")
        clauses = {
            key: ClauseContract.model_validate(body).verify()
            for key, body in snapshot["clauses"].items()
        }
        if any(key != clause.clause_key for key, clause in clauses.items()):
            raise ClauseRepositoryError("Frozen clause identity differs")
        return clauses

    def save(self, clause, *, expected_digest=None):
        clause = clause.seal().verify()
        with self._locked(write=True):
            existing = next(
                (
                    r
                    for r in self._catalog()["clauses"]
                    if r["clause_key"] == clause.clause_key
                ),
                None,
            )
            if existing and existing["group_id"] != clause.group_id:
                raise ClauseRepositoryError(
                    "A clause identity cannot be duplicated under a different group"
                )
            directory = self.root / clause.group_id / clause.clause_key
            if (
                directory.is_symlink()
                or directory.parent.is_symlink()
                or not directory.resolve().is_relative_to(self.root)
            ):
                raise ClauseRepositoryError("Clause path escapes its owner")
            path = directory / "clause.json"
            if path.is_file():
                current = parse_record(json.loads(path.read_text(encoding="utf-8")))
                if expected_digest != current.clause_digest:
                    raise ClauseRepositoryError("Clause changed; reload before saving")
            elif expected_digest is not None:
                raise ClauseRepositoryError("Base clause no longer exists")
            _write_json(path, clause.model_dump(mode="json"))
            self._reindex()
        return clause

    def reindex(self):
        with self._locked(write=True):
            return self._reindex()

    def _reindex(self):
        rows = []
        for path in sorted(self.root.glob("*/*/clause.json")):
            if not path.resolve().is_relative_to(self.root):
                raise ClauseRepositoryError("Clause path escapes repository")
            clause = parse_record(json.loads(path.read_text(encoding="utf-8")))
            if (
                path.parent.name != clause.clause_key
                or path.parent.parent.name != clause.group_id
            ):
                raise ClauseRepositoryError("Clause directory identity differs")
            rows.append(
                {
                    "clause_key": clause.clause_key,
                    "group_id": clause.group_id,
                    "title": clause.title,
                    "data_kind": clause.data_kind,
                    "entry_kind": "structure" if isinstance(clause, ClauseDescriptor) else "value",
                    "readiness": clause.readiness,
                    "clause_digest": clause.clause_digest,
                    "path": path.parent.relative_to(self.root).as_posix(),
                }
            )
        if len({row["clause_key"] for row in rows}) != len(rows):
            raise ClauseRepositoryError("Duplicate clause identity")
        known = {row["clause_key"] for row in rows}
        for row in rows:
            record = self._get(row["clause_key"], {"clauses": rows})
            if isinstance(record, ClauseDescriptor):
                if (len(set(record.member_clause_keys)) != len(record.member_clause_keys)
                    or set(record.member_clause_keys) - known
                    or record.clause_key in record.member_clause_keys):
                    raise ClauseRepositoryError("Structure members must be distinct existing independent clauses")
        for key, selected in self._selections().items():
            if len(set(selected)) != len(selected) or set(selected) - known:
                raise ClauseRepositoryError(f"Invalid clause selection {key}")
        groups = {group["group_id"]: group for group in self._groups()}
        for group_id in {row["group_id"] for row in rows}:
            body = {
                "group_id": group_id,
                "title": groups.get(group_id, {}).get("title", group_id),
                "clauses": [
                    row["clause_key"] for row in rows if row["group_id"] == group_id
                ],
            }
            _write_json(self.root / group_id / "group.json", body)
        payload = {"schema_version": "clause-catalog-4.0", "clauses": rows}
        _write_json(self.root / "_catalog.json", payload)
        return payload
