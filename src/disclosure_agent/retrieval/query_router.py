"""Deterministic routing across structured SQL and semantic retrieval rails."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class RetrievalRail(StrEnum):
    """Supported retrieval rails."""

    SEMANTIC = "semantic"
    REVENUE = "revenue"
    FUNDRAISING = "fundraising"
    FACILITY_INVESTMENT = "facility_investment"
    SUPPLY_CONTRACT = "supply_contract"


@dataclass(frozen=True, slots=True)
class QueryRoute:
    """Deterministic retrieval plan for one user query."""

    rails: tuple[RetrievalRail, ...]
    matched_terms: tuple[str, ...]

    @property
    def uses_semantic(self) -> bool:
        return RetrievalRail.SEMANTIC in self.rails


_REVENUE_TERMS = ("매출액", "영업수익", "연결매출")
_FUNDRAISING_TERMS = (
    "자금조달",
    "유상증자",
    "전환사채",
    "신주인수권부사채",
    "교환사채",
)
_FUNDRAISING_ABBREVIATIONS = ("CB", "BW", "EB")
_FACILITY_TERMS = (
    "시설투자",
    "설비투자",
    "CAPEX",
    "투자계획",
    "투자 계획",
    "투자목적",
    "투자 목적",
)
_CONTRACT_TERMS = (
    "공급계약",
    "공급 계약",
    "단일판매",
    "판매공급계약",
    "계약해지",
    "계약 해지",
    "해지된 계약",
    "해지 계약",
    "계약이 해지",
    "계약을 해지",
)
_NARRATIVE_TERMS = (
    "이유",
    "배경",
    "목적",
    "계획",
    "전략",
    "전망",
    "영향",
    "변화",
    "변경",
)


def _normalize(query: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", query)).strip()


def route_query(query: str) -> QueryRoute:
    """Route a query without using an LLM or external knowledge."""

    normalized = _normalize(query)
    upper = normalized.upper()
    rails: list[RetrievalRail] = []
    matched_terms: list[str] = []

    def add_rail(rail: RetrievalRail, terms: tuple[str, ...]) -> None:
        matched = [term for term in terms if term in normalized]
        if matched:
            rails.append(rail)
            matched_terms.extend(matched)

    add_rail(RetrievalRail.REVENUE, _REVENUE_TERMS)
    add_rail(RetrievalRail.FUNDRAISING, _FUNDRAISING_TERMS)
    add_rail(RetrievalRail.FACILITY_INVESTMENT, _FACILITY_TERMS)
    add_rail(RetrievalRail.SUPPLY_CONTRACT, _CONTRACT_TERMS)

    abbreviations = tuple(
        abbreviation
        for abbreviation in _FUNDRAISING_ABBREVIATIONS
        if re.search(rf"(?<![A-Z]){abbreviation}(?![A-Z])", upper)
    )
    if abbreviations and RetrievalRail.FUNDRAISING not in rails:
        rails.append(RetrievalRail.FUNDRAISING)
        matched_terms.extend(abbreviations)

    narrative_matches = [term for term in _NARRATIVE_TERMS if term in normalized]
    closed_supply_termination_reason = (
        RetrievalRail.SUPPLY_CONTRACT in rails
        and "해지" in normalized
        and set(narrative_matches).issubset({"이유"})
    )
    narrative = bool(narrative_matches) and not closed_supply_termination_reason
    needs_semantic = not rails or narrative or RetrievalRail.FACILITY_INVESTMENT in rails
    if needs_semantic:
        rails.append(RetrievalRail.SEMANTIC)

    deduped_rails = tuple(dict.fromkeys(rails))
    deduped_terms = tuple(dict.fromkeys(matched_terms))
    return QueryRoute(rails=deduped_rails, matched_terms=deduped_terms)
