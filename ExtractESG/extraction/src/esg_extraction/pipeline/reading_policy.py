"""Bounded reading and task-local progress, shared by both data readers.

Capture a useful physical object completely, including its historical columns.
Optional attributes use the evidence already prepared; they never launch OCR.
An attempted attribute is work progress, not a reusable model interpretation.
"""
from dataclasses import dataclass, field, asdict


@dataclass(frozen=True)
class ReadingPolicy:
    primary_objects_per_source: int = 4
    fallback_objects_per_source: int = 4
    target_page_rounds: int = 1
    extra_pages: int = 4
    extra_pages_per_report: int = 8
    context_calls_per_clause: int = 2
    context_tokens_per_fact: int = 384


POLICY = ReadingPolicy()


@dataclass
class ClauseReadingProgress:
    selected_object: dict | None = None
    object_complete: bool = False
    page_rounds: int = 0
    context_calls: int = 0
    attempted_attributes: dict[str, list[str]] = field(default_factory=dict)
    search: dict | None = None

    def select(self, reference):
        if self.selected_object is None:
            self.selected_object = reference.model_dump(mode="json")

    def can_read_context(self):
        return self.context_calls < POLICY.context_calls_per_clause

    def pending_attributes(self, missing):
        return {fid:[axis for axis in axes if axis not in self.attempted_attributes.get(fid, [])]
            for fid,axes in missing.items()}

    def finish_attributes(self, requested):
        for fid, axes in requested.items():
            self.attempted_attributes[fid] = sorted(set(self.attempted_attributes.get(fid, [])) | set(axes))


class ReadingProgress:
    def __init__(self, snapshot):
        self.clauses = {key:ClauseReadingProgress(**value) for key,value in snapshot.items()}

    def for_clause(self, key):
        return self.clauses.setdefault(key, ClauseReadingProgress())

    def reserve_pages(self, key, extra_pages_used):
        clause = self.for_clause(key)
        available = min(POLICY.extra_pages, POLICY.extra_pages_per_report-extra_pages_used)
        if clause.selected_object is not None or clause.page_rounds >= POLICY.target_page_rounds or available <= 0:
            return 0
        clause.page_rounds += 1
        return available

    def snapshot(self):
        return {key:asdict(value) for key,value in self.clauses.items()}
