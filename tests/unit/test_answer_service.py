from types import SimpleNamespace

from disclosure_agent.services.answer_service import (
    AnswerService,
    _grounding_prompt_for_query,
    _round_robin,
)


def _runner(name: str):
    def run(query, plan, **kwargs):
        return name, query, plan.mode.value, kwargs

    return run


def test_answer_service_dispatches_metric_query(monkeypatch) -> None:
    service = AnswerService(SimpleNamespace())
    monkeypatch.setattr(service, "_answer_metric", _runner("metric"))

    result = service.answer("카카오의 2025년 매출액은 얼마인가?")

    assert result[0] == "metric"
    assert result[2] == "metric_structured"


def test_answer_service_dispatches_fundraising_query(monkeypatch) -> None:
    service = AnswerService(SimpleNamespace())
    monkeypatch.setattr(service, "_answer_fundraising", _runner("fundraising"))

    result = service.answer("우리기술의 2025년 자금조달 내역을 유형별로 정리해줘")

    assert result[0] == "fundraising"
    assert result[2] == "fundraising_structured"


def test_answer_service_dispatches_supply_contract_query(monkeypatch) -> None:
    service = AnswerService(SimpleNamespace())
    monkeypatch.setattr(service, "_answer_supply_contract", _runner("supply_contract"))

    result = service.answer(
        "두산퓨얼셀이 2023년에 체결한 주요 계약 중 이후 해지된 계약이 존재하는가?"
    )

    assert result[0] == "supply_contract"
    assert result[2] == "supply_contract_structured"


def test_answer_service_dispatches_narrative_query_to_hybrid(monkeypatch) -> None:
    service = AnswerService(SimpleNamespace())
    monkeypatch.setattr(service, "_answer_hybrid", _runner("hybrid"))

    result = service.answer("카카오의 2025년 주요 사업 변화와 영향을 설명해줘")

    assert result[0] == "hybrid"
    assert result[2] == "hybrid_grounded"


def test_hybrid_year_does_not_collapse_multi_year_comparison() -> None:
    year = AnswerService._hybrid_year(
        "카카오의 2023년과 2025년 사업보고서 변화를 비교해줘",
        fallback_year=None,
        report_name=None,
    )

    assert year is None


def test_hybrid_year_keeps_single_year_filter() -> None:
    year = AnswerService._hybrid_year(
        "카카오의 2025년 사업보고서 핵심 변화를 설명해줘",
        fallback_year=None,
        report_name=None,
    )

    assert year == 2025


def test_hybrid_years_preserve_all_comparison_years() -> None:
    years = AnswerService._hybrid_years(
        "삼성전자의 2023년과 2025년 사업보고서를 비교해줘",
        fallback_year=None,
        report_name=None,
    )

    assert years == (2023, 2025)


def test_infers_explicit_report_type_from_query() -> None:
    assert AnswerService._infer_report_type("2025년 사업보고서를 기준으로 정리해줘") == "사업보고서"
    assert AnswerService._infer_report_type("2026년 1분기 분기보고서를 요약해줘") == "분기보고서"


def test_round_robin_balances_comparison_groups() -> None:
    merged = _round_robin((("2023-a", "2023-b", "2023-c"), ("2025-a", "2025-b")), limit=5)

    assert merged == ("2023-a", "2025-a", "2023-b", "2025-b", "2023-c")


def test_investment_plan_prompt_excludes_shareholder_return_by_default() -> None:
    prompt = _grounding_prompt_for_query(
        "삼성전자의 2025년 사업보고서를 기준으로 주요 투자 계획과 목적을 정리해줘"
    )

    assert "배당, 자사주 매입, 주주환원은 투자 계획으로 분류하지 마세요" in prompt


def test_multi_year_prompt_guards_temporal_attribution() -> None:
    prompt = _grounding_prompt_for_query(
        "삼성전자의 2023년과 2025년 사업보고서에서 핵심 사업 변화를 비교해줘"
    )

    assert "다른 연도의 사업보고서 내용으로 재귀속하지 마세요" in prompt
