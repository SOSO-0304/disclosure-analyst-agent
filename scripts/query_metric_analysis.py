#!/usr/bin/env python3
"""Query multiple structured metrics and calculate deterministic comparisons/aggregations."""

from __future__ import annotations

import argparse
from decimal import Decimal

from disclosure_agent.domain.metrics import MetricName, MetricOperation, MetricTarget
from disclosure_agent.rendering.money import format_krw
from disclosure_agent.retrieval.company_resolver import resolve_company
from disclosure_agent.services.metric_analysis import MetricAnalysisService
from disclosure_agent.storage.database import get_engine, session_scope


def _parse_target(value: str) -> tuple[str, int]:
    try:
        company_name, raw_year = value.rsplit(":", 1)
        year = int(raw_year)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("target must use COMPANY:YEAR format") from exc
    if not company_name.strip():
        raise argparse.ArgumentTypeError("target company must not be empty")
    if year < 2000 or year > 2100:
        raise argparse.ArgumentTypeError("target year must be between 2000 and 2100")
    return company_name.strip(), year


def _display_decimal(value: Decimal | None, operation: MetricOperation) -> str:
    if value is None:
        return "unknown"
    if operation is MetricOperation.GROWTH_RATE:
        rendered = f"{value.quantize(Decimal('0.0001')):f}".rstrip("0").rstrip(".")
        return f"{rendered}%"
    if value == value.to_integral_value():
        return format_krw(int(value))
    return f"{value:,.2f}원"


def _formula(operation: MetricOperation) -> str:
    formulas = {
        MetricOperation.VALUES: "grounded values only",
        MetricOperation.SUM: "sum(values)",
        MetricOperation.AVERAGE: "sum(values) / count(values)",
        MetricOperation.DIFFERENCE: "abs(second - first)",
        MetricOperation.GROWTH_RATE: "(second - first) / first * 100",
        MetricOperation.RANKING: "sort values descending",
    }
    return formulas[operation]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--metric", choices=[item.value for item in MetricName], required=True)
    parser.add_argument(
        "--operation",
        choices=[item.value for item in MetricOperation],
        required=True,
    )
    parser.add_argument(
        "--target",
        action="append",
        type=_parse_target,
        required=True,
        help="Repeat COMPANY:YEAR, e.g. --target 삼성전자:2025",
    )
    args = parser.parse_args()

    metric = MetricName(args.metric)
    operation = MetricOperation(args.operation)
    engine = get_engine(args.database_url)

    with session_scope(engine) as session:
        targets = []
        for raw_company_name, year in args.target:
            company = resolve_company(session, raw_company_name)
            if company is None:
                raise SystemExit(f"Unknown company: {raw_company_name}")
            targets.append(MetricTarget(company_name=company.listed_name, year=year))

        result = MetricAnalysisService(session).analyze(
            metric=metric,
            targets=tuple(targets),
            operation=operation,
        )

    print("=== METRIC ANALYSIS ===")
    print(f"metric                          {result.metric.value}")
    print(f"operation                       {result.operation.value}")
    print(f"status                          {result.status}")
    print(f"formula                         {_formula(result.operation)}")
    print(f"target_count                    {len(result.observations)}")
    if result.reason is not None:
        print(f"reason                          {result.reason}")

    print("\n=== OBSERVATIONS ===")
    for index, observation in enumerate(result.observations, start=1):
        print(f"[{index}] {observation.target.company_name} {observation.target.year}")
        print(f"    status                      {observation.status}")
        print(f"    amount                      {format_krw(observation.amount_krw)}")
        print(f"    filing                      {observation.filing_id or '-'}")
        print(f"    fact                        {observation.fact_id or '-'}")
        print(f"    raw                         {observation.raw_value or '-'}")
        print(f"    unit                        {observation.resolved_unit or '-'}")

    if result.derived_value is not None:
        print("\n=== DERIVED RESULT ===")
        displayed = _display_decimal(result.derived_value, operation)
        print(f"value                           {displayed}")

    if result.ranking:
        print("\n=== RANKING ===")
        for item in result.ranking:
            observation = item.observation
            print(
                f"{item.position}. {observation.target.company_name} "
                f"({observation.target.year}) {format_krw(observation.amount_krw)}"
            )


if __name__ == "__main__":
    main()
