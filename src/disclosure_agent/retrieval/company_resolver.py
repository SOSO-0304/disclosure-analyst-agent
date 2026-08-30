"""Resolve user-facing company aliases to the canonical listed company name."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from disclosure_agent.storage.db_models import SourceCompanyRow


@dataclass(frozen=True, slots=True)
class CompanyIdentity:
    """One company identity shared by listed and legal names."""

    corp_code: str
    stock_code: str | None
    listed_name: str
    corp_name: str


def normalize_company_alias(value: str) -> str:
    """Normalize harmless spacing and Unicode differences in a company alias."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(normalized.split())


def match_company_alias(
    value: str,
    companies: tuple[CompanyIdentity, ...],
) -> CompanyIdentity | None:
    """Return the unique exact alias match after normalization."""

    key = normalize_company_alias(value)
    if not key:
        return None

    matches = []
    for company in companies:
        aliases = {
            normalize_company_alias(company.listed_name),
            normalize_company_alias(company.corp_name),
        }
        if company.stock_code:
            aliases.add(normalize_company_alias(company.stock_code))
        if key in aliases:
            matches.append(company)

    if len(matches) > 1:
        names = ", ".join(company.listed_name for company in matches)
        raise ValueError(f"ambiguous company alias: {value!r} matches {names}")
    return matches[0] if matches else None


def resolve_company(session: Session, value: str) -> CompanyIdentity | None:
    """Resolve one company alias against the persisted 70-company source master."""

    rows = session.execute(
        select(
            SourceCompanyRow.corp_code,
            SourceCompanyRow.stock_code,
            SourceCompanyRow.listed_name,
            SourceCompanyRow.corp_name,
        ).order_by(SourceCompanyRow.corp_code)
    )
    companies = tuple(
        CompanyIdentity(
            corp_code=row.corp_code,
            stock_code=row.stock_code,
            listed_name=row.listed_name,
            corp_name=row.corp_name,
        )
        for row in rows
    )
    return match_company_alias(value, companies)
