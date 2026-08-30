import pytest

from disclosure_agent.retrieval.company_resolver import (
    CompanyIdentity,
    match_company_alias,
    normalize_company_alias,
)


COMPANIES = (
    CompanyIdentity(
        corp_code="00261443",
        stock_code="036570",
        listed_name="엔씨소프트",
        corp_name="NC",
    ),
    CompanyIdentity(
        corp_code="00503668",
        stock_code="079550",
        listed_name="LIG넥스원",
        corp_name="LIG디펜스앤에어로스페이스",
    ),
    CompanyIdentity(
        corp_code="00105855",
        stock_code="010120",
        listed_name="LS ELECTRIC",
        corp_name="엘에스일렉트릭",
    ),
)


def test_normalize_company_alias_ignores_spacing_and_case() -> None:
    assert normalize_company_alias(" LIG 넥스원 ") == normalize_company_alias("LIG넥스원")
    assert normalize_company_alias("ls electric") == normalize_company_alias("LS ELECTRIC")


@pytest.mark.parametrize(
    ("alias", "expected"),
    (
        ("엔씨소프트", "엔씨소프트"),
        ("NC", "엔씨소프트"),
        ("LIG 넥스원", "LIG넥스원"),
        ("LIG디펜스앤에어로스페이스", "LIG넥스원"),
        ("엘에스일렉트릭", "LS ELECTRIC"),
        ("010120", "LS ELECTRIC"),
    ),
)
def test_match_company_alias_resolves_listed_legal_and_stock_names(
    alias: str,
    expected: str,
) -> None:
    result = match_company_alias(alias, COMPANIES)

    assert result is not None
    assert result.listed_name == expected


def test_match_company_alias_returns_none_for_unknown_company() -> None:
    assert match_company_alias("없는회사", COMPANIES) is None
