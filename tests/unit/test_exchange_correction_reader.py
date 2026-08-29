from __future__ import annotations

from disclosure_agent.domain.models import SourceLocator, TableCell, TableData
from disclosure_agent.extractors.exchange_corrections import ExchangeCorrectionReader


def _cell(row: int, column: int, text: str) -> TableCell:
    return TableCell(
        row_index=row,
        column_index=column,
        text_raw=text,
        text_normalized=text,
        source_locator=SourceLocator(
            source_file_id="source:1",
            xpath=f"//tr[{row + 1}]/td[{column + 1}]",
        ),
    )


def test_reads_before_after_columns_with_provenance() -> None:
    table = TableData(
        table_id="table:1",
        row_count=3,
        column_count=3,
        cells=[
            _cell(0, 0, "정정항목"),
            _cell(0, 1, "정정전"),
            _cell(0, 2, "정정후"),
            _cell(1, 0, "3. 계약상대"),
            _cell(1, 1, "A사"),
            _cell(1, 2, "B사"),
            _cell(2, 0, "5. 계약기간 종료일"),
            _cell(2, 1, "2025-12-31"),
            _cell(2, 2, "2026-12-31"),
        ],
    )

    rows = ExchangeCorrectionReader().read_table(
        filing_id="exchange_1",
        document_id="document:1",
        table=table,
    )

    assert [(row.item, row.before, row.after) for row in rows] == [
        ("3. 계약상대", "A사", "B사"),
        ("5. 계약기간 종료일", "2025-12-31", "2026-12-31"),
    ]
    assert rows[0].table_id == "table:1"
    assert rows[0].after_locator is not None
    assert rows[0].after_locator.xpath == "//tr[2]/td[3]"


def test_ignores_non_correction_table() -> None:
    table = TableData(
        table_id="table:1",
        row_count=2,
        column_count=3,
        cells=[
            _cell(0, 0, "계약내역"),
            _cell(0, 1, "계약금액"),
            _cell(0, 2, "100"),
            _cell(1, 0, "계약기간"),
            _cell(1, 1, "종료일"),
            _cell(1, 2, "2026-12-31"),
        ],
    )

    rows = ExchangeCorrectionReader().read_table(
        filing_id="exchange_1",
        document_id="document:1",
        table=table,
    )

    assert rows == []
