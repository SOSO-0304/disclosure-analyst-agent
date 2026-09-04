from datetime import date

from disclosure_agent.domain.fundraising_analysis import (
    FundraisingAnalysisResult,
    FundraisingCategorySummary,
    FundraisingEventObservation,
)
from disclosure_agent.extractors.fundraising import FundraisingInstrument
from disclosure_agent.retrieval.evidence_pack import EvidenceItem, EvidencePack
from disclosure_agent.services.answer_service import (
    _render_fundraising_answer,
    _render_requested_fundraising_absence,
)


def _pack() -> EvidencePack:
    items = tuple(
        EvidenceItem(
            evidence_id=f"event:{index}",
            source_kind="sql_fundraising",
            rank=index,
            score=1.0,
            semantic_score=0.0,
            lexical_score=0.0,
            company_name="우리기술",
            filing_id=f"filing:{index}",
            report_name="사업보고서 (2025.12)",
            document_id=f"document:{index}",
            section_id=f"section:{index}",
            content_text=f"전환사채(CB) 이벤트 {index}",
            truncated=False,
            matched_terms=(),
            block_ids=(),
            table_ids=(),
            event_ids=(f"cb:{index}",),
        )
        for index in range(1, 4)
    )
    return EvidencePack(
        query="우리기술이 2025년에 유상증자로 조달한 내역이 있어?",
        retrieval_status="MATCHES_FOUND",
        items=items,
        total_chars=sum(len(item.content_text) for item in items),
    )


def _category(instrument: FundraisingInstrument, status: str) -> FundraisingCategorySummary:
    return FundraisingCategorySummary(
        instrument_type=instrument,
        status=status,
        event_count=0 if status == "NO_MATCH" else 1,
        known_amount_count=0 if status == "NO_MATCH" else 1,
        missing_amount_count=0,
        total_amount_krw=None if status == "NO_MATCH" else 1,
        known_amount_sum_krw=0 if status == "NO_MATCH" else 1,
        events=(),
    )


def _analysis(*categories: FundraisingCategorySummary) -> FundraisingAnalysisResult:
    return FundraisingAnalysisResult(
        company_name="우리기술",
        year=2025,
        status="ANSWERABLE",
        event_count=3,
        known_amount_count=3,
        missing_amount_count=0,
        total_amount_krw=37_800_000_000,
        known_amount_sum_krw=37_800_000_000,
        categories=categories,
    )


def _cb_event(index: int, *, amount: int, series: str) -> FundraisingEventObservation:
    return FundraisingEventObservation(
        event_id=f"cb:{index}",
        instrument_type=FundraisingInstrument.CONVERTIBLE_BOND,
        issue_date=date(2025, index, index),
        amount_krw=amount,
        issuer_name="우리기술",
        security_name=f"제{series}회 전환사채",
        series=f"제{series}회",
        issuance_method="사모",
        stock_kind=None,
        share_quantity=None,
        issue_price_krw=None,
        source_count=1,
        representative_filing_id=f"filing:{index}",
        representative_table_id=f"table:{index}",
        representative_row_index=index,
    )


def _positive_cb_category() -> FundraisingCategorySummary:
    events = (
        _cb_event(1, amount=5_000_000_000, series="17"),
        _cb_event(2, amount=10_800_000_000, series="18"),
        _cb_event(3, amount=22_000_000_000, series="19"),
    )
    return FundraisingCategorySummary(
        instrument_type=FundraisingInstrument.CONVERTIBLE_BOND,
        status="ANSWERABLE",
        event_count=3,
        known_amount_count=3,
        missing_amount_count=0,
        total_amount_krw=37_800_000_000,
        known_amount_sum_krw=37_800_000_000,
        events=events,
    )


def test_positive_fundraising_aggregation_is_rendered_deterministically() -> None:
    answer = _render_fundraising_answer(
        "우리기술의 2025년 전환사채(CB) 발행 건수와 총 조달금액을 알려줘",
        _analysis(_positive_cb_category()),
        _pack(),
    )

    assert "전환사채(CB): 3건" in answer
    assert "총 조달금액 378억 원" in answer
    assert "[E1][E2][E3]" in answer


def test_fundraising_event_details_are_rendered_from_canonical_events() -> None:
    answer = _render_fundraising_answer(
        "우리기술이 2025년에 발행한 전환사채(CB)를 회차별로 날짜와 금액까지 정리해줘",
        _analysis(_positive_cb_category()),
        _pack(),
    )

    assert "제17회" in answer
    assert "50억 원" in answer
    assert "제18회" in answer
    assert "108억 원" in answer
    assert "제19회" in answer
    assert "220억 원" in answer
    assert "[E1]" in answer and "[E2]" in answer and "[E3]" in answer


def test_requested_zero_event_type_is_rendered_deterministically() -> None:
    answer = _render_requested_fundraising_absence(
        "우리기술이 2025년에 유상증자로 조달한 내역이 있어?",
        _analysis(_category(FundraisingInstrument.RIGHTS_ISSUE, "NO_MATCH")),
        _pack(),
    )

    assert answer is not None
    assert answer.startswith("유상증자: 확인된 내역 없음")
    assert "조달금액 0원으로 해석하지 않습니다" in answer
    assert "집계 범위 근거: [E1][E2][E3]" in answer


def test_requested_multiple_zero_event_types_are_all_preserved() -> None:
    answer = _render_requested_fundraising_absence(
        "우리기술의 2025년 BW와 EB 발행 내역만 확인해줘",
        _analysis(
            _category(FundraisingInstrument.BOND_WITH_WARRANTS, "NO_MATCH"),
            _category(FundraisingInstrument.EXCHANGEABLE_BOND, "NO_MATCH"),
        ),
        _pack(),
    )

    assert answer is not None
    assert "신주인수권부사채(BW): 확인된 내역 없음" in answer
    assert "교환사채(EB): 확인된 내역 없음" in answer


def test_positive_requested_type_still_uses_normal_generation_path() -> None:
    answer = _render_requested_fundraising_absence(
        "우리기술의 2025년 CB 발행 내역을 알려줘",
        _analysis(_category(FundraisingInstrument.CONVERTIBLE_BOND, "ANSWERABLE")),
        _pack(),
    )

    assert answer is None
