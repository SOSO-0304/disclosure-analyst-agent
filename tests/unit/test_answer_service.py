from types import SimpleNamespace

from disclosure_agent.llm.hcx_client import HcxAnswerResult
from disclosure_agent.retrieval.evidence_pack import EvidenceItem, EvidencePack
from disclosure_agent.services.answer_service import (
    AnswerService,
    _generation_status,
    _grounding_prompt_for_query,
    _render_facility_execution_semantic_answer,
    _render_future_market_price_limit_answer,
    _render_predictive_probability_limit_answer,
    _render_quantified_attribution_limit_answer,
    _render_multi_scope_comparison_fallback,
    _render_multi_year_comparison_fallback,
    _round_robin,
)


def _runner(name: str):
    def run(query, plan, **kwargs):
        return name, query, plan.mode.value, kwargs

    return run


def _model_result(finish_reason: str) -> HcxAnswerResult:
    return HcxAnswerResult(
        content="answer",
        finish_reason=finish_reason,
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
    )


def test_generation_status_downgrades_grounding_exhaustion() -> None:
    assert _generation_status("ANSWERABLE", _model_result("stop")) == "ANSWERABLE"
    assert _generation_status("ANSWERABLE", _model_result("grounding_exhausted")) == "PARTIAL"


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


def test_hybrid_company_resolution_preserves_multiple_mentions(monkeypatch) -> None:
    service = AnswerService(SimpleNamespace())
    monkeypatch.setattr(
        "disclosure_agent.services.answer_service.match_query_companies",
        lambda session, query: (
            SimpleNamespace(listed_name="삼성전자"),
            SimpleNamespace(listed_name="현대차"),
            SimpleNamespace(listed_name="카카오"),
        ),
    )

    companies, error = service._resolve_hybrid_companies(
        "삼성전자, 현대차, 카카오의 전략을 비교해줘",
        None,
    )

    assert companies == ("삼성전자", "현대차", "카카오")
    assert error is None


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


def test_hybrid_years_separate_report_scope_from_forecast_target() -> None:
    years = AnswerService._hybrid_years(
        "LG에너지솔루션의 2025년 사업보고서를 보면 2027년 주가가 얼마가 될지 계산할 수 있지?",
        fallback_year=None,
        report_name=None,
    )

    assert years == (2025,)


def test_infers_explicit_report_type_from_query() -> None:
    annual = AnswerService._infer_report_type("2025년 사업보고서를 기준으로 정리해줘")
    quarterly = AnswerService._infer_report_type("2026년 1분기 분기보고서를 요약해줘")

    assert annual == "사업보고서"
    assert quarterly == "분기보고서"


def test_round_robin_balances_comparison_groups() -> None:
    groups = (("2023-a", "2023-b", "2023-c"), ("2025-a", "2025-b"))
    merged = _round_robin(groups, limit=5)

    assert merged == ("2023-a", "2025-a", "2023-b", "2025-b", "2023-c")


def _facility_item(rank: int, amount: int, subject: str) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"facility:{rank}",
        source_kind="sql_facility_investment",
        rank=rank,
        score=1.0,
        semantic_score=0.0,
        lexical_score=0.0,
        company_name="한화오션",
        filing_id=f"filing:{rank}",
        report_name="신규시설투자등",
        document_id=f"document:{rank}",
        section_id=f"section:{rank}",
        content_text="\n".join(
            (
                f"투자대상: {subject}",
                f"투자금액: {amount:,}원",
                "이사회결정일: 2025-01-01",
            )
        ),
        truncated=False,
        matched_terms=(),
        block_ids=(),
        table_ids=(),
    )


