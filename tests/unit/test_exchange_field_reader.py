from __future__ import annotations

from disclosure_agent.domain.models import SourceLocator, TableCell, TableData
from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader


def _cell(
    row: int,
    column: int,
    text: str,
    *,
    row_span: int = 1,
    column_span: int = 1,
) -> TableCell:
    return TableCell(
        row_index=row,
        column_index=column,
        row_span=row_span,
        column_span=column_span,
        text_raw=text,
        text_normalized=text,
        source_locator=SourceLocator(
            source_file_id="source-1",
            xpath=f"/html/body/table/tr[{row + 1}]/td[{column + 1}]",
        ),
    )


def test_reader_reconstructs_rowspan_paths_and_provenance():
    table = TableData(
        table_id="table-1",
        row_count=4,
        column_count=4,
        cells=[
            _cell(0, 0, "1. 판매ㆍ공급계약 구분", column_span=2),
            _cell(0, 2, "기타 판매ㆍ공급계약", column_span=2),
            _cell(1, 0, "2. 계약내역", row_span=3),
            _cell(1, 1, "계약금액(원)"),
            _cell(1, 2, "97,000,000,000", column_span=2),
            _cell(2, 1, "최근매출액(원)"),
            _cell(2, 2, "1,806,000,000,000", column_span=2),
            _cell(3, 1, "매출액대비(%)"),
            _cell(3, 2, "5.37", column_span=2),
        ],
    )

    fields = ExchangeFieldReader().read_table(
        filing_id="filing-1",
        document_id="document-1",
        table=table,
    )

    assert [field.path for field in fields] == [
        ("1. 판매ㆍ공급계약 구분",),
        ("2. 계약내역", "계약금액(원)"),
        ("2. 계약내역", "최근매출액(원)"),
        ("2. 계약내역", "매출액대비(%)"),
    ]
    assert [field.value for field in fields] == [
        "기타 판매ㆍ공급계약",
        "97,000,000,000",
        "1,806,000,000,000",
        "5.37",
    ]

    contract_amount = fields[1]
    assert contract_amount.table_id == "table-1"
    assert contract_amount.row_index == 1
    assert contract_amount.value_column_index == 2
    assert contract_amount.value_locator is not None
    assert contract_amount.value_locator.xpath == "/html/body/table/tr[2]/td[3]"
    assert len(contract_amount.label_locators) == 2
    assert contract_amount.path_key == "2. 계약내역 > 계약금액(원)"


def test_reader_pairs_full_width_label_with_following_content_row():
    table = TableData(
        table_id="table-2",
        row_count=3,
        column_count=4,
        cells=[
            _cell(0, 0, "9. 기타 투자판단과 관련한 중요사항", column_span=4),
            _cell(
                1,
                0,
                "1. 상기 계약은 미국 판매법인이 수주한 후 당사로 재발주한 공급계약임.",
                column_span=4,
            ),
            _cell(2, 0, "※ 관련공시"),
            _cell(2, 1, "-", column_span=3),
        ],
    )

    fields = ExchangeFieldReader().read_table(
        filing_id="filing-1",
        document_id="document-1",
        table=table,
    )

    assert len(fields) == 2
    assert fields[0].path == ("9. 기타 투자판단과 관련한 중요사항",)
    assert fields[0].value.startswith("1. 상기 계약은")
    assert fields[0].raw_value.startswith("1. 상기 계약은")
    assert fields[1].path == ("※ 관련공시",)
    assert fields[1].value == "-"


def test_reader_ignores_single_full_width_non_label_content():
    table = TableData(
        table_id="table-3",
        row_count=1,
        column_count=4,
        cells=[_cell(0, 0, "단순 안내 문구", column_span=4)],
    )

    fields = ExchangeFieldReader().read_table(
        filing_id="filing-1",
        document_id="document-1",
        table=table,
    )

    assert fields == []
