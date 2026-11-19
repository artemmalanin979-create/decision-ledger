"""Zero-dependency OpenXML (.xlsx) reader, text extractor, and writer using Python stdlib."""

from __future__ import annotations

from html import escape
import io
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

MAX_XLSX_ENTRIES = 256
MAX_XLSX_XML_BYTES = 16 * 1024 * 1024
CELL_REF_RE = re.compile(r"^([A-Za-z]+)(\d+)$")
INT_FLOAT_XML_RE = re.compile(r"^([+-]?\d+)\.0+$")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _col_letters_to_index(letters: str) -> int:
    idx = 0
    for ch in letters.upper():
        if not ("A" <= ch <= "Z"):
            raise ValueError(f"Invalid column reference: {letters!r}")
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _col_index_to_letters(index: int) -> str:
    if index < 0:
        raise ValueError("Column index must be non-negative")
    n = index + 1
    chars: list[str] = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        chars.append(chr(ord("A") + rem))
    return "".join(reversed(chars))


def _safe_parse_xml(zf: zipfile.ZipFile, member: str) -> ET.Element:
    info = zf.getinfo(member)
    if info.file_size > MAX_XLSX_XML_BYTES:
        raise ValueError(f"XLSX XML entry {member!r} exceeds {MAX_XLSX_XML_BYTES} bytes limit")
    raw = zf.read(member)
    if len(raw) > MAX_XLSX_XML_BYTES:
        raise ValueError(f"XLSX XML entry {member!r} exceeds {MAX_XLSX_XML_BYTES} bytes limit")
    head_upper = raw[:4096].upper()
    if b"<!DOCTYPE" in head_upper or b"<!ENTITY" in head_upper:
        raise ValueError(f"Forbidden DTD/entity declaration in XLSX XML entry {member!r}")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError(f"Malformed XML in {member!r}: {error}") from error


def _open_zip(source: Path | bytes) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source, "r")
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError(f"Invalid .xlsx archive: {error}") from error
    if len(zf.infolist()) > MAX_XLSX_ENTRIES:
        zf.close()
        raise ValueError(f"XLSX archive has too many entries (>{MAX_XLSX_ENTRIES})")
    return zf


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    names = set(zf.namelist())
    target = "xl/sharedStrings.xml"
    if target not in names:
        return []
    root = _safe_parse_xml(zf, target)
    strings: list[str] = []
    for child in root:
        if _local_name(child.tag) != "si":
            continue
        parts: list[str] = []
        for elem in child.iter():
            if _local_name(elem.tag) == "t" and elem.text is not None:
                parts.append(elem.text)
        strings.append("".join(parts))
    return strings


