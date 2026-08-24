from __future__ import annotations

import json
import zipfile

import pytest

from ouroboros.spreadsheets import SpreadsheetError, inspect_xlsx, parse_range
from ouroboros.tools.registry import ToolContext, ToolRegistry
from ouroboros.tools.spreadsheets import _read_spreadsheet, resolve_spreadsheet_path


CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>'''

WORKBOOK = '''<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Продажи" sheetId="1" r:id="rId1"/></sheets>
</workbook>'''

RELS = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>'''

SHARED = '''<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="2" uniqueCount="2">
  <si><t>Товар</t></si><si><t>Нефть</t></si>
</sst>'''

SHEET = '''<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="A1:C3"/>
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>Объём</t></is></c><c r="C1" t="inlineStr"><is><t>Итого</t></is></c></row>
    <row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>12</v></c><c r="C2"><f>B2*10</f><v>120</v></c></row>
    <row r="3"><c r="A3" t="inlineStr"><is><t>Активен</t></is></c><c r="B3" t="b"><v>1</v></c></row>
  </sheetData>
</worksheet>'''


def make_xlsx(path, *, sheet_xml: str = SHEET, extra: dict[str, str] | None = None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("xl/workbook.xml", WORKBOOK)
        archive.writestr("xl/_rels/workbook.xml.rels", RELS)
        archive.writestr("xl/sharedStrings.xml", SHARED)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        for name, content in (extra or {}).items():
            archive.writestr(name, content)


def test_inspect_xlsx_reads_strings_booleans_numbers_and_formulas(tmp_path):
    path = tmp_path / "report.xlsx"
    make_xlsx(path)

    result = inspect_xlsx(path, max_rows=3, max_columns=3)

    assert result["selected_sheet"] == "Продажи"
    assert result["dimension"] == "A1:C3"
    assert result["columns"] == ["A", "B", "C"]
    assert result["rows"][0]["values"] == ["Товар", "Объём", "Итого"]
    assert result["rows"][1]["values"] == ["Нефть", 12, {"formula": "=B2*10", "value": 120}]
    assert result["rows"][2]["values"] == ["Активен", True, None]


def test_inspect_xlsx_applies_range_and_limits(tmp_path):
    path = tmp_path / "report.xlsx"
    make_xlsx(path)

    result = inspect_xlsx(path, sheet="Продажи", cell_range="B2:C99", max_rows=2, max_columns=1)

    assert result["range"] == "B2:B3"
    assert result["truncated"] is True
    assert result["rows"] == [{"row": 2, "values": [12]}, {"row": 3, "values": [True]}]


def test_inspect_xlsx_rejects_non_workbook_and_macro_payload(tmp_path):
    invalid = tmp_path / "invalid.xlsx"
    invalid.write_bytes(b"not a zip")
    with pytest.raises(SpreadsheetError, match="ZIP container"):
        inspect_xlsx(invalid)

    macro = tmp_path / "macro.xlsx"
    make_xlsx(macro, extra={"xl/vbaProject.bin": "payload"})
    with pytest.raises(SpreadsheetError, match="Macro-enabled"):
        inspect_xlsx(macro)


def test_range_validation():
    assert parse_range("AA10:AC12") == (10, 27, 12, 29)
    with pytest.raises(SpreadsheetError):
        parse_range("C5:A1")


def test_read_spreadsheet_tool_only_resolves_allowed_roots(tmp_path):
    repo = tmp_path / "repo"
    drive = tmp_path / "data"
    uploads = drive / "uploads"
    repo.mkdir()
    uploads.mkdir(parents=True)
    workbook = uploads / "report.xlsx"
    make_xlsx(workbook)
    outside = tmp_path / "outside.xlsx"
    make_xlsx(outside)
    ctx = ToolContext(repo_dir=repo, drive_root=drive)

    assert resolve_spreadsheet_path(ctx, str(workbook)) == workbook.resolve()
    payload = json.loads(_read_spreadsheet(ctx, str(workbook), max_rows=1, max_columns=2))
    assert payload["rows"][0]["values"] == ["Товар", "Объём"]
    assert "outside uploads" in _read_spreadsheet(ctx, str(outside))


def test_read_spreadsheet_is_a_core_registry_tool(tmp_path):
    registry = ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path)
    assert "read_spreadsheet" in registry.initial_tool_names()
    schema = registry.get_schema_by_name("read_spreadsheet")
    assert schema and schema["function"]["name"] == "read_spreadsheet"
