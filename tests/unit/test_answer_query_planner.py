from disclosure_agent.retrieval.answer_query_planner import (
    AnswerExecutionMode,
    plan_answer_query,
)
from disclosure_agent.retrieval.query_router import RetrievalRail


def test_revenue_value_uses_metric_engine() -> None:
    plan = plan_answer_query("카카오의 2025년 연결기준 매출액은 얼마인가?")

    assert plan.mode is AnswerExecutionMode.METRIC_STRUCTURED
    assert plan.route.rails == (RetrievalRail.REVENUE,)


def test_facility_ranking_uses_metric_engine_even_with_semantic_route() -> None:
    plan = plan_answer_query(
        "두산에너빌리티와 한화오션 중 2025년 설비투자 규모가 더 큰 기업은 어디인가?"
    )

    assert plan.mode is AnswerExecutionMode.METRIC_STRUCTURED
    assert RetrievalRail.FACILITY_INVESTMENT in plan.route.rails
    assert RetrievalRail.SEMANTIC in plan.route.rails


def test_investment_plan_uses_hybrid_engine() -> None:
    plan = plan_answer_query("카카오의 2026년 1분기 주요 투자 계획과 목적을 정리해줘")

    assert plan.mode is AnswerExecutionMode.HYBRID_GROUNDED


def test_fundraising_summary_uses_structured_engine() -> None:
    plan = plan_answer_query(
        "우리기술이 2025년에 실시한 자금조달 내역을 유형별로 정리해줘"
    )

    assert plan.mode is AnswerExecutionMode.FUNDRAISING_STRUCTURED


def test_fundraising_reason_uses_hybrid_engine() -> None:
    plan = plan_answer_query("우리기술이 2025년에 CB로 자금조달한 이유를 설명해줘")

    assert plan.mode is AnswerExecutionMode.HYBRID_GROUNDED


def test_supply_contract_termination_uses_structured_engine() -> None:
    plan = plan_answer_query(
        "두산퓨얼셀이 2023년에 체결한 주요 계약 중 이후 해지된 계약이 존재하는가?"
    )

    assert plan.mode is AnswerExecutionMode.SUPPLY_CONTRACT_STRUCTURED


def test_supply_contract_change_explanation_uses_hybrid_engine() -> None:
    plan = plan_answer_query("두산퓨얼셀 공급계약의 변경 내용과 영향을 설명해줘")

    assert plan.mode is AnswerExecutionMode.HYBRID_GROUNDED


def test_business_report_change_uses_hybrid_engine() -> None:
    plan = plan_answer_query("2023년과 2025년 사업보고서의 핵심 사업 변화를 비교해줘")

    assert plan.mode is AnswerExecutionMode.HYBRID_GROUNDED


def test_causal_revenue_contribution_uses_hybrid_engine() -> None:
    plan = plan_answer_query(
        "셀트리온의 2025년 매출 증가 중 미국 생산시설 인수가 기여한 금액을 정확히 계산해줘"
    )

    assert plan.mode is AnswerExecutionMode.HYBRID_GROUNDED
    assert RetrievalRail.SEMANTIC in plan.route.rails