def _discover_sheets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    names = set(zf.namelist())
    rels_map: dict[str, str] = {}
    rels_path = "xl/_rels/workbook.xml.rels"
    if rels_path in names:
        rels_root = _safe_parse_xml(zf, rels_path)
        for rel in rels_root:
            if _local_name(rel.tag) != "Relationship":
                continue
            rid = rel.attrib.get("Id", "")
            target = rel.attrib.get("Target", "")
            if rid and target:
                norm = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
                rels_map[rid] = norm

    sheets: list[tuple[str, str]] = []
    wb_path = "xl/workbook.xml"
    if wb_path in names:
        wb_root = _safe_parse_xml(zf, wb_path)
        for elem in wb_root.iter():
            if _local_name(elem.tag) != "sheet":
                continue
            name = elem.attrib.get("name", "").strip() or f"Sheet{len(sheets) + 1}"
            rid = ""
            for attr_k, attr_v in elem.attrib.items():
                if _local_name(attr_k) == "id":
                    rid = attr_v
                    break
            member = rels_map.get(rid, f"xl/worksheets/sheet{len(sheets) + 1}.xml")
            if member in names:
                sheets.append((name, member))

    if not sheets:
        ws_members = sorted(n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml"))
        for idx, member in enumerate(ws_members, 1):
            sheets.append((f"Sheet{idx}", member))

    if not sheets:
        raise ValueError("XLSX workbook contains no worksheets")
    return sheets


def _select_sheet(sheets: list[tuple[str, str]], selector: str | int | None) -> tuple[str, str]:
    if selector is None or selector == "":
        return sheets[0]
    if isinstance(selector, int):
        if 0 <= selector < len(sheets):
            return sheets[selector]
        raise ValueError(f"Sheet index {selector} out of range (0..{len(sheets) - 1})")
    sel = str(selector).strip()
    for name, member in sheets:
        if name == sel:
            return name, member
    for name, member in sheets:
        if name.lower() == sel.lower():
            return name, member
    if sel.isdigit():
        idx = int(sel)
        if 0 <= idx < len(sheets):
            return sheets[idx]
    available = ", ".join(name for name, _ in sheets)
    raise ValueError(f"Unknown worksheet {sel!r}; available sheets: {available}")


def _parse_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        parts: list[str] = []
        for sub in cell.iter():
            if _local_name(sub.tag) == "t" and sub.text is not None:
                parts.append(sub.text)
        return "".join(parts)

    val_text: str | None = None
    for child in cell:
        if _local_name(child.tag) == "v":
            val_text = child.text
            break
    if val_text is None:
        return ""

    if cell_type == "s":
        try:
            idx = int(val_text.strip())
            return shared_strings[idx]
        except (ValueError, IndexError) as error:
            raise ValueError(f"Invalid sharedString index {val_text!r} in XLSX cell") from error
    if cell_type == "b":
        return "TRUE" if val_text.strip() in {"1", "true", "TRUE"} else "FALSE"
    if cell_type in {"str", "e"}:
        return val_text

    # Numeric or untyped value: normalize cached integer floats like "120.0" -> "120" when lossless.
    cleaned = val_text.strip()
    m_int = INT_FLOAT_XML_RE.match(cleaned)
    if m_int:
        return m_int.group(1)
    return cleaned


def _parse_sheet_rows(zf: zipfile.ZipFile, member: str, shared_strings: list[str]) -> list[tuple[int, list[str]]]:
    root = _safe_parse_xml(zf, member)
    parsed_rows: list[tuple[int, dict[int, str]]] = []
    max_col = -1

    for elem in root.iter():
        if _local_name(elem.tag) != "sheetData":
            continue
        for row_idx, row_el in enumerate(elem, 1):
            if _local_name(row_el.tag) != "row":
                continue
            excel_row_num = int(row_el.attrib.get("r", row_idx) or row_idx)
            cells: dict[int, str] = {}
            next_col = 0
            for cell_el in row_el:
                if _local_name(cell_el.tag) != "c":
                    continue
                ref = cell_el.attrib.get("r", "").strip()
                m_ref = CELL_REF_RE.match(ref)
                if m_ref:
                    col_idx = _col_letters_to_index(m_ref.group(1))
                    next_col = col_idx + 1
                else:
                    col_idx = next_col
                    next_col += 1
                val = _parse_cell_value(cell_el, shared_strings)
                cells[col_idx] = val
                if val != "" and col_idx > max_col:
                    max_col = col_idx
            parsed_rows.append((excel_row_num, cells))
        break

    if max_col < 0:
        return []

    width = max_col + 1
    dense: list[tuple[int, list[str]]] = []
    for excel_row_num, cells in parsed_rows:
        row_vals = [cells.get(c, "") for c in range(width)]
        if any(v.strip() != "" for v in row_vals):
            dense.append((excel_row_num, row_vals))
    return dense


def list_xlsx_sheets(source: Path | bytes) -> list[str]:
    """Return ordered worksheet names from an .xlsx workbook."""
    with _open_zip(source) as zf:
        return [name for name, _ in _discover_sheets(zf)]


def read_xlsx_rows(
    source: Path | bytes,
    *,
    sheet: str | int | None = None,
) -> tuple[str, list[tuple[int, list[str]]]]:
    """Read non-empty rows as (sheet_name, [(excel_row_number, [cell_str, ...]), ...])."""
    with _open_zip(source) as zf:
        sheets = _discover_sheets(zf)
        sheet_name, member = _select_sheet(sheets, sheet)
        shared = _read_shared_strings(zf)
        rows = _parse_sheet_rows(zf, member, shared)
        return sheet_name, rows


def read_xlsx_records(
    source: Path | bytes,
    *,
    sheet: str | int | None = None,
) -> dict:
    """Read a worksheet with a header row into structured JSON-ready records."""
    all_sheets = list_xlsx_sheets(source)
    sheet_name, rows = read_xlsx_rows(source, sheet=sheet)
    if not rows:
        return {"sheet": sheet_name, "sheets": all_sheets, "headers": [], "rows": 0, "records": []}
    _, raw_header = rows[0]
    headers = [cell.strip() for cell in raw_header]
    while headers and headers[-1] == "":
        headers.pop()
    if not headers:
        raise ValueError(f"Worksheet {sheet_name!r} header row is empty")
    records = []
    for line_num, vals in rows[1:]:
        trimmed = [v.strip() for v in vals[: len(headers)]]
        if not any(trimmed):
            continue
        rec = dict(zip(headers, trimmed))
        rec["_row"] = line_num
        records.append(rec)
    return {
        "sheet": sheet_name,
        "sheets": all_sheets,
        "headers": headers,
        "rows": len(records),
        "records": records,
    }


def render_xlsx_markdown(report: dict) -> str:
    """Render structured worksheet records as a GitHub-flavored Markdown table."""
    headers = report.get("headers") or []
    lines = [f"### Лист `{report['sheet']}` (всего листов: {', '.join(report.get('sheets') or [])})", ""]
    if not headers:
        lines.append("_Лист пуст._")
        return "\n".join(lines) + "\n"
    lines.append("| " + " | ".join(h.replace("|", "\\|") for h in headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for rec in report.get("records") or []:
        cells = [str(rec.get(h, "")).replace("\n", " ").replace("|", "\\|") for h in headers]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def extract_xlsx_text(raw: bytes) -> str:
    """Extract all worksheet names and non-empty cell strings for security/PII scanning."""
    with _open_zip(raw) as zf:
        sheets = _discover_sheets(zf)
        shared = _read_shared_strings(zf)
        chunks: list[str] = []
        for name, member in sheets:
            chunks.append(f"[sheet:{name}]")
            for _, row_vals in _parse_sheet_rows(zf, member, shared):
                line = "\t".join(v for v in row_vals if v != "")
                if line:
                    chunks.append(line)
        return "\n".join(chunks)


def write_xlsx_workbook(path: Path, sheets: dict[str, list[list[object]]]) -> Path:
    """Write a clean, standards-compliant OpenXML .xlsx workbook using Python stdlib only."""
    if not sheets:
        raise ValueError("Workbook requires at least one sheet")

    shared_index: dict[str, int] = {}
    shared_list: list[str] = []

    def intern_str(text: str) -> int:
        if text not in shared_index:
            shared_index[text] = len(shared_list)
            shared_list.append(text)
        return shared_index[text]

    sheet_entries: list[tuple[str, str]] = []
    worksheet_xmls: list[bytes] = []

    for idx, (sheet_name, matrix) in enumerate(sheets.items(), 1):
        clean_name = (sheet_name.strip() or f"Sheet{idx}")[:31]
        sheet_entries.append((clean_name, f"worksheets/sheet{idx}.xml"))
        row_xml_parts: list[str] = []
        for r_idx, row in enumerate(matrix, 1):
            cell_parts: list[str] = []
            for c_idx, val in enumerate(row):
                if val is None or val == "":
                    continue
                ref = f"{_col_index_to_letters(c_idx)}{r_idx}"
                if isinstance(val, bool):
                    b_val = "1" if val else "0"
                    cell_parts.append(f'<c r="{ref}" t="b"><v>{b_val}</v></c>')
                elif isinstance(val, (int, float)):
                    cell_parts.append(f'<c r="{ref}"><v>{val}</v></c>')
                else:
                    s_idx = intern_str(str(val))
                    cell_parts.append(f'<c r="{ref}" t="s"><v>{s_idx}</v></c>')
            row_xml_parts.append(f'<row r="{r_idx}">{"".join(cell_parts)}</row>')
        ws_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(row_xml_parts)}</sheetData>'
            "</worksheet>"
        ).encode("utf-8")
        worksheet_xmls.append(ws_xml)

    content_types_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheet_entries) + 1)
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f"{content_types_overrides}"
        "</Types>"
    ).encode("utf-8")

    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    ).encode("utf-8")

    wb_sheets_xml = "".join(
        f'<sheet name="{escape(name, quote=True)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (name, _) in enumerate(sheet_entries, 1)
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{wb_sheets_xml}</sheets>"
        "</workbook>"
    ).encode("utf-8")

    wb_rels_parts = [
        f'<Relationship Id="rId{i}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="{target}"/>'
        for i, (_, target) in enumerate(sheet_entries, 1)
    ]
    next_rid = len(sheet_entries) + 1
    wb_rels_parts.append(
        f'<Relationship Id="rId{next_rid}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        'Target="sharedStrings.xml"/>'
    )
    wb_rels_parts.append(
        f'<Relationship Id="rId{next_rid + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    wb_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(wb_rels_parts)}'
        "</Relationships>"
    ).encode("utf-8")

    si_items = "".join(f'<si><t xml:space="preserve">{escape(s)}</t></si>' for s in shared_list)
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(shared_list)}" uniqueCount="{len(shared_list)}">'
        f"{si_items}"
        "</sst>"
    ).encode("utf-8")

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        "</styleSheet>"
    ).encode("utf-8")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", root_rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels_xml)
        zf.writestr("xl/sharedStrings.xml", shared_xml)
        zf.writestr("xl/styles.xml", styles_xml)
        for idx, ws_xml in enumerate(worksheet_xmls, 1):
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", ws_xml)
    return path
