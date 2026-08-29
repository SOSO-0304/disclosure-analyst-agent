from __future__ import annotations

from decimal import Decimal

from disclosure_agent.domain.models import SourceLocator, TableCell, TableData
from disclosure_agent.facts.generic import FactKind, extract_table_facts


def test_numeric_fact_keeps_row_header_and_source_context() -> None:
    table = TableData(
        table_id="table:revenue",
        row_count=2,
        column_count=2,
        header_row_indices=[0],
        caption_raw="연결 손익",
        caption_normalized="연결 손익",
        cells=[
            TableCell(
                row_index=0,
                column_index=0,
                is_header=True,
                text_raw="구분",
                text_normalized="구분",
            ),
            TableCell(
                row_index=0,
                column_index=1,
                is_header=True,
                text_raw="2025년",
                text_normalized="2025년",
            ),
            TableCell(
                row_index=1,
                column_index=0,
                text_raw="매출액",
                text_normalized="매출액",
            ),
            TableCell(
                row_index=1,
                column_index=1,
                text_raw="1,000",
                text_normalized="1,000",
                numeric_value=Decimal("1000"),
                unit_raw="백만원",
                source_locator=SourceLocator(
                    source_file_id="source:1",
                    xpath="/DOCUMENT/TABLE[1]/TR[2]/TD[2]",
                ),
            ),
        ],
    )

    facts = extract_table_facts(
        table,
        filing_id="filing:1",
        document_id="document:1",
        section_id="section:1",
        block_id="block:1",
        section_path=("III. 재무에 관한 사항",),
    )

    assert len(facts) == 1
    fact = facts[0]
    assert fact.fact_kind is FactKind.NUMERIC
    assert fact.label_text == "매출액"
    assert fact.header_text == "2025년"
    assert fact.path_text == "III. 재무에 관한 사항 | 연결 손익 | 2025년 | 매출액"
    assert fact.numeric_value == Decimal("1000")
    assert fact.unit_raw == "백만원"
    assert fact.source_locator == {
        "source_file_id": "source:1",
        "xpath": "/DOCUMENT/TABLE[1]/TR[2]/TD[2]",
        "page_number": None,
        "element_index": None,
        "char_start": None,
        "char_end": None,
    }


def test_compact_label_value_row_promotes_rightmost_text_value() -> None:
    table = TableData(
        table_id="table:contract",
        row_count=1,
        column_count=2,
        cells=[
            TableCell(
                row_index=0,
                column_index=0,
                text_raw="계약상대",
                text_normalized="계약상대",
            ),
            TableCell(
                row_index=0,
                column_index=1,
                text_raw="테스트 주식회사",
                text_normalized="테스트 주식회사",
            ),
        ],
    )

    facts = extract_table_facts(
        table,
        filing_id="filing:2",
        document_id="document:2",
        section_id=None,
        block_id="block:2",
    )

    assert len(facts) == 1
    assert facts[0].fact_kind is FactKind.LABEL_VALUE
    assert facts[0].label_text == "계약상대"
    assert facts[0].value_text == "테스트 주식회사"


def test_wide_narrative_row_is_not_exploded_into_text_facts() -> None:
    table = TableData(
        table_id="table:wide",
        row_count=1,
        column_count=5,
        cells=[
            TableCell(
                row_index=0,
                column_index=index,
                text_raw=f"값{index}",
                text_normalized=f"값{index}",
            )
            for index in range(5)
        ],
    )

    facts = extract_table_facts(
        table,
        filing_id="filing:3",
        document_id="document:3",
        section_id=None,
        block_id="block:3",
    )

    assert facts == ()


def test_rowspan_label_is_available_to_following_logical_row() -> None:
    table = TableData(
        table_id="table:rowspan",
        row_count=2,
        column_count=2,
        cells=[
            TableCell(
                row_index=0,
                column_index=0,
                row_span=2,
                text_raw="계약기간",
                text_normalized="계약기간",
            ),
            TableCell(
                row_index=0,
                column_index=1,
                text_raw="2025-01-01",
                text_normalized="2025-01-01",
            ),
            TableCell(
                row_index=1,
                column_index=1,
                text_raw="2025-12-31",
                text_normalized="2025-12-31",
            ),
        ],
    )

    facts = extract_table_facts(
        table,
        filing_id="filing:4",
        document_id="document:4",
        section_id=None,
        block_id="block:4",
    )

    assert [fact.label_text for fact in facts] == ["계약기간", "계약기간"]
    assert [fact.value_text for fact in facts] == ["2025-01-01", "2025-12-31"]
