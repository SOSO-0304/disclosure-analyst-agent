from dataclasses import replace
from datetime import date
from typing import Any

from disclosure_agent.extractors.fundraising import (
    FundraisingInstrument,
    canonicalize_fundraising_occurrences,
    extract_fundraising_occurrences,
)


def _cell(
    row: int,
    column: int,
    text: str,
    *,
    header: bool = False,
    numeric: str | None = None,
    unit: str | None = None,
) -> dict[str, Any]:
    return {
        "row_index": row,
        "column_index": column,
        "row_span": 1,
        "column_span": 1,
        "is_header": header,
        "text_raw": text,
        "text_normalized": text,
        "numeric_value": numeric,
        "unit_raw": unit,
    }


def _extract(grid: dict[str, Any], normalized_text: str = ""):
    return extract_fundraising_occurrences(
        filing_id="periodic_1",
        corp_code="00000001",
        company_name="테스트",
        receipt_date=date(2025, 11, 14),
        table_id="table_1",
        grid=grid,
        normalized_text=normalized_text,
    )


def test_extracts_rights_issue_amount_from_quantity_and_price() -> None:
    headers = [
        _cell(0, 0, "주식발행(감소)일자", header=True),
        _cell(0, 1, "발행(감소)형태", header=True),
        _cell(0, 2, "종류", header=True),
        _cell(0, 3, "수량", header=True),
        _cell(0, 4, "주당액면가액", header=True),
        _cell(0, 5, "주당발행(감소)가액", header=True),
    ]
    data = [
        _cell(1, 0, "2025.07.02"),
        _cell(1, 1, "유상증자(제3자배정)"),
        _cell(1, 2, "보통주 발행"),
        _cell(1, 3, "1,300,000", numeric="1300000"),
        _cell(1, 4, "500", numeric="500"),
        _cell(1, 5, "14,681", numeric="14681"),
    ]

    grid = {"header_row_indices": [0, 1], "cells": headers + data}
    occurrences = _extract(grid)

    assert len(occurrences) == 1
    event = occurrences[0]
    assert event.instrument_type is FundraisingInstrument.RIGHTS_ISSUE
    assert event.issue_date == date(2025, 7, 2)
    assert event.share_quantity == 1_300_000
    assert event.issue_price_krw == 14_681
    assert event.amount_krw == 19_085_300_000


def test_bond_matrix_uses_original_principal_not_outstanding_balance() -> None:
    headers = [
        _cell(0, 0, "종류＼구분", header=True),
        _cell(0, 1, "회차", header=True),
        _cell(0, 2, "발행일", header=True),
        _cell(0, 3, "권면(전자등록)총액", header=True),
        _cell(0, 4, "미상환사채 권면(전자등록)총액", header=True),
    ]
    data = [
        _cell(1, 0, "제1회 무보증 사모 전환사채"),
        _cell(1, 1, "1"),
        _cell(1, 2, "2020.09.02"),
        _cell(1, 3, "30,000,000,000", numeric="30000000000"),
        _cell(1, 4, "20,000,000,000", numeric="20000000000"),
    ]

    occurrences = _extract({"header_row_indices": [0], "cells": headers + data})

    assert len(occurrences) == 1
    event = occurrences[0]
    assert event.instrument_type is FundraisingInstrument.CONVERTIBLE_BOND
    assert event.amount_krw == 30_000_000_000


def test_bond_matrix_applies_explicit_table_unit() -> None:
    headers = [
        _cell(0, 0, "종류＼구분", header=True),
        _cell(0, 1, "회차", header=True),
        _cell(0, 2, "발행일", header=True),
        _cell(0, 3, "권면(전자등록)총액", header=True),
    ]
    data = [
        _cell(1, 0, "제1회 사모 신주인수권부사채"),
        _cell(1, 1, "1회차"),
        _cell(1, 2, "2021.10.28"),
        _cell(1, 3, "25,000", numeric="25000"),
    ]

    occurrences = _extract(
        {"header_row_indices": [0], "cells": headers + data},
        "(단위 : 백만원) 제1회 사모 신주인수권부사채",
    )

    assert len(occurrences) == 1
    event = occurrences[0]
    assert event.instrument_type is FundraisingInstrument.BOND_WITH_WARRANTS
    assert event.amount_krw == 25_000_000_000
    assert event.amount_unit == "백만원"


