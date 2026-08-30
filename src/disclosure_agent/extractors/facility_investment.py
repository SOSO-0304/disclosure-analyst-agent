"""Extract typed facility-investment events from persisted generic facts.

Unlike the legacy Supply Contract slice, this extractor deliberately consumes the
full-corpus generic fact layer. That lets typed-event expansion run without
re-reading the 27GB canonical JSONL snapshot.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from disclosure_agent.domain.events import FacilityInvestmentEvent
from disclosure_agent.facts.generic import GenericFact

FACILITY_INVESTMENT_SUBTYPE = "신규시설투자등"


@dataclass(frozen=True, slots=True)
class FacilityInvestmentExtraction:
    """One typed event and the generic facts that support each attribute."""

    event: FacilityInvestmentEvent
    evidence: dict[str, GenericFact]


ALIASES: dict[str, tuple[tuple[str, ...], ...]] = {
    "investment_type": (("투자구분",),),
    "investment_subject": (("투자대상",), ("투자명",)),
    "investment_amount_krw": (("투자금액(원)",), ("투자금액",)),
    "equity_krw": (("자기자본(원)",), ("자기자본금액",), ("자기자본",)),
    "equity_ratio": (("자기자본대비(%)",), ("자기자본대비",)),
    "purpose": (("투자목적",),),
    "investment_start_date": (("투자기간", "시작일"),),
    "investment_end_date": (("투자기간", "종료일"),),
    "decision_date": (("이사회결의일(결정일)",), ("이사회결의일",), ("결정일",)),
    "defer_reason": (("공시유보관련내용", "유보사유"),),
    "defer_until": (("공시유보관련내용", "유보기한"),),
    "notes": (("기타투자판단에참고할사항",),),
}

_MISSING = {"", "-", "해당없음", "해당 없음", "없음"}
_DATE_PATTERN = re.compile(
    r"(?P<year>20\d{2})\s*[./-]\s*(?P<month>\d{1,2})\s*[./-]\s*(?P<day>\d{1,2})"
)
_NUMBER_PATTERN = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_LEADING_NUMBER = re.compile(r"^\s*\d+(?:[-.]\d+)*\s*[.)]?\s*")


def extract_facility_investment(
    *,
    filing_id: str,
    receipt_number: str,
    company_name: str,
    stock_code: str,
    is_correction: bool,
    facts: Iterable[GenericFact],
) -> FacilityInvestmentExtraction:
    """Extract one 신규시설투자등 event from facts belonging to a single filing."""

    fact_list = tuple(facts)
    chosen: dict[str, GenericFact] = {}
    for attribute, alias_groups in ALIASES.items():
        match = _first_matching_fact(fact_list, alias_groups)
        if match is not None:
            chosen[attribute] = match

    event = FacilityInvestmentEvent(
        filing_id=filing_id,
        receipt_number=receipt_number,
        company_name=company_name,
        stock_code=stock_code,
        is_correction=is_correction,
        investment_type=_text(chosen.get("investment_type")),
        investment_subject=_text(chosen.get("investment_subject")),
        investment_amount_krw=_parse_int(chosen.get("investment_amount_krw")),
        equity_krw=_parse_int(chosen.get("equity_krw")),
        equity_ratio=_parse_decimal(chosen.get("equity_ratio")),
        purpose=_text(chosen.get("purpose")),
        investment_start_date=_parse_date(chosen.get("investment_start_date")),
        investment_end_date=_parse_date(chosen.get("investment_end_date")),
        decision_date=_parse_date(chosen.get("decision_date")),
        defer_reason=_text(chosen.get("defer_reason")),
        defer_until=_parse_date(chosen.get("defer_until")),
        notes=_text(chosen.get("notes")),
    )
    return FacilityInvestmentExtraction(event=event, evidence=chosen)


def _first_matching_fact(
    facts: Iterable[GenericFact],
    alias_groups: tuple[tuple[str, ...], ...],
) -> GenericFact | None:
    ranked: list[tuple[int, int, int, GenericFact]] = []
    for fact in facts:
        if _is_missing(fact.value_text):
            continue
        label_parts = _label_parts(fact)
        compact_parts = tuple(_compact_label(part) for part in label_parts)
        for alias_order, aliases in enumerate(alias_groups):
            normalized_aliases = tuple(_compact_label(alias) for alias in aliases)
            score = _match_score(compact_parts, normalized_aliases)
            if score is None:
                continue
            direct_bonus = 1 if fact.label_text else 0
            ranked.append((score + direct_bonus, -alias_order, -fact.row_index, fact))
            break
    if not ranked:
        return None
    return max(ranked, key=lambda item: (item[0], item[1], item[2]))[3]


def _match_score(parts: tuple[str, ...], aliases: tuple[str, ...]) -> int | None:
    if not parts:
        return None
    if len(aliases) == 1:
        alias = aliases[0]
        if parts[-1] == alias:
            return 5
        if alias in parts:
            return 4
        if parts[-1].startswith(alias) or parts[-1].endswith(alias):
            return 3
        return None

    position = 0
    for alias in aliases:
        matched_index = next(
            (
                index
                for index in range(position, len(parts))
                if parts[index] == alias
                or parts[index].startswith(alias)
                or parts[index].endswith(alias)
            ),
            None,
        )
        if matched_index is None:
            return None
        position = matched_index + 1
    if parts[-1] == aliases[-1] or parts[-1].startswith(aliases[-1]):
        return 6
    return 5


def _label_parts(fact: GenericFact) -> tuple[str, ...]:
    if fact.label_text:
        parts = tuple(part.strip() for part in fact.label_text.split(">") if part.strip())
        if parts:
            return parts
    path_parts: list[str] = []
    for segment in fact.path_text.split("|"):
        path_parts.extend(part.strip() for part in segment.split(">") if part.strip())
    return tuple(path_parts)


def _compact_label(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).strip()
    text = _LEADING_NUMBER.sub("", text)
    text = text.replace("ㆍ", "").replace("·", "")
    return re.sub(r"[\s()（）%％:：_\-/.,]", "", text)


def _is_missing(value: str | None) -> bool:
    return value is None or value.strip() in _MISSING


def _text(fact: GenericFact | None) -> str | None:
    if fact is None or _is_missing(fact.value_text):
        return None
    return fact.value_text.strip()


def _parse_int(fact: GenericFact | None) -> int | None:
    if fact is None:
        return None
    if fact.numeric_value is not None:
        integral = fact.numeric_value.to_integral_value()
        if integral == fact.numeric_value:
            return int(integral)
    text = _text(fact)
    if text is None:
        return None
    match = _NUMBER_PATTERN.search(text.replace("원", ""))
    if match is None:
        return None
    try:
        value = Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None
    integral = value.to_integral_value()
    return int(integral) if integral == value else None


def _parse_decimal(fact: GenericFact | None) -> Decimal | None:
    if fact is None:
        return None
    if fact.numeric_value is not None:
        return fact.numeric_value
    text = _text(fact)
    if text is None:
        return None
    match = _NUMBER_PATTERN.search(text)
    if match is None:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None


def _parse_date(fact: GenericFact | None) -> date | None:
    text = _text(fact)
    if text is None:
        return None
    match = _DATE_PATTERN.search(text)
    if match is None:
        return None
    try:
        return date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return None
