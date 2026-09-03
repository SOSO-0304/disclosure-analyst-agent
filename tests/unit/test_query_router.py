from disclosure_agent.retrieval.query_router import RetrievalRail, route_query


def test_revenue_query_uses_sql_only() -> None:
    route = route_query("카카오의 2025년 연결기준 매출액은 얼마인가?")

    assert route.rails == (RetrievalRail.REVENUE,)


def test_revenue_difference_query_uses_sql_only() -> None:
    route = route_query("삼성전자와 현대차의 2025년 연결기준 매출액 차이는 얼마인가?")

    assert route.rails == (RetrievalRail.REVENUE,)


def test_fundraising_query_uses_fundraising_sql() -> None:
    route = route_query("2025년 유상증자와 CB 자금조달 내역을 유형별로 정리해줘")

    assert route.rails == (RetrievalRail.FUNDRAISING,)


def test_facility_plan_query_keeps_semantic_rail() -> None:
    route = route_query("카카오의 주요 투자 계획과 투자 목적")

    assert route.rails == (
        RetrievalRail.FACILITY_INVESTMENT,
        RetrievalRail.SEMANTIC,
    )


def test_actual_investment_semantic_question_uses_facility_and_semantic_rails() -> None:
    route = route_query("한화오션은 2025년에 실제로 6,008억 원을 투자한 것으로 보면 돼?")

    assert route.rails == (
        RetrievalRail.FACILITY_INVESTMENT,
        RetrievalRail.SEMANTIC,
    )
    assert "실제 투자 의미 검증" in route.matched_terms


def test_terminated_contract_query_uses_contract_sql() -> None:
    route = route_query("2025년에 체결한 공급계약 중 이후 계약 해지된 건이 있는가?")

    assert route.rails == (RetrievalRail.SUPPLY_CONTRACT,)


def test_termination_reason_query_stays_on_contract_sql() -> None:
    route = route_query(
        "두산퓨얼셀의 2023년 연료전지 시스템 공급 계약은 언제 해지됐고 이유는 무엇이야?"
    )

    assert route.rails == (RetrievalRail.SUPPLY_CONTRACT,)


def test_natural_terminated_contract_phrase_uses_contract_sql() -> None:
    route = route_query(
        "두산퓨얼셀이 2023년에 체결한 주요 계약 중 이후 해지된 계약이 존재하는가?"
    )

    assert route.rails == (RetrievalRail.SUPPLY_CONTRACT,)
    assert "해지된 계약" in route.matched_terms


def test_business_change_query_falls_back_to_semantic() -> None:
    route = route_query("2023년과 2025년 사업보고서의 핵심 사업 변화를 비교해줘")

    assert route.rails == (RetrievalRail.SEMANTIC,)
