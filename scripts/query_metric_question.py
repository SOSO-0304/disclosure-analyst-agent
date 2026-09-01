#!/usr/bin/env python3
"""Plan and execute one natural-language structured metric analysis query."""

from __future__ import annotations

import argparse
from decimal import Decimal

from disclosure_agent.domain.metrics import MetricOperation
from disclosure_agent.rendering.money import format_krw
from disclosure_agent.retrieval.metric_query_planner import plan_metric_query
from disclosure_agent.retrieval.metric_target_resolver import resolve_metric_targets
from disclosure_agent.services.metric_analysis import MetricAnalysisService
from disclosure_agent.storage.database import get_engine, session_scope


def _display_decimal(value: Decimal | None, operation: MetricOperation) -> str:
    if value is None:
        return "unknown"
    if operation is MetricOperation.GROWTH_RATE:
        rendered = f"{value.quantize(Decimal('0.0001')):f}".rstrip("0").rstrip(".")
        return f"{rendered}%"
    if value == value.to_integral_value():
        return format_krw(int(value))
    return f"{value:,.2f}원"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--query", required=True)
    parser.add_argument("--company", help="Fallback company when the query omits one")
    parser.add_argument("--year", type=int, help="Fallback year when the query omits one")
    args = parser.parse_args()

    intent = plan_metric_query(args.query)
    print("=== METRIC QUERY PLAN ===")
    print(f"query                           {args.query}")
    print(f"metric                          {intent.metric.value if intent.metric else 'semantic'}")
    print(
        f"operation                       "
        f"{intent.operation.value if intent.operation else 'semantic'}"
    )
    print(f"matched_terms                   {','.join(intent.matched_terms) or '-'}")

    if intent.metric is None or intent.operation is None:
        print("execution                       semantic_fallback")
        return

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        resolution = resolve_metric_targets(
            session,
            query=args.query,
            operation=intent.operation,
            fallback_company=args.company,
            fallback_year=args.year,
        )
        print(f"target_resolution               {resolution.status}")
        if resolution.reason is not None:
            print(f"target_reason                   {resolution.reason}")
        if resolution.status != "RESOLVED":
            return

        result = MetricAnalysisService(session).analyze(
            metric=intent.metric,
            targets=resolution.targets,
            operation=intent.operation,
        )

    print("\n=== TARGETS ===")
    for index, target in enumerate(resolution.targets, start=1):
        print(f"[{index}] {target.company_name} {target.year}")

    print("\n=== OBSERVATIONS ===")
    for index, observation in enumerate(result.observations, start=1):
        print(f"[{index}] {observation.target.company_name} {observation.target.year}")
        print(f"    status                      {observation.status}")
        print(f"    amount                      {format_krw(observation.amount_krw)}")
        print(f"    sources                     {len(observation.sources)}")
        if observation.sources:
            for source_index, source in enumerate(observation.sources, start=1):
                print(f"    source[{source_index}] filing           {source.filing_id}")
                print(
                    f"    source[{source_index}] amount           "
                    f"{format_krw(source.amount_krw)}"
                )
                print(
                    f"    source[{source_index}] facts            "
                    f"{','.join(source.fact_ids) or '-'}"
                )
                print(
                    f"    source[{source_index}] events           "
                    f"{','.join(source.event_ids) or '-'}"
                )
                if source.description:
                    print(f"    source[{source_index}] description      {source.description}")
        else:
            print(f"    filing                      {observation.filing_id or '-'}")
            print(f"    fact                        {observation.fact_id or '-'}")
            print(f"    raw                         {observation.raw_value or '-'}")
            print(f"    unit                        {observation.resolved_unit or '-'}")

    print("\n=== ANALYSIS RESULT ===")
    print(f"status                          {result.status}")
    if result.reason is not None:
        print(f"reason                          {result.reason}")
    if result.derived_value is not None:
        print(f"value                           {_display_decimal(result.derived_value, result.operation)}")
    if result.ranking:
        for item in result.ranking:
            observation = item.observation
            print(
                f"{item.position}. {observation.target.company_name} "
                f"({observation.target.year}) {format_krw(observation.amount_krw)}"
            )


if __name__ == "__main__":
    main()
