from disclosure_agent.retrieval.company_resolver import CompanyIdentity
from disclosure_agent.retrieval.metric_target_resolver import (
    extract_query_years,
    match_company_mentions,
)


def _company(corp_code: str, listed_name: str, corp_name: str) -> CompanyIdentity:
    return CompanyIdentity(
        corp_code=corp_code,
        stock_code=None,
        listed_name=listed_name,
        corp_name=corp_name,
    )


def test_company_mentions_preserve_query_order() -> None:
    companies = (
        _company("1", "삼성전자", "삼성전자"),
        _company("2", "현대차", "현대자동차"),
        _company("3", "카카오", "카카오"),
    )

    matched = match_company_mentions(
        "삼성전자, 현대차, 카카오의 2025년 매출 합계는?",
        companies,
    )

    assert [company.listed_name for company in matched] == ["삼성전자", "현대차", "카카오"]


def test_longer_overlapping_company_alias_wins() -> None:
    companies = (
        _company("1", "카카오", "카카오"),
        _company("2", "카카오뱅크", "카카오뱅크"),
    )

    matched = match_company_mentions("카카오뱅크의 2025년 매출액은?", companies)

    assert [company.listed_name for company in matched] == ["카카오뱅크"]


def test_legal_name_can_resolve_to_listed_name() -> None:
    companies = (_company("1", "현대차", "현대자동차"),)

    matched = match_company_mentions("현대자동차의 2025년 매출액을 알려줘", companies)

    assert [company.listed_name for company in matched] == ["현대차"]


def test_years_are_extracted_in_query_order_without_duplicates() -> None:
    years = extract_query_years("2024년과 2025년을 비교하고 2024년도 다시 보여줘")

    assert years == (2024, 2025)


def test_years_are_not_extracted_from_longer_digit_sequences() -> None:
    years = extract_query_years("접수번호 120251과 2025년 공시를 확인해줘")

    assert years == (2025,)
