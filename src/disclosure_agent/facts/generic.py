"""Conservative atomic fact extraction from loss-minimising canonical tables.

This layer deliberately does not assign business semantics such as "revenue" or
"facility investment".  It only preserves a value together with the labels,
headers, section path, and source locator that make the value retrievable and
auditable.  Higher-level typed event extractors may later interpret these facts.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from disclosure_agent.domain.models import FilingPackage, TableCell, TableData


class FactKind(StrEnum):
    """Why a canonical cell was promoted to the generic fact layer."""

    CONCEPT = "concept"
    NUMERIC = "numeric"
    LABEL_VALUE = "label_value"


@dataclass(frozen=True, slots=True)
class GenericFact:
    """One atomic value with enough canonical context to retrieve and cite it."""

    fact_id: str
    fact_kind: FactKind
    filing_id: str
    document_id: str
    section_id: str | None
    block_id: str
    table_id: str
    row_index: int
    column_index: int
    label_text: str | None
    header_text: str | None
    path_text: str
    value_text: str
    raw_value: str
    numeric_value: Decimal | None
    unit_raw: str | None
    currency: str | None
    concept_code: str | None
    context_ref: str | None
    source_locator: dict[str, Any] | None


def extract_generic_facts(package: FilingPackage) -> tuple[GenericFact, ...]:
    """Extract generic facts from every table in one canonical filing package."""

    facts: list[GenericFact] = []
    for document in package.documents:
        sections = {section.section_id: section for section in document.sections}
        for block in document.blocks:
            if block.table is None:
                continue
            section_path = _section_path(block.section_id, sections)
            facts.extend(
                extract_table_facts(
                    block.table,
                    filing_id=package.filing_id,
                    document_id=document.document_id,
                    section_id=block.section_id,
                    block_id=block.block_id,
                    section_path=section_path,
                )
            )
    return tuple(facts)


def extract_table_facts(
    table: TableData,
    *,
    filing_id: str,
    document_id: str,
    section_id: str | None,
    block_id: str,
    section_path: Sequence[str] = (),
) -> tuple[GenericFact, ...]:
    """Promote retrievable value cells while retaining their table context.

    Promotion is intentionally conservative:
    - a cell with a DART concept code is always retained;
    - a cell with a parsed numeric value is retained;
    - a non-numeric value is retained only for compact label/value rows where it
      is the right-most visible cell and has a non-header label to its left.

    The third rule captures exchange-disclosure rows such as counterparty/date
    fields without exploding every narrative table cell into a database row.
    """

    facts: list[GenericFact] = []
    for cell in sorted(table.cells, key=lambda item: (item.row_index, item.column_index)):
        value_text = cell.text_normalized.strip()
        if not value_text:
            continue
        if _is_header_cell(cell, table):
            continue

        visible_row = _visible_row_cells(table, cell.row_index)
        labels = _row_labels(cell, visible_row, table)
        fact_kind = _fact_kind(cell, visible_row, labels, table)
        if fact_kind is None:
            continue

        header_parts = _header_labels(cell, table)
        label_text = _join_unique(labels, " > ") or None
        header_text = _join_unique(header_parts, " > ") or None
        path_parts = [
            *section_path,
            table.caption_normalized or table.caption_raw or "",
            header_text or "",
            label_text or "",
        ]
        path_text = " | ".join(_unique_nonempty(path_parts))

        facts.append(
            GenericFact(
                fact_id=_fact_id(table.table_id, cell),
                fact_kind=fact_kind,
                filing_id=filing_id,
                document_id=document_id,
                section_id=section_id,
                block_id=block_id,
                table_id=table.table_id,
                row_index=cell.row_index,
                column_index=cell.column_index,
                label_text=label_text,
                header_text=header_text,
                path_text=path_text,
                value_text=value_text,
                raw_value=cell.text_raw,
                numeric_value=cell.numeric_value,
                unit_raw=cell.unit_raw,
                currency=cell.currency,
                concept_code=cell.concept_code,
                context_ref=cell.context_ref,
                source_locator=_json(cell.source_locator),
            )
        )
    return tuple(facts)


def _fact_kind(
    cell: TableCell,
    visible_row: Sequence[TableCell],
    labels: Sequence[str],
    table: TableData,
) -> FactKind | None:
    if cell.concept_code:
        return FactKind.CONCEPT
    if cell.numeric_value is not None:
        return FactKind.NUMERIC

    non_header_values = [
        item
        for item in visible_row
        if item.text_normalized.strip() and not _is_header_cell(item, table)
    ]
    if not labels or len(non_header_values) > 4:
        return None
    rightmost = max(non_header_values, key=lambda item: item.column_index)
    if rightmost is cell:
        return FactKind.LABEL_VALUE
    return None


def _visible_row_cells(table: TableData, row_index: int) -> tuple[TableCell, ...]:
    """Return anchor cells whose row spans cover the requested logical row."""

    cells = [
        cell for cell in table.cells if cell.row_index <= row_index < cell.row_index + cell.row_span
    ]
    return tuple(sorted(cells, key=lambda item: item.column_index))


def _row_labels(
    value_cell: TableCell,
    visible_row: Sequence[TableCell],
    table: TableData,
) -> tuple[str, ...]:
    labels: list[str] = []
    for cell in visible_row:
        if cell is value_cell or cell.column_index >= value_cell.column_index:
            continue
        text = cell.text_normalized.strip()
        if not text or _is_header_cell(cell, table):
            continue
        labels.append(text)
    # Long rows are usually matrix-like data.  Keeping only the nearest three
    # labels avoids turning the whole row into an accidental semantic claim.
    return tuple(_unique_nonempty(labels[-3:]))


def _header_labels(value_cell: TableCell, table: TableData) -> tuple[str, ...]:
    labels: list[str] = []
    for cell in sorted(table.cells, key=lambda item: (item.row_index, item.column_index)):
        if cell.row_index >= value_cell.row_index:
            continue
        if not _is_header_cell(cell, table):
            continue
        if not _covers_column(cell, value_cell.column_index):
            continue
        text = cell.text_normalized.strip()
        if text:
            labels.append(text)
    return tuple(_unique_nonempty(labels))


def _is_header_cell(cell: TableCell, table: TableData) -> bool:
    return cell.is_header or cell.row_index in table.header_row_indices


def _covers_column(cell: TableCell, column_index: int) -> bool:
    return cell.column_index <= column_index < cell.column_index + cell.column_span


def _fact_id(table_id: str, cell: TableCell) -> str:
    identity = f"{table_id}\x1f{cell.row_index}\x1f{cell.column_index}".encode()
    digest = hashlib.sha256(identity).hexdigest()[:32]
    return f"fact:{digest}"


def _section_path(section_id: str | None, sections: dict[str, Any]) -> tuple[str, ...]:
    if section_id is None:
        return ()

    titles: list[str] = []
    visited: set[str] = set()
    current_id: str | None = section_id
    while current_id is not None and current_id not in visited:
        visited.add(current_id)
        section = sections.get(current_id)
        if section is None:
            break
        title = (section.title_normalized or section.title_raw or "").strip()
        if title:
            titles.append(title)
        current_id = section.parent_section_id
    titles.reverse()
    return tuple(_unique_nonempty(titles))


def _join_unique(values: Iterable[str], separator: str) -> str:
    return separator.join(_unique_nonempty(values))


def _unique_nonempty(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _json(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value
