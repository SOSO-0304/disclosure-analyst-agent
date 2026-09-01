from datetime import date
from types import SimpleNamespace

from disclosure_agent.domain.fundraising_analysis import (
    FundraisingAnalysisResult,
    FundraisingCategorySummary,
    FundraisingEventObservation,
)
from disclosure_agent.extractors.fundraising import FundraisingInstrument
from disclosure_agent.llm.prompts import GROUNDING_SYSTEM_PROMPT, build_grounded_answer_prompt
from disclosure_agent.retrieval import fundraising_query_resolver
from disclosure_agent.retrieval.company_resolver import CompanyIdentity
from disclosure_agent.retrieval.evidence_pack import EvidencePack
from disclosure_agent.retrieval.fundraising_evidence import (
    render_deterministic_fundraising_analysis,
)
from disclosure_agent.retrieval.fundraising_query_resolver import (
    resolve_fundraising_query_target,
)


def _company(corp_code: str, listed_name: str) -> CompanyIdentity:
    return CompanyIdentity(
        corp_code=corp_code,
        stock_code=None,
        listed_name=listed_name,
        corp_name=listed_name,
    )


def _event(event_id: str, amount_krw: int, series: str) -> FundraisingEventObservation:
    return FundraisingEventObservation(
        event_id=event_id,
        instrument_type=FundraisingInstrument.CONVERTIBLE_BOND,
        issue_date=date(2025, 1, 1),
        amount_krw=amount_krw,
        issuer_name="우리기술",
        security_name="무기명 이권부 무보증 사모 전환사채",
        series=series,
        issuance_method="사모",
        stock_kind=None,
        share_quantity=None,
        issue_price_krw=None,
        source_count=2,
        representative_filing_id="periodic_1",
        representative_table_id="table_1",
        representative_row_index=1,
    )


def test_resolves_single_company_and_year(monkeypatch) -> None:
    companies = (_company("1", "우리기술"), _company("2", "카카오"))
    monkeypatch.setattr(
        fundraising_query_resolver,
        "_source_companies",
        lambda session: companies,
    )

    target = resolve_fundraising_query_target(
        SimpleNamespace(),
        query="우리기술이 2025년에 실시한 자금조달 내역을 유형별로 정리해줘",
    )

    assert target.status == "RESOLVED"
    assert target.company_name == "우리기술"
    assert target.year == 2025


def test_multiple_companies_are_ambiguous(monkeypatch) -> None:
    companies = (_company("1", "우리기술"), _company("2", "카카오"))
    monkeypatch.setattr(
        fundraising_query_resolver,
        "_source_companies",
        lambda session: companies,
    )

    target = resolve_fundraising_query_target(
        SimpleNamespace(),
        query="우리기술과 카카오의 2025년 자금조달 내역을 정리해줘",
    )

    assert target.status == "AMBIGUOUS"
    assert target.reason == "multiple_companies"


def test_rendered_analysis_keeps_empty_types_distinct_from_zero_amount() -> None:
    events = (
        _event("cb1", 5_000_000_000, "제17회"),
        _event("cb2", 10_800_000_000, "제18회"),
        _event("cb3", 22_000_000_000, "제19회"),
    )
    categories = (
        FundraisingCategorySummary(
            instrument_type=FundraisingInstrument.RIGHTS_ISSUE,
            status="NO_MATCH",
            event_count=0,
            known_amount_count=0,
            missing_amount_count=0,
            total_amount_krw=None,
            known_amount_sum_krw=0,
            events=(),
        ),
        FundraisingCategorySummary(
            instrument_type=FundraisingInstrument.CONVERTIBLE_BOND,
            status="ANSWERABLE",
            event_count=3,
            known_amount_count=3,
            missing_amount_count=0,
            total_amount_krw=37_800_000_000,
            known_amount_sum_krw=37_800_000_000,
            events=events,
        ),
        FundraisingCategorySummary(
            instrument_type=FundraisingInstrument.BOND_WITH_WARRANTS,
            status="NO_MATCH",
            event_count=0,
            known_amount_count=0,
            missing_amount_count=0,
            total_amount_krw=None,
            known_amount_sum_krw=0,
            events=(),
        ),
        FundraisingCategorySummary(
            instrument_type=FundraisingInstrument.EXCHANGEABLE_BOND,
            status="NO_MATCH",
            event_count=0,
            known_amount_count=0,
            missing_amount_count=0,
            total_amount_krw=None,
            known_amount_sum_krw=0,
            events=(),
        ),
    )
    result = FundraisingAnalysisResult(
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

    rendered = render_deterministic_fundraising_analysis(
        result,
        evidence_labels_by_event_id={"cb1": "E1", "cb2": "E2", "cb3": "E3"},
    )

    assert "유형: 전환사채(CB) | 3건 | 합계 378억 원 | [E1],[E2],[E3]" in rendered
    assert "유형: 유상증자 | 확인된 이벤트 0건 | 금액 0원으로 해석하지 않음" in rendered
    assert "유형: 신주인수권부사채(BW) | 확인된 이벤트 0건" in rendered
    assert "유형: 교환사채(EB) | 확인된 이벤트 0건" in rendered


def test_fundraising_prompt_requires_local_citations() -> None:
    assert "자금조달 유형별 건수나 합계를 말할 때" in GROUNDING_SYSTEM_PROMPT
    assert "자금조달 개별 이벤트의 날짜, 금액, 회차를 말할 때" in GROUNDING_SYSTEM_PROMPT
    assert "포괄 문장에만 몰아서 인용하지 마세요" in GROUNDING_SYSTEM_PROMPT

    pack = EvidencePack(
        query="우리기술 2025년 자금조달",
        retrieval_status="NO_MATCH",
        items=(),
        total_chars=0,
    )
    prompt = build_grounded_answer_prompt(pack.query, pack)

    assert "자금조달 유형별 건수와 합계 문장에는 해당 유형의 모든 Evidence" in prompt
    assert "자금조달 개별 이벤트의 날짜·금액·회차 항목에는 해당 이벤트 Evidence" in prompt
    assert "답변 끝의 포괄 문장 하나에 몰아 넣어 로컬 인용을 대체하지 마세요" in prompt