def _semantic_item(
    rank: int,
    year: int,
    text: str,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"semantic:{rank}",
        source_kind="semantic_chunk",
        rank=rank,
        score=1.0,
        semantic_score=1.0,
        lexical_score=1.0,
        company_name="삼성전자",
        filing_id=f"filing:{year}:{rank}",
        report_name=f"사업보고서 ({year}.12)",
        document_id=f"document:{year}:{rank}",
        section_id=f"section:{year}:{rank}",
        content_text="\n".join(
            (
                "회사: 삼성전자",
                f"공시: 사업보고서 ({year}.12)",
                "문서: 사업의 내용",
                "섹션: 반도체 사업",
                text,
            )
        ),
        truncated=False,
        matched_terms=(),
        block_ids=(),
        table_ids=(),
    )


def _company_semantic_item(
    rank: int,
    company_name: str,
    year: int,
    text: str,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"semantic:{company_name}:{year}:{rank}",
        source_kind="semantic_chunk",
        rank=rank,
        score=1.0,
        semantic_score=1.0,
        lexical_score=1.0,
        company_name=company_name,
        filing_id=f"filing:{company_name}:{year}:{rank}",
        report_name=f"사업보고서 ({year}.12)",
        document_id=f"document:{company_name}:{year}:{rank}",
        section_id=f"section:{company_name}:{year}:{rank}",
        content_text="\n".join(
            (
                f"회사: {company_name}",
                f"공시: 사업보고서 ({year}.12)",
                "문서: 사업의 내용",
                "섹션: 핵심 사업",
                text,
            )
        ),
        truncated=False,
        matched_terms=(),
        block_ids=(),
        table_ids=(),
    )


def test_multi_scope_comparison_fallback_preserves_company_and_year_scopes() -> None:
    query = (
        "A사와 B사의 2024년 및 2025년 사업보고서를 비교해서 "
        "두 기업의 핵심 사업 전략 변화가 어떻게 달랐는지 설명해줘"
    )
    items = (
        _company_semantic_item(1, "A사", 2024, "AI 제품 사업을 확대했습니다."),
        _company_semantic_item(2, "A사", 2025, "신규 서비스 사업을 강화했습니다."),
        _company_semantic_item(3, "B사", 2024, "해외 시장 투자를 확대했습니다."),
        _company_semantic_item(4, "B사", 2025, "고객 플랫폼 전략을 강화했습니다."),
    )
    pack = EvidencePack(
        query=query,
        retrieval_status="MATCHES_FOUND",
        items=items,
        total_chars=sum(len(item.content_text) for item in items),
    )

    answer = _render_multi_scope_comparison_fallback(query, pack)

    assert answer is not None
    assert "A사:" in answer
    assert "B사:" in answer
    assert "2024년:" in answer
    assert "2025년:" in answer
    assert "[E1]" in answer
    assert "[E2]" in answer
    assert "[E3]" in answer
    assert "[E4]" in answer
    assert "비교하면" in answer


def test_multi_year_comparison_fallback_preserves_years_strategy_terms_and_citations() -> None:
    items = (
        _semantic_item(
            1,
            2023,
            "DDR5와 서버용 메모리 제품 대응을 강화하고 고부가 제품 비중을 확대했습니다.",
        ),
        _semantic_item(
            2,
            2023,
            "메모리 시장 변화에 맞춰 제품 경쟁력을 강화했습니다.",
        ),
        _semantic_item(
            3,
            2025,
            "HBM4 중심의 고부가 메모리 제품 공급을 확대하고 AI 서버 수요에 대응합니다.",
        ),
        _semantic_item(
            4,
            2025,
            "서버향 제품 중심으로 수요 강세에 대응하고 있습니다.",
        ),
    )
    query = (
        "삼성전자의 2023년과 2025년 사업보고서를 기준으로 "
        "메모리·반도체 사업 전략이 어떻게 달라졌는지 비교해줘"
    )
    pack = EvidencePack(
        query=query,
        retrieval_status="MATCHES_FOUND",
        items=items,
        total_chars=sum(len(item.content_text) for item in items),
    )

    answer = _render_multi_year_comparison_fallback(query, pack)

    assert answer is not None
    assert "2023년:" in answer
    assert "2025년:" in answer
    assert "DDR5" in answer
    assert "HBM4" in answer
    assert "[E1]" in answer
    assert "[E3]" in answer


