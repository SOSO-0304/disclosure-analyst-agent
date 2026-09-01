"""Resolve company/year targets from natural-language metric analysis queries."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from disclosure_agent.domain.metrics import MetricOperation, MetricTarget
from disclosure_agent.retrieval.company_resolver import (
    CompanyIdentity,
    normalize_company_alias,
    resolve_company,
)
from disclosure_agent.storage.db_models import SourceCompanyRow

_YEAR_PATTERN = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


@dataclass(frozen=True, slots=True)
class MetricTargetResolution:
    """Conservative target resolution result for one metric query."""

    status: str
    targets: tuple[MetricTarget, ...]
    companies: tuple[str, ...]
    years: tuple[int, ...]
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _CompanyMention:
    company: CompanyIdentity
    start: int
    end: int
    alias_length: int


def _source_companies(session: Session) -> tuple[CompanyIdentity, ...]:
    rows = session.execute(
        select(
            SourceCompanyRow.corp_code,
            SourceCompanyRow.stock_code,
            SourceCompanyRow.listed_name,
            SourceCompanyRow.corp_name,
        ).order_by(SourceCompanyRow.corp_code)
    )
    return tuple(
        CompanyIdentity(
            corp_code=row.corp_code,
            stock_code=row.stock_code,
            listed_name=row.listed_name,
            corp_name=row.corp_name,
        )
        for row in rows
    )


def match_company_mentions(
    query: str,
    companies: tuple[CompanyIdentity, ...],
) -> tuple[CompanyIdentity, ...]:
    """Resolve non-overlapping company mentions, preferring the longest alias."""

    normalized_query = normalize_company_alias(query)
    mentions: list[_CompanyMention] = []

    for company in companies:
        aliases = {
            normalize_company_alias(company.listed_name),
            normalize_company_alias(company.corp_name),
        }
        aliases.discard("")
        for alias in aliases:
            start = 0
            while True:
                index = normalized_query.find(alias, start)
                if index < 0:
                    break
                mentions.append(
                    _CompanyMention(
                        company=company,
                        start=index,
                        end=index + len(alias),
                        alias_length=len(alias),
                    )
                )
                start = index + 1

    selected: list[_CompanyMention] = []
    occupied: list[tuple[int, int]] = []
    for mention in sorted(
        mentions,
        key=lambda item: (-item.alias_length, item.start, item.company.corp_code),
    ):
        overlaps = any(mention.start < end and start < mention.end for start, end in occupied)
        if overlaps:
            continue
        selected.append(mention)
        occupied.append((mention.start, mention.end))

    ordered = sorted(selected, key=lambda item: (item.start, item.end, item.company.corp_code))
    deduped: list[CompanyIdentity] = []
    seen_corp_codes: set[str] = set()
    for mention in ordered:
        if mention.company.corp_code in seen_corp_codes:
            continue
        seen_corp_codes.add(mention.company.corp_code)
        deduped.append(mention.company)
    return tuple(deduped)


def extract_query_years(query: str) -> tuple[int, ...]:
    """Extract unique four-digit years in first-appearance order."""

    years = [int(match.group(1)) for match in _YEAR_PATTERN.finditer(query)]
    return tuple(dict.fromkeys(years))


def resolve_metric_targets(
    session: Session,
    *,
    query: str,
    operation: MetricOperation,
    fallback_company: str | None = None,
    fallback_year: int | None = None,
) -> MetricTargetResolution:
    """Resolve targets conservatively without guessing multi-company/multi-year pairings."""

    source_companies = _source_companies(session)
    companies = list(match_company_mentions(query, source_companies))
    years = list(extract_query_years(query))

    if not companies and fallback_company:
        company = resolve_company(session, fallback_company)
        if company is not None:
            companies.append(company)
    if not years and fallback_year is not None:
        years.append(fallback_year)

    if operation is MetricOperation.RANKING and not companies and len(years) == 1:
        companies = list(source_companies)

    company_names = tuple(company.listed_name for company in companies)
    year_values = tuple(years)

    if not companies:
        return MetricTargetResolution(
            status="UNRESOLVED",
            targets=(),
            companies=company_names,
            years=year_values,
            reason="company_unresolved",
        )
    if not years:
        return MetricTargetResolution(
            status="UNRESOLVED",
            targets=(),
            companies=company_names,
            years=year_values,
            reason="year_unresolved",
        )

    if len(companies) == 1:
        targets = tuple(
            MetricTarget(company_name=companies[0].listed_name, year=year) for year in years
        )
    elif len(years) == 1:
        targets = tuple(
            MetricTarget(company_name=company.listed_name, year=years[0]) for company in companies
        )
    else:
        return MetricTargetResolution(
            status="AMBIGUOUS",
            targets=(),
            companies=company_names,
            years=year_values,
            reason="multiple_companies_and_multiple_years",
        )

    return MetricTargetResolution(
        status="RESOLVED",
        targets=targets,
        companies=company_names,
        years=year_values,
    )
