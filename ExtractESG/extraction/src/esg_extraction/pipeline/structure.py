"""Physical period selection; never supplies a clause concept or invents totals."""

from esg_data.literals import period_years
import re


def period_columns(frame, document):
    if not document.reporting_year or not frame.columns:
        return None
    candidates = []
    for key, path in frame.columns.items():
        years = set(period_years(" ".join(path)))
        if years == {document.reporting_year}:
            candidates.append(key)
    if len(candidates) != 1:
        return None
    # A lone year label cannot disambiguate sibling totals/entities/methods.
    if any(
        re.search(r"合计|总计|total|group|consolidated|合并", " ".join(path), re.I)
        for key, path in frame.columns.items()
        if key not in candidates and not period_years(" ".join(path))
    ):
        return None
    return candidates
