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


def _tag(element: etree._Element) -> str:
    return element.tag.rsplit("}", 1)[-1].upper() if isinstance(element.tag, str) else ""


def build_table_ids(root: etree._Element, prefix: str) -> dict[etree._Element, str]:
    return {
        node: f"{prefix}:table:{index}"
        for index, node in enumerate((node for node in root.iter() if _tag(node) == "TABLE"), 1)
    }


def _parent_table(element: etree._Element) -> etree._Element | None:
    return next((node for node in element.iterancestors() if _tag(node) == "TABLE"), None)


def _cell_text(element: etree._Element) -> str:
    """Exclude child tables, retaining surrounding text and child-table tails."""
    parts = [element.text or ""]
    for child in element:
        if _tag(child) != "TABLE" and isinstance(child.tag, str):
            parts.append(_cell_text(child))
        elif isinstance(child, etree._Entity):
            parts.append(child.text or "")
        parts.append(child.tail or "")
    return "".join(parts)


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


def _attribute_lookup(attributes: dict[str, Any]) -> dict[str, Any]:
    """Build one case-insensitive lookup while preserving the first raw value."""

    lookup: dict[str, Any] = {}
    for key, value in attributes.items():
        lookup.setdefault(key.lower(), value)
    return lookup


def parse_table(
    element: etree._Element,
    table_id: str,
    source_file_id: str,
    *,
    table_ids: dict[etree._Element, str] | None = None,
) -> TableData:
    """Parse a table without shifting empty cells or flattening spans."""

    cells: list[TableCell] = []
    occupied: set[tuple[int, int]] = set()
    header_rows: set[int] = set()
    row_count = 0
    column_count = 0
    if table_ids is None:
        table_ids = build_table_ids(element, table_id + ":nested")
        table_ids[element] = table_id
    row_elements = [
        row
        for row in element.iterdescendants()
        if _tag(row) == "TR" and _parent_table(row) is element
    ]

    for row_index, row in enumerate(row_elements):
        column_index = 0
        for cell in row:
            cell_tag = _tag(cell)
            if cell_tag not in {"TH", "TD", "TE", "TU"}:
                continue
            attributes_raw = _attributes(cell)
            attributes = _attribute_lookup(attributes_raw)
            row_span = _span(attributes.get("rowspan"))
            column_span = _span(attributes.get("colspan"))
            # The whole colspan must fit around active rowspans, not only its origin.
            while any(
                (row_index, column) in occupied
                for column in range(column_index, column_index + column_span)
            ):
                column_index += 1
            raw_text = _cell_text(cell)
            normalized_text = normalize_text(raw_text) or ""
            negated = parse_bool_attribute(attributes.get("anegated"))
            is_header = cell_tag in {"TH", "TE"}
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
                    unit_raw=attributes.get("aunitvalue") or attributes.get("aunit"),
                    currency=attributes.get("acurrency"),
                    concept_code=attributes.get("acode"),
                    context_ref=attributes.get("acontext"),
                    decimals_raw=attributes.get("adecimal"),
                    is_negated=negated,
                    attributes_raw=attributes_raw,
                    source_locator=SourceLocator(
                        source_file_id=source_file_id,
                        xpath=_xpath(cell),
                    ),
                    nested_table_ids=[
                        table_ids[node]
                        for node in cell.iterdescendants()
                        if _tag(node) == "TABLE" and _parent_table(node) is element
                    ],
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
    parent = _parent_table(element)
    parent_cell = next(
        (node for node in element.iterancestors() if _tag(node) in {"TD", "TH", "TE", "TU"}), None
    )
    return TableData(
        table_id=table_id,
        caption_raw=caption_raw,
        caption_normalized=normalize_text(caption_raw),
        row_count=row_count,
        column_count=column_count,
        header_row_indices=sorted(header_rows),
        cells=cells,
        attributes_raw=_attributes(element),
        parent_table_id=table_ids.get(parent),
        parent_cell_locator=(
            SourceLocator(source_file_id=source_file_id, xpath=_xpath(parent_cell))
            if parent is not None and parent_cell is not None
            else None
        ),
    )