def test_bond_matrix_uses_unit_from_grid_when_normalized_text_omits_it() -> None:
    unit_row = [_cell(0, 0, "(단위 : 백만원)", header=True)]
    headers = [
        _cell(1, 0, "종류＼구분", header=True),
        _cell(1, 1, "회차", header=True),
        _cell(1, 2, "발행일", header=True),
        _cell(1, 3, "권면(전자등록)총액", header=True),
    ]
    data = [
        _cell(2, 0, "무기명식 무보증사모 전환사채"),
        _cell(2, 1, "14"),
        _cell(2, 2, "2025.05.30"),
        _cell(2, 3, "50,183", numeric="50183"),
    ]
    grid = {"header_row_indices": [0, 1], "cells": unit_row + headers + data}

    occurrences = _extract(grid, "전환사채 현황")

    assert len(occurrences) == 1
    event = occurrences[0]
    assert event.instrument_type is FundraisingInstrument.CONVERTIBLE_BOND
    assert event.amount_krw == 50_183_000_000
    assert event.amount_unit == "백만원"


def test_vertical_exchangeable_bond_keeps_missing_issue_date_explicit() -> None:
    cells = [
        _cell(0, 0, "구 분", header=True),
        _cell(0, 1, "제75회 무기명식 후순위 교환사채", header=True),
        _cell(1, 0, "만기"),
        _cell(1, 1, "30년"),
        _cell(2, 0, "사채의 권면 총액"),
        _cell(2, 1, "100,000,000,000원", numeric="100000000000"),
    ]

    occurrences = _extract({"header_row_indices": [0], "cells": cells})

    assert len(occurrences) == 1
    event = occurrences[0]
    assert event.instrument_type is FundraisingInstrument.EXCHANGEABLE_BOND
    assert event.issue_date is None
    assert event.amount_krw == 100_000_000_000


def test_vertical_bond_rejects_single_cell_narrative_note() -> None:
    text = (
        "제17회 사모전환사채(발행일자: 25.04.30) 권면총액은 "
        "5월 6일 10억원이 전환청구되어 감소하였습니다."
    )
    cells = [_cell(0, 0, text)]

    occurrences = _extract({"header_row_indices": [], "cells": cells}, text)

    assert occurrences == ()


def test_deduplication_collapses_repeated_periodic_report_occurrences() -> None:
    headers = [
        _cell(0, 0, "종류＼구분", header=True),
        _cell(0, 1, "회차", header=True),
        _cell(0, 2, "발행일", header=True),
        _cell(0, 3, "권면총액", header=True),
    ]
    data = [
        _cell(1, 0, "제1회 사모 전환사채"),
        _cell(1, 1, "1"),
        _cell(1, 2, "2025.01.02"),
        _cell(1, 3, "10,000,000,000", numeric="10000000000"),
    ]
    first = _extract({"header_row_indices": [0], "cells": headers + data})[0]
    latest = replace(
        first,
        filing_id="periodic_2",
        receipt_date=date(2026, 3, 20),
    )

    events = canonicalize_fundraising_occurrences((first, latest))

    assert len(events) == 1
    assert events[0].source_count == 2
    assert events[0].source_filing_ids == ("periodic_1", "periodic_2")
    assert events[0].occurrence.filing_id == "periodic_2"


def test_amount_unit_does_not_match_arbitrary_korean_context() -> None:
    headers = [
        _cell(0, 0, "종류＼구분", header=True),
        _cell(0, 1, "회차", header=True),
        _cell(0, 2, "발행일", header=True),
        _cell(0, 3, "권면총액", header=True),
    ]
    data = [
        _cell(1, 0, "제1회 사모 전환사채"),
        _cell(1, 1, "1"),
        _cell(1, 2, "2025.01.02"),
        _cell(1, 3, "25,000", numeric="25000"),
    ]

    occurrences = _extract(
        {"header_row_indices": [0], "cells": headers + data},
        "운영자금 지원을 위한 전환사채",
    )

    assert len(occurrences) == 1
    assert occurrences[0].amount_krw is None
    assert occurrences[0].amount_unit is None
