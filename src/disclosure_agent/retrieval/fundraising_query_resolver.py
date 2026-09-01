"""Resolve one company and one calendar year from fundraising questions."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from disclosure_agent.retrieval.company_resolver import CompanyIdentity, resolve_company
from disclosure_agent.retrieval.metric_target_resolver import (
    extract_query_years,
    has_company_placeholders,
    match_company_mentions,
)
from disclosure_agent.storage.db_models import SourceCompanyRow


@dataclass(frozen=True, slots=True)
class FundraisingQueryTarget:
    """Resolved company/year target for one fundraising aggregation question."""

    status: str
    company_name: str | None
    year: int | None
    reason: str | None = None


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


def resolve_fundraising_query_target(
    session: Session,
    *,
    query: str,
    fallback_company: str | None = None,
    fallback_year: int | None = None,
) -> FundraisingQueryTarget:
    """Resolve exactly one company and one year without guessing ambiguous questions."""

    contains_placeholders = has_company_placeholders(query)
    companies = list(match_company_mentions(query, _source_companies(session)))
    years = list(extract_query_years(query))

    if not companies and fallback_company and not contains_placeholders:
        resolved = resolve_company(session, fallback_company)
        if resolved is not None:
            companies.append(resolved)
    if not years and fallback_year is not None:
        years.append(fallback_year)

    if contains_placeholders and not companies:
        return FundraisingQueryTarget(
            status="UNRESOLVED",
            company_name=None,
            year=years[0] if len(years) == 1 else None,
            reason="company_placeholder_unresolved",
        )
    if not companies:
        return FundraisingQueryTarget(
            status="UNRESOLVED",
            company_name=None,
            year=years[0] if len(years) == 1 else None,
            reason="company_unresolved",
        )
    if len(companies) != 1:
        return FundraisingQueryTarget(
            status="AMBIGUOUS",
            company_name=None,
            year=years[0] if len(years) == 1 else None,
            reason="multiple_companies",
        )
    if not years:
        return FundraisingQueryTarget(
            status="UNRESOLVED",
            company_name=companies[0].listed_name,
            year=None,
            reason="year_unresolved",
        )
    if len(years) != 1:
        return FundraisingQueryTarget(
            status="AMBIGUOUS",
            company_name=companies[0].listed_name,
            year=None,
            reason="multiple_years",
        )

    return FundraisingQueryTarget(
        status="RESOLVED",
        company_name=companies[0].listed_name,
        year=years[0],
    )
