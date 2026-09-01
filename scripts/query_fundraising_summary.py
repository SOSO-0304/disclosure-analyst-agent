#!/usr/bin/env python3
"""Query deterministic fundraising events and aggregate them by instrument type."""

from __future__ import annotations

import argparse

from disclosure_agent.domain.fundraising_analysis import FundraisingCategorySummary
from disclosure_agent.extractors.fundraising import FundraisingInstrument
from disclosure_agent.rendering.money import format_krw
from disclosure_agent.services.fundraising_analysis import FundraisingAnalysisService
from disclosure_agent.storage.database import get_engine, session_scope

INSTRUMENT_LABELS = {
    FundraisingInstrument.RIGHTS_ISSUE: "유상증자",
    FundraisingInstrument.CONVERTIBLE_BOND: "전환사채(CB)",
    FundraisingInstrument.BOND_WITH_WARRANTS: "신주인수권부사채(BW)",
    FundraisingInstrument.EXCHANGEABLE_BOND: "교환사채(EB)",
}


def _category_amount(category: FundraisingCategorySummary) -> str:
    if category.status == "NO_MATCH":
        return "확인된 이벤트 없음"
    if category.status == "PARTIAL":
        return (
            f"합계 확정 불가 (확인 금액 {format_krw(category.known_amount_sum_krw)}, "
            f"금액 미확인 {category.missing_amount_count}건)"
        )
    return format_krw(category.total_amount_krw)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--company", required=True)
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        result = FundraisingAnalysisService(session).analyze(
            company_name=args.company,
            year=args.year,
        )

    print("=== FUNDRAISING SUMMARY ===")
    print(f"company                         {result.company_name}")
    print(f"year                            {result.year}")
    print(f"status                          {result.status}")
    print(f"event_count                     {result.event_count}")
    print(f"known_amount_count              {result.known_amount_count}")
    print(f"missing_amount_count            {result.missing_amount_count}")
    if result.total_amount_krw is not None:
        print(f"total_amount                    {format_krw(result.total_amount_krw)}")
    elif result.event_count:
        print("total_amount                    확인 불가")

    print("\n=== BY TYPE ===")
    for category in result.categories:
        label = INSTRUMENT_LABELS[category.instrument_type]
        print(f"\n[{label}]")
        print(f"status                          {category.status}")
        print(f"events                          {category.event_count}")
        print(f"amount                          {_category_amount(category)}")
        for index, event in enumerate(category.events, start=1):
            amount = (
                format_krw(event.amount_krw)
                if event.amount_krw is not None
                else "확인되지 않음"
            )
            print(f"  event[{index}] id              {event.event_id}")
            print(f"  event[{index}] date            {event.issue_date.isoformat()}")
            print(f"  event[{index}] amount          {amount}")
            if event.security_name:
                print(f"  event[{index}] security        {event.security_name}")
            if event.series:
                print(f"  event[{index}] series          {event.series}")
            if event.issuance_method:
                print(f"  event[{index}] method          {event.issuance_method}")
            print(f"  event[{index}] filing          {event.representative_filing_id}")
            print(f"  event[{index}] sources         {event.source_count}")


if __name__ == "__main__":
    main()
