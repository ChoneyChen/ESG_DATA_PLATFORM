from __future__ import annotations

import copy
import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {"m": MAIN_NS, "r": REL_NS, "pr": PKG_REL_NS}
ET.register_namespace("", MAIN_NS)
ET.register_namespace("r", REL_NS)


class XlsxTemplateDocument:
    """Small OOXML adapter for template-preserving cell reads and writes."""

    def __init__(self, path: Path):
        self.path = path
        if not path.is_file():
            raise FileNotFoundError(path)
        with zipfile.ZipFile(path) as archive:
            self.sheet_paths = self._sheet_paths(archive)
            self.shared_strings = self._shared_strings(archive)

    def rows(self, sheet_name: str, *, min_column: str = "A", max_column: str = "X") -> list[dict[str, str]]:
        sheet_path = self._sheet_path(sheet_name)
        minimum = self.column_number(min_column)
        maximum = self.column_number(max_column)
        with zipfile.ZipFile(self.path) as archive:
            root = ET.fromstring(archive.read(sheet_path))
        output: list[dict[str, str]] = []
        for row in root.findall(".//m:sheetData/m:row", NS):
            row_number = int(row.attrib["r"])
            values: dict[str, str] = {"__row__": str(row_number)}
            for cell in row.findall("m:c", NS):
                reference = cell.attrib.get("r", "")
                column = re.sub(r"\d", "", reference)
                number = self.column_number(column)
                if minimum <= number <= maximum:
                    values[column] = self._cell_value(cell)
            output.append(values)
        return output

    def write_cells(
        self,
        output_path: Path,
        sheet_name: str,
        updates: dict[str, str],
    ) -> list[str]:
        sheet_path = self._sheet_path(sheet_name)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.path, "r") as source, zipfile.ZipFile(output_path, "w") as target:
            for info in source.infolist():
                payload = source.read(info.filename)
                if info.filename == sheet_path:
                    root = ET.fromstring(payload)
                    for reference, value in updates.items():
                        cell = self._get_or_create_cell(root, reference)
                        self._set_inline_string(cell, value)
                    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                target.writestr(copy.copy(info), payload)
        return sorted(updates, key=self._cell_sort_key)

    def _sheet_path(self, sheet_name: str) -> str:
        if sheet_name not in self.sheet_paths:
            raise ValueError(f"Worksheet not found: {sheet_name}")
        return self.sheet_paths[sheet_name]

    @staticmethod
    def _sheet_paths(archive: zipfile.ZipFile) -> dict[str, str]:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            relation.attrib["Id"]: relation.attrib["Target"]
            for relation in relationships.findall("pr:Relationship", NS)
        }
        output: dict[str, str] = {}
        for sheet in workbook.findall(".//m:sheets/m:sheet", NS):
            relationship_id = sheet.attrib[f"{{{REL_NS}}}id"]
            target = targets[relationship_id].lstrip("/")
            output[sheet.attrib["name"]] = target if target.startswith("xl/") else posixpath.normpath(f"xl/{target}")
        return output

    @staticmethod
    def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in archive.namelist():
            return []
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        return ["".join(node.text or "" for node in item.findall(".//m:t", NS)) for item in root.findall("m:si", NS)]

    def _cell_value(self, cell: ET.Element) -> str:
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            return "".join(node.text or "" for node in cell.findall(".//m:t", NS))
        value = cell.findtext("m:v", default="", namespaces=NS)
        if cell_type == "s" and value:
            index = int(value)
            return self.shared_strings[index] if 0 <= index < len(self.shared_strings) else ""
        if cell_type == "b":
            return "TRUE" if value == "1" else "FALSE"
        return value

    @staticmethod
    def _get_or_create_cell(root: ET.Element, reference: str) -> ET.Element:
        match = re.fullmatch(r"([A-Z]+)(\d+)", reference)
        if not match:
            raise ValueError(f"Invalid cell reference: {reference}")
        row_number = int(match.group(2))
        sheet_data = root.find("m:sheetData", NS)
        if sheet_data is None:
            sheet_data = ET.SubElement(root, f"{{{MAIN_NS}}}sheetData")
        row = next((item for item in sheet_data.findall("m:row", NS) if int(item.attrib["r"]) == row_number), None)
        if row is None:
            row = ET.Element(f"{{{MAIN_NS}}}row", {"r": str(row_number)})
            rows = list(sheet_data)
            insert_at = next((i for i, item in enumerate(rows) if int(item.attrib["r"]) > row_number), len(rows))
            sheet_data.insert(insert_at, row)
        cell = next((item for item in row.findall("m:c", NS) if item.attrib.get("r") == reference), None)
        if cell is None:
            cell = ET.Element(f"{{{MAIN_NS}}}c", {"r": reference})
            cells = list(row)
            target_column = XlsxTemplateDocument.column_number(match.group(1))
            insert_at = next(
                (
                    i
                    for i, item in enumerate(cells)
                    if XlsxTemplateDocument.column_number(re.sub(r"\d", "", item.attrib.get("r", "A"))) > target_column
                ),
                len(cells),
            )
            row.insert(insert_at, cell)
        return cell

    @staticmethod
    def _set_inline_string(cell: ET.Element, value: str) -> None:
        for child in list(cell):
            cell.remove(child)
        cell.attrib["t"] = "inlineStr"
        inline = ET.SubElement(cell, f"{{{MAIN_NS}}}is")
        text = ET.SubElement(inline, f"{{{MAIN_NS}}}t")
        if value.startswith(" ") or value.endswith(" ") or "\n" in value:
            text.attrib[f"{{{XML_NS}}}space"] = "preserve"
        text.text = value

    @staticmethod
    def column_number(column: str) -> int:
        value = 0
        for character in column.upper():
            if not "A" <= character <= "Z":
                raise ValueError(f"Invalid column: {column}")
            value = value * 26 + ord(character) - 64
        return value

    @staticmethod
    def _cell_sort_key(reference: str) -> tuple[int, int]:
        match = re.fullmatch(r"([A-Z]+)(\d+)", reference)
        if not match:
            return (0, 0)
        return (int(match.group(2)), XlsxTemplateDocument.column_number(match.group(1)))
