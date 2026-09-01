from disclosure_agent.rendering.money import format_krw


def test_format_krw_uses_korean_large_units() -> None:
    assert format_krw(333_605_938_000_000) == "333조 6,059억 3,800만 원"
    assert format_krw(186_254_472_000_000) == "186조 2,544억 7,200만 원"
    assert format_krw(8_099_147_815_086) == "8조 991억 4,781만 5,086 원"


def test_format_krw_handles_boundaries() -> None:
    assert format_krw(None) == "확인되지 않음"
    assert format_krw(0) == "0원"
    assert format_krw(9_999) == "9,999 원"
    assert format_krw(10_000) == "1만 원"
    assert format_krw(100_000_000) == "1억 원"
    assert format_krw(-10_000) == "-1만 원"
