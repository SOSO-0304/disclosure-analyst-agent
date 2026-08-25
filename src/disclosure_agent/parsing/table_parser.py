"""Table extraction utilities."""
from __future__ import annotations

from lxml import etree

from disclosure_agent.domain.models import DisclosureTable, TableCell, TableRow
from disclosure_agent.parsing.text_normalizer import normalize_text


def element_text(element: etree._Element) -> str | None:
    return normalize_text(" ".join(element.itertext()))


def _span(value: str | None) -> int:
    try:
        return max(1, int(value or 1))
    except ValueError:
        return 1


def parse_table(element: etree._Element, table_id: str, order: int | None = None,
                section_path: list[str] | None = None) -> DisclosureTable:
    rows: list[TableRow] = []
    for tr in element.xpath(".//TR | .//tr"):
        cells: list[TableCell] = []
        for cell in tr.xpath("./TH | ./TD | ./TE | ./TU | ./th | ./td"):
            cells.append(TableCell(
                text=element_text(cell),
                code=cell.get("ACODE") or cell.get("acode"),
                unit=cell.get("AUNIT") or cell.get("aunit"),
                unit_value=cell.get("AUNITVALUE") or cell.get("aunitvalue"),
                row_span=_span(cell.get("ROWSPAN") or cell.get("rowspan")),
                col_span=_span(cell.get("COLSPAN") or cell.get("colspan")),
                is_header=cell.tag.lower() in {"th", "te"},
            ))
        if cells:
            rows.append(TableRow(cells=cells))
    return DisclosureTable(
        table_id=table_id,
        rows=rows,
        section_path=section_path or [],
        order=order,
    )


def logical_html_rows(table: etree._Element) -> list[list[str]]:
    """Expand rowspan/colspan so hierarchical exchange labels retain parents."""
    active: dict[int, tuple[int, str]] = {}
    result: list[list[str]] = []
    for tr in table.xpath(".//tr"):
        row: dict[int, str] = {}
        carried: dict[int, tuple[int, str]] = {}
        for col, (remaining, text) in active.items():
            row[col] = text
            if remaining > 1:
                carried[col] = (remaining - 1, text)
        active = carried
        col = 0
        for cell in tr.xpath("./th | ./td"):
            while col in row:
                col += 1
            text = element_text(cell)
            if not text:
                continue
            rs, cs = _span(cell.get("rowspan")), _span(cell.get("colspan"))
            for offset in range(cs):
                idx = col + offset
                row[idx] = text
                if rs > 1:
                    active[idx] = (rs - 1, text)
            col += cs
        if row:
            vals: list[str] = []
            for idx in range(max(row) + 1):
                value = row.get(idx)
                if value and (not vals or vals[-1] != value):
                    vals.append(value)
            if vals:
                result.append(vals)
    return result
