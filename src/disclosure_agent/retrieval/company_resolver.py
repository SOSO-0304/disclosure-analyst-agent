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


def source_companies(session: Session) -> tuple[CompanyIdentity, ...]:
    """Return the persisted source-company master in deterministic order."""

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
    mentions: list[tuple[int, int, int, CompanyIdentity]] = []

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
                mentions.append((index, index + len(alias), len(alias), company))
                start = index + 1

    selected: list[tuple[int, int, int, CompanyIdentity]] = []
    occupied: list[tuple[int, int]] = []
    for mention in sorted(
        mentions,
        key=lambda item: (-item[2], item[0], item[3].corp_code),
    ):
        start, end, _, _ = mention
        overlaps = any(
            start < occupied_end and occupied_start < end
            for occupied_start, occupied_end in occupied
        )
        if overlaps:
            continue
        selected.append(mention)
        occupied.append((start, end))

    ordered = sorted(selected, key=lambda item: (item[0], item[1], item[3].corp_code))
    deduped: list[CompanyIdentity] = []
    seen_corp_codes: set[str] = set()
    for _, _, _, company in ordered:
        if company.corp_code in seen_corp_codes:
            continue
        seen_corp_codes.add(company.corp_code)
        deduped.append(company)
    return tuple(deduped)


def match_query_companies(session: Session, query: str) -> tuple[CompanyIdentity, ...]:
    """Resolve company names mentioned inside a natural-language query."""

    return match_company_mentions(query, source_companies(session))


def resolve_company(session: Session, value: str) -> CompanyIdentity | None:
    """Resolve one company alias against the persisted source master."""

    return match_company_alias(value, source_companies(session))
