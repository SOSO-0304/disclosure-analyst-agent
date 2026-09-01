from datetime import date

from disclosure_agent.extractors.fundraising import FundraisingInstrument
from disclosure_agent.services.fundraising_analysis import summarize_fundraising_events
from disclosure_agent.storage.fundraising_repository import FundraisingQueryResult


def _event(
    *,
    event_id: str,
    instrument_type: FundraisingInstrument,
    amount_krw: int | None,
    issue_date: date | None = None,
) -> FundraisingQueryResult:
    return FundraisingQueryResult(
        event_id=event_id,
        company_name="우리기술",
        instrument_type=instrument_type.value,
        issue_date=issue_date or date(2025, 1, 2),
        amount_krw=amount_krw,
        issuer_name="우리기술",
        security_name="제1회 사모 전환사채",
        series="1",
        issuance_method="사모",
        stock_kind=None,
        share_quantity=None,
        issue_price_krw=None,
        source_count=1,
        representative_filing_id=f"periodic_{event_id}",
        representative_table_id=f"table_{event_id}",
        representative_row_index=1,
    )


def test_summarizes_three_cb_events_and_keeps_empty_categories() -> None:
    events = (
        _event(
            event_id="cb1",
            instrument_type=FundraisingInstrument.CONVERTIBLE_BOND,
            amount_krw=10_000_000_000,
        ),
        _event(
            event_id="cb2",
            instrument_type=FundraisingInstrument.CONVERTIBLE_BOND,
            amount_krw=12_800_000_000,
        ),
        _event(
            event_id="cb3",
            instrument_type=FundraisingInstrument.CONVERTIBLE_BOND,
            amount_krw=15_000_000_000,
        ),
    )

    result = summarize_fundraising_events(
        company_name="우리기술",
        year=2025,
        events=events,
    )

    assert result.status == "ANSWERABLE"
    assert result.event_count == 3
    assert result.total_amount_krw == 37_800_000_000

    by_type = {category.instrument_type: category for category in result.categories}
    cb = by_type[FundraisingInstrument.CONVERTIBLE_BOND]
    assert cb.status == "ANSWERABLE"
    assert cb.event_count == 3
    assert cb.total_amount_krw == 37_800_000_000

    assert by_type[FundraisingInstrument.RIGHTS_ISSUE].status == "NO_MATCH"
    assert by_type[FundraisingInstrument.BOND_WITH_WARRANTS].status == "NO_MATCH"
    assert by_type[FundraisingInstrument.EXCHANGEABLE_BOND].status == "NO_MATCH"


def test_missing_event_amount_makes_category_and_total_partial() -> None:
    events = (
        _event(
            event_id="cb1",
            instrument_type=FundraisingInstrument.CONVERTIBLE_BOND,
            amount_krw=10_000_000_000,
        ),
        _event(
            event_id="cb2",
            instrument_type=FundraisingInstrument.CONVERTIBLE_BOND,
            amount_krw=None,
        ),
    )

    result = summarize_fundraising_events(
        company_name="우리기술",
        year=2025,
        events=events,
    )

    assert result.status == "PARTIAL"
    assert result.total_amount_krw is None
    assert result.known_amount_sum_krw == 10_000_000_000

    cb = next(
        category
        for category in result.categories
        if category.instrument_type is FundraisingInstrument.CONVERTIBLE_BOND
    )
    assert cb.status == "PARTIAL"
    assert cb.event_count == 2
    assert cb.known_amount_count == 1
    assert cb.missing_amount_count == 1
    assert cb.total_amount_krw is None
    assert cb.known_amount_sum_krw == 10_000_000_000


def test_no_events_is_no_match_instead_of_zero_amount() -> None:
    result = summarize_fundraising_events(
        company_name="우리기술",
        year=2025,
        events=(),
    )

    assert result.status == "NO_MATCH"
    assert result.event_count == 0
    assert result.total_amount_krw is None
    assert all(category.status == "NO_MATCH" for category in result.categories)
