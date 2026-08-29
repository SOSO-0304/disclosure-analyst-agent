"""Read semantic key/value fields from canonical Exchange tables.

This adapter deliberately sits *after* canonical parsing.  It never mutates or
replaces :class:`TableData`; instead it derives convenient semantic fields while
retaining locators back to the exact canonical cells that supplied labels and
values.

The reader is conservative and tailored to the XForms-style Exchange filings in
the supplied corpus:

* rowspan/colspan are expanded into a logical grid;
* inherited rowspan labels become parent path components;
* the right-most distinct cell in a row is treated as the value cell;
* a full-width label row followed by a full-width content row is represented as
  one field (used by e.g. ``9. 기타 투자판단과 관련한 중요사항``);
* blank rows and rows without a distinct label/value boundary are ignored.

No business-specific aliases (``계약금액`` -> ``contract_amount``) belong here.
Those mappings are the responsibility of event extractors.
"""

from __future__ import annotations

from dataclasses import dataclass

from disclosure_agent.domain.models import (
    BlockType,
    CanonicalDocument,
    FilingPackage,
    SourceLocator,
    TableCell,
    TableData,
)


@dataclass(frozen=True, slots=True)
class SemanticField:
    """One derived Exchange field with canonical-cell provenance."""

    filing_id: str
    document_id: str
    table_id: str
    path: tuple[str, ...]
    value: str
    raw_value: str
    row_index: int
    value_column_index: int
    value_locator: SourceLocator | None
    label_locators: tuple[SourceLocator | None, ...]

    @property
    def path_key(self) -> str:
        """Return a stable human-readable key for diagnostics and indexing."""

        return " > ".join(self.path)


class ExchangeFieldReader:
    """Derive semantic fields from loss-minimising canonical Exchange tables."""

    def read_package(self, package: FilingPackage) -> list[SemanticField]:
        fields: list[SemanticField] = []
        for document in package.documents:
            fields.extend(self.read_document(package.filing_id, document))
        return fields

    def read_document(
        self,
        filing_id: str,
        document: CanonicalDocument,
    ) -> list[SemanticField]:
        fields: list[SemanticField] = []
        for block in document.blocks:
            if block.block_type is not BlockType.TABLE or block.table is None:
                continue
            fields.extend(
                self.read_table(
                    filing_id=filing_id,
                    document_id=document.document_id,
                    table=block.table,
                )
            )
        return fields

    def read_table(
        self,
        *,
        filing_id: str,
        document_id: str,
        table: TableData,
    ) -> list[SemanticField]:
        if table.row_count == 0 or table.column_count == 0 or not table.cells:
            return []

        grid = _logical_grid(table)
        fields: list[SemanticField] = []
        pending_full_width_label: TableCell | None = None

        for row_index in range(table.row_count):
            row_cells = _distinct_row_cells(grid[row_index])
            if not row_cells:
                continue

            # XForms sometimes emits a section label across the whole row and its
            # free-text value across the following whole row.
            if len(row_cells) == 1 and row_cells[0].column_span >= table.column_count:
                only = row_cells[0]
                text = only.text_normalized.strip()
                if not text:
                    continue

                if pending_full_width_label is not None:
                    label = pending_full_width_label.text_normalized.strip()
                    if label and _looks_like_label(label):
                        fields.append(
                            _field(
                                filing_id=filing_id,
                                document_id=document_id,
                                table_id=table.table_id,
                                row_index=row_index,
                                labels=[pending_full_width_label],
                                value_cell=only,
                            )
                        )
                        pending_full_width_label = None
                        continue

                pending_full_width_label = only if _looks_like_label(text) else None
                continue

            pending_full_width_label = None

            # Spans make the same anchor cell appear in several logical columns;
            # _distinct_row_cells removes those duplicates while preserving order.
            if len(row_cells) < 2:
                continue

            value_cell = row_cells[-1]
            labels = row_cells[:-1]
            if not value_cell.text_normalized.strip():
                continue

            label_texts = [cell.text_normalized.strip() for cell in labels]
            if not any(label_texts):
                continue

            fields.append(
                _field(
                    filing_id=filing_id,
                    document_id=document_id,
                    table_id=table.table_id,
                    row_index=row_index,
                    labels=labels,
                    value_cell=value_cell,
                )
            )

        return fields


def _field(
    *,
    filing_id: str,
    document_id: str,
    table_id: str,
    row_index: int,
    labels: list[TableCell],
    value_cell: TableCell,
) -> SemanticField:
    path = tuple(text for cell in labels if (text := cell.text_normalized.strip()))
    return SemanticField(
        filing_id=filing_id,
        document_id=document_id,
        table_id=table_id,
        path=path,
        value=value_cell.text_normalized.strip(),
        raw_value=value_cell.text_raw,
        row_index=row_index,
        value_column_index=value_cell.column_index,
        value_locator=value_cell.source_locator,
        label_locators=tuple(
            cell.source_locator
            for cell in labels
            if cell.text_normalized.strip()
        ),
    )


def _logical_grid(table: TableData) -> list[list[TableCell | None]]:
    """Expand canonical spans without creating synthetic cells."""

    grid: list[list[TableCell | None]] = [
        [None for _ in range(table.column_count)] for _ in range(table.row_count)
    ]
    for cell in table.cells:
        for row in range(cell.row_index, cell.row_index + cell.row_span):
            for column in range(cell.column_index, cell.column_index + cell.column_span):
                grid[row][column] = cell
    return grid


def _distinct_row_cells(row: list[TableCell | None]) -> list[TableCell]:
    cells: list[TableCell] = []
    seen: set[tuple[int, int]] = set()
    for cell in row:
        if cell is None:
            continue
        key = (cell.row_index, cell.column_index)
        if key in seen:
            continue
        seen.add(key)
        cells.append(cell)
    return cells


def _looks_like_label(text: str) -> bool:
    """Recognise numbered/annotated XForms labels without domain aliases."""

    stripped = text.strip()
    if not stripped:
        return False
    return stripped[0].isdigit() or stripped.startswith("-") or stripped.startswith("※")
