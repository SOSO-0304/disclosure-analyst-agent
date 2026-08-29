"""Read before/after correction rows from canonical Exchange tables."""

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
class CorrectionRow:
    """One Exchange correction-table row with source provenance."""

    filing_id: str
    document_id: str
    table_id: str
    item: str
    before: str
    after: str
    row_index: int
    item_locator: SourceLocator | None
    before_locator: SourceLocator | None
    after_locator: SourceLocator | None


class ExchangeCorrectionReader:
    """Extract explicit ``정정항목 / 정정전 / 정정후`` rows without flattening them."""

    def read_package(self, package: FilingPackage) -> list[CorrectionRow]:
        rows: list[CorrectionRow] = []
        for document in package.documents:
            rows.extend(self.read_document(package.filing_id, document))
        return rows

    def read_document(
        self,
        filing_id: str,
        document: CanonicalDocument,
    ) -> list[CorrectionRow]:
        rows: list[CorrectionRow] = []
        for block in document.blocks:
            if block.block_type is not BlockType.TABLE or block.table is None:
                continue
            rows.extend(
                self.read_table(
                    filing_id=filing_id,
                    document_id=document.document_id,
                    table=block.table,
                )
            )
        return rows

    def read_table(
        self,
        *,
        filing_id: str,
        document_id: str,
        table: TableData,
    ) -> list[CorrectionRow]:
        if table.row_count < 2 or table.column_count < 3 or not table.cells:
            return []

        grid = _logical_grid(table)
        header = _find_header(grid)
        if header is None:
            return []

        header_row, item_column, before_column, after_column = header
        rows: list[CorrectionRow] = []
        for row_index in range(header_row + 1, table.row_count):
            item_cell = grid[row_index][item_column]
            before_cell = grid[row_index][before_column]
            after_cell = grid[row_index][after_column]
            if item_cell is None or before_cell is None or after_cell is None:
                continue

            item = item_cell.text_normalized.strip()
            before = before_cell.text_normalized.strip()
            after = after_cell.text_normalized.strip()
            if not item and not before and not after:
                continue

            # A repeated header may appear inside a long correction table.
            if (
                _normalise_header(item) == "정정항목"
                and _normalise_header(before) == "정정전"
                and _normalise_header(after) == "정정후"
            ):
                continue

            rows.append(
                CorrectionRow(
                    filing_id=filing_id,
                    document_id=document_id,
                    table_id=table.table_id,
                    item=item,
                    before=before,
                    after=after,
                    row_index=row_index,
                    item_locator=item_cell.source_locator,
                    before_locator=before_cell.source_locator,
                    after_locator=after_cell.source_locator,
                )
            )
        return rows


def _logical_grid(table: TableData) -> list[list[TableCell | None]]:
    grid: list[list[TableCell | None]] = [
        [None for _ in range(table.column_count)] for _ in range(table.row_count)
    ]
    for cell in table.cells:
        for row in range(cell.row_index, min(table.row_count, cell.row_index + cell.row_span)):
            for column in range(
                cell.column_index,
                min(table.column_count, cell.column_index + cell.column_span),
            ):
                grid[row][column] = cell
    return grid


def _normalise_header(text: str) -> str:
    return "".join(text.split()).replace("ㆍ", "").replace("·", "")


def _find_header(
    grid: list[list[TableCell | None]],
) -> tuple[int, int, int, int] | None:
    for row_index, row in enumerate(grid):
        labels = [
            _normalise_header(cell.text_normalized) if cell is not None else "" for cell in row
        ]
        item_columns = [i for i, label in enumerate(labels) if label == "정정항목"]
        before_columns = [i for i, label in enumerate(labels) if label == "정정전"]
        after_columns = [i for i, label in enumerate(labels) if label == "정정후"]
        if item_columns and before_columns and after_columns:
            return row_index, item_columns[0], before_columns[0], after_columns[0]
    return None
