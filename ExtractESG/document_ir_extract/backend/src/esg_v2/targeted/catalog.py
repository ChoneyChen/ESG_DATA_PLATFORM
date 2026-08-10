from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from esg_v2.evidence.contracts import EvidenceAtom
from esg_v2.targeted.contracts import DisclosureGroup
from esg_v2.targeted.features import DisclosureFeatureExtractor, matched_aliases, normalized_match_text


TABLE_TYPES = {
    "table_cell_atom",
    "table_row_atom",
    "table_region_atom",
    "logical_table_row_atom",
    "logical_table_region_atom",
}
ROW_TYPES = {"table_row_atom", "logical_table_row_atom"}
@dataclass(frozen=True)
class DisclosureCatalog:
    groups: list[DisclosureGroup]
    atom_to_group: dict[str, str]

    @classmethod
    def build(cls, atoms: list[EvidenceAtom]) -> "DisclosureCatalog":
        grouped: dict[str, list[EvidenceAtom]] = defaultdict(list)
        atom_to_group: dict[str, str] = {}
        for atom in atoms:
            group_id = cls._group_id(atom)
            grouped[group_id].append(atom)
            atom_to_group[atom.atom_id] = group_id
        groups = [cls._group(group_id, rows) for group_id, rows in sorted(grouped.items())]
        return cls(groups=groups, atom_to_group=atom_to_group)

    def by_id(self) -> dict[str, DisclosureGroup]:
        return {group.group_id: group for group in self.groups}

    @staticmethod
    def _group_id(atom: EvidenceAtom) -> str:
        if atom.logical_table_id:
            return f"disclosure-logical-table:{atom.logical_table_id}"
        if atom.source_table_ids:
            return f"disclosure-table:{atom.source_table_ids[0]}"
        if atom.source_figure_ids:
            return f"disclosure-figure:{atom.source_figure_ids[0]}"
        return f"disclosure-atom:{atom.atom_id}"

    @classmethod
    def _group(cls, group_id: str, atoms: list[EvidenceAtom]) -> DisclosureGroup:
        representative = min(atoms, key=cls._representative_rank)
        rows = [atom.source_text for atom in atoms if atom.atom_type in ROW_TYPES and atom.source_text.strip()]
        headers = list(dict.fromkeys(header for atom in atoms for header in atom.header_paths if header.strip()))
        source_text = representative.source_text.strip()
        if representative.atom_type not in {"table_region_atom", "logical_table_region_atom"} and rows:
            source_text = "\n".join(rows)
        search_text = "\n".join(
            dict.fromkeys(
                part
                for part in [representative.search_text, source_text, *headers]
                if part and part.strip()
            )
        )
        pages = sorted({page for atom in atoms for page in atom.location.page_indices})
        page_numbers = sorted({page for atom in atoms for page in atom.location.page_numbers})
        sections = max((atom.location.section_path for atom in atoms), key=len, default=[])
        source_table_ids = list(dict.fromkeys(table_id for atom in atoms for table_id in atom.source_table_ids))
        flags = list(dict.fromkeys(flag for atom in atoms for flag in atom.quality_flags))
        group_type = cls._group_type(representative)
        features = DisclosureFeatureExtractor.extract(
            source_text,
            section_path=sections,
            group_type=group_type,
        )
        if any(atom.atom_type == "index_atom" for atom in atoms):
            features.index_like = True
        return DisclosureGroup(
            group_id=group_id,
            group_type=group_type,
            atom_ids=[atom.atom_id for atom in atoms],
            representative_atom_id=representative.atom_id,
            source_text=source_text,
            search_text=search_text,
            page_indices=pages,
            page_numbers=page_numbers,
            section_path=sections,
            source_table_ids=source_table_ids,
            row_texts=rows,
            header_texts=headers,
            quality_flags=flags,
            features=features,
        )

    @staticmethod
    def _representative_rank(atom: EvidenceAtom) -> tuple[int, int, str]:
        priority = {
            "table_region_atom": 0,
            "logical_table_region_atom": 1,
            "paragraph_atom": 2,
            "list_item_atom": 3,
            "figure_atom": 4,
            "logical_table_row_atom": 5,
            "table_row_atom": 6,
            "table_cell_atom": 7,
            "index_atom": 8,
        }.get(atom.atom_type, 9)
        return priority, -len(atom.source_text), atom.atom_id

    @staticmethod
    def _group_type(atom: EvidenceAtom) -> str:
        if atom.atom_type.startswith("logical_table"):
            return "logical_table"
        if atom.atom_type in TABLE_TYPES:
            return "table"
        if atom.atom_type == "figure_atom":
            return "figure"
        if atom.atom_type in {"paragraph_atom", "list_atom", "list_item_atom", "footnote_atom"}:
            return "paragraph"
        return "other"
