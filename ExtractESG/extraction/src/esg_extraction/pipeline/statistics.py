"""Incremental run statistics. No facts, evidence or publication decisions are rewritten."""
from collections import Counter
from esg_data.periods import period_label


class RunStatistics:
    def __init__(self, clauses):
        self.required = {key: clause.purposes.enrichment.identity_axes for key, clause in clauses.items()}
        self.facts = {}
        self.links = {}
        self.kinds = Counter()
        self.periods = Counter()
        self.relations = {name: Counter() for name in ("match", "different", "uncertain")}
        self.incomplete = 0

    @property
    def fact_count(self):
        return len(self.facts)

    def record(self, fact, mapping):
        fid = fact.source_fact_id
        current = (fact.kind, period_label(fact.identity.reporting_period.value))
        previous = self.facts.get(fid)
        if previous != current:
            if previous is not None:
                self.kinds[previous[0]] -= 1
                self.periods[previous[1]] -= 1
                if not self.periods[previous[1]]:
                    del self.periods[previous[1]]
            self.kinds[current[0]] += 1
            self.periods[current[1]] += 1
            self.facts[fid] = current

        missing = mapping.match != "different" and any(
            field is None or field.state not in {"stated", "inherited"}
            for axis in self.required[mapping.clause_key]
            for field in [getattr(fact.identity, axis, None) or fact.identity.dimensions.get(axis)]
        )
        key = (fid, mapping.clause_key)
        previous = self.links.get(key)
        current = (mapping.match, missing)
        if previous == current:
            return
        if previous is not None:
            members = self.relations[previous[0]]
            members[fid] -= 1
            if not members[fid]:
                del members[fid]
            self.incomplete -= previous[1]
        self.links[key] = current
        self.relations[current[0]][fid] += 1
        self.incomplete += missing

    def summary(self, coverage, errors, final, rejected_count=0):
        interrupted = bool(errors) or len(coverage) != len(self.required) or any(
            c.state == "failed" or set(c.reasons) & {
                "execution_budget_exhausted", "object_budget_exhausted", "document_read_failed"
            } for c in coverage
        )
        return {
            "source_facts": self.fact_count,
            "quantitative": self.kinds["quantitative"],
            "qualitative": self.kinds["assertion"],
            "assessment": {
                "execution_state": "running" if not final else "interrupted" if interrupted else "completed",
                "search_coverage": "in_progress" if not final else "incomplete" if interrupted else "bounded_complete",
                "capture_result": "usable" if self.facts else "empty",
                "metadata_completeness": "not_requested" if not any(self.required.values()) or not self.facts else "partial" if self.incomplete else "complete",
                "comparison_status": "not_evaluated",
                "target_facts": len(self.relations["match"]),
                "other_facts": len(self.relations["different"]),
                "unresolved_facts": len(self.relations["uncertain"]),
                "rejected_candidates": rejected_count,
                "periods": dict(self.periods),
            },
        }
