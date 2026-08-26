"""Loss-minimising table extraction with stable logical coordinates."""

from __future__ import annotations

from typing import Any

from lxml import etree

from disclosure_agent.domain.models import SourceLocator, TableCell, TableData
from disclosure_agent.parsing.text_normalizer import (
    normalize_text,
    parse_bool_attribute,
    parse_decimal,
)


def raw_element_text(element: etree._Element) -> str:
    """Return source text without whitespace normalization."""

    return "".join(element.itertext())


def _attribute(element: etree._Element, name: str) -> str | None:
    wanted = name.lower()
    for key, value in element.attrib.items():
        if key.lower() == wanted:
            return value
    return None


def _span(value: str | None) -> int:
    try:
        return max(1, int(value or 1))
    except ValueError:
        return 1


def _xpath(element: etree._Element) -> str | None:
    try:
        return element.getroottree().getpath(element)
    except (AttributeError, ValueError):
        return None


def _attributes(element: etree._Element) -> dict[str, Any]:
    return {str(key): value for key, value in element.attrib.items()}


def parse_table(
    element: etree._Element,
    table_id: str,
    source_file_id: str,
) -> TableData:
    """Parse a table without shifting empty cells or flattening spans."""

    cells: list[TableCell] = []
    occupied: set[tuple[int, int]] = set()
    header_rows: set[int] = set()
    row_count = 0
    column_count = 0
    row_elements = element.xpath(".//TR | .//tr")

    for row_index, row in enumerate(row_elements):
        column_index = 0
        for cell in row.xpath("./TH | ./TD | ./TE | ./TU | ./th | ./td"):
            while (row_index, column_index) in occupied:
                column_index += 1

            row_span = _span(_attribute(cell, "rowspan"))
            column_span = _span(_attribute(cell, "colspan"))
            raw_text = raw_element_text(cell)
            normalized_text = normalize_text(raw_text) or ""
            negated = parse_bool_attribute(_attribute(cell, "anegated"))
            is_header = str(cell.tag).lower() in {"th", "te"}
            if is_header:
                header_rows.add(row_index)

            cells.append(
                TableCell(
                    row_index=row_index,
                    column_index=column_index,
                    row_span=row_span,
                    column_span=column_span,
                    is_header=is_header,
                    text_raw=raw_text,
                    text_normalized=normalized_text,
                    numeric_value=parse_decimal(raw_text, negated=negated),
                    unit_raw=_attribute(cell, "aunitvalue") or _attribute(cell, "aunit"),
                    currency=_attribute(cell, "acurrency"),
                    concept_code=_attribute(cell, "acode"),
                    context_ref=_attribute(cell, "acontext"),
                    decimals_raw=_attribute(cell, "adecimal"),
                    is_negated=negated,
                    attributes_raw=_attributes(cell),
                    source_locator=SourceLocator(
                        source_file_id=source_file_id,
                        xpath=_xpath(cell),
                    ),
                )
            )

            for occupied_row in range(row_index, row_index + row_span):
                for occupied_column in range(
                    column_index,
                    column_index + column_span,
                ):
                    occupied.add((occupied_row, occupied_column))
            column_index += column_span
            row_count = max(row_count, row_index + row_span)
            column_count = max(column_count, column_index)

        row_count = max(row_count, row_index + 1)

    caption_nodes = element.xpath("./CAPTION | ./caption")
    caption_raw = raw_element_text(caption_nodes[0]) if caption_nodes else None
    return TableData(
        table_id=table_id,
        caption_raw=caption_raw,
        caption_normalized=normalize_text(caption_raw),
        row_count=row_count,
        column_count=column_count,
        header_row_indices=sorted(header_rows),
        cells=cells,
        attributes_raw=_attributes(element),
    )