def test_multi_year_comparison_fallback_skips_non_comparison_query() -> None:
    item = _semantic_item(
        1,
        2025,
        "HBM4 중심의 고부가 메모리 제품 공급을 확대합니다.",
    )
    query = "삼성전자의 2025년 사업보고서에서 메모리 전략을 정리해줘"
    pack = EvidencePack(
        query=query,
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )

    assert _render_multi_year_comparison_fallback(query, pack) is None


def test_facility_execution_semantics_are_rendered_deterministically() -> None:
    items = (
        _facility_item(1, 332_800_000_000, "Floating Dock 확장"),
        _facility_item(2, 268_000_000_000, "6,500톤급 Floating Crane"),
    )
    pack = EvidencePack(
        query="한화오션은 2025년에 실제로 6,008억 원을 투자한 것으로 보면 돼?",
        retrieval_status="MATCHES_FOUND",
        items=items,
        total_chars=sum(len(item.content_text) for item in items),
    )

    answer = _render_facility_execution_semantic_answer(pack.query, pack)

    assert answer is not None
    assert "실제 집행액이 6,008억 원이었다고 단정하기는 어렵습니다" in answer
    assert "신규시설투자 결정 금액 합계 6,008억 원" in answer
    assert "[E1][E2]" in answer


def test_non_execution_investment_query_keeps_model_path() -> None:
    item = _facility_item(1, 332_800_000_000, "Floating Dock 확장")
    pack = EvidencePack(
        query="한화오션의 2025년 투자 목적을 설명해줘",
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )

    assert _render_facility_execution_semantic_answer(pack.query, pack) is None


def test_future_market_price_is_rejected_deterministically() -> None:
    query = (
        "LG에너지솔루션의 2025년 사업보고서를 보면 "
        "2027년 주가가 얼마가 될지 계산할 수 있지?"
    )

    answer = _render_future_market_price_limit_answer(query)

    assert answer is not None
    assert "미래 주가를 계산하거나 확정적으로 예측할 수 없습니다" in answer


def test_predictive_probability_does_not_reuse_generic_success_statistics() -> None:
    item = _semantic_item(
        1,
        2025,
        "일반적인 신약개발 과정에서 후보물질 탐색부터 최종 승인까지 성공 가능성은 0.01%입니다.",
    )
    query = "한미약품의 신약이 FDA 승인을 받을 확률을 사업보고서만 보고 숫자로 계산해줘"
    pack = EvidencePack(
        query=query,
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )

    answer = _render_predictive_probability_limit_answer(query, pack)

    assert answer is not None
    assert "객관적인 퍼센트로 계산할 수 없습니다" in answer
    assert "일반적인 산업 통계나 개발 성공률" in answer


def test_quantified_attribution_does_not_substitute_total_revenue() -> None:
    item = _semantic_item(
        1,
        2025,
        "2025년 연결기준 매출액은 4조 1,624억 원입니다.",
    )
    query = "셀트리온의 2025년 매출 증가 중 미국 생산시설 인수가 기여한 금액을 정확히 계산해줘"
    pack = EvidencePack(
        query=query,
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )

    answer = _render_quantified_attribution_limit_answer(query, pack)

    assert answer is not None
    assert "기여한 금액 또는 비율을 직접 분리해 확인할 수 없습니다" in answer
    assert "전체 매출액" in answer


def test_quantified_attribution_keeps_model_path_when_directly_disclosed() -> None:
    item = _semantic_item(
        1,
        2025,
        "미국 생산시설 인수 효과가 매출 증가에 기여한 금액은 500억 원입니다.",
    )
    query = "미국 생산시설 인수가 매출 증가에 기여한 금액은 얼마야?"
    pack = EvidencePack(
        query=query,
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )

    assert _render_quantified_attribution_limit_answer(query, pack) is None


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
