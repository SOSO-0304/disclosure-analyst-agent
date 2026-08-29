"""Resolve Supply Contract correction lineage using explicit disclosure dates.

The resolver is conservative and deterministic:

1. use ``2. 정정관련 공시서류제출일`` to select earlier filings for the same
   company;
2. if that yields exactly one candidate, link it directly;
3. if several candidates remain, compare stable Supply Contract event fields and
   link only when one candidate has a unique best score with at least two
   comparable matching attributes;
4. if a fingerprint tie remains, compare correction-table ``정정전`` values
   against candidate event values and link only when that evidence yields a
   unique best candidate.

Out-of-corpus or still-ambiguous predecessors remain unresolved instead of being
guessed from free-text similarity.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from disclosure_agent.domain.models import FilingPackage
from disclosure_agent.extractors.exchange_corrections import (
    CorrectionRow,
    ExchangeCorrectionReader,
)
from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader, SemanticField
from disclosure_agent.extractors.supply_contract import extract_supply_contract

RELATED_FILING_DATE_PATH = "2. 정정관련 공시서류제출일"
MATCH_FIELDS = (
    "contract_name",
    "counterparty",
    "contract_date",
    "contract_type",
    "region",
    "contract_start_date",
)
MIN_FINGERPRINT_MATCHES = 2
KOREAN_DATE_PATTERN = re.compile(
    r"^\s*(?P<year>\d{4})\s*년\s*(?P<month>\d{1,2})\s*월\s*(?P<day>\d{1,2})\s*일\s*$"
)

CORRECTION_ITEM_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("contract_name", ("체결계약명", "판매공급계약내용", "세부내용")),
    ("contract_amount", ("계약금액", "확정계약금액", "계약금액총액")),
    ("recent_revenue", ("최근매출액",)),
    ("revenue_ratio", ("매출액대비",)),
    ("counterparty", ("계약상대", "계약상대방")),
    ("region", ("판매공급지역",)),
    ("contract_start_date", ("계약기간시작일", "시작일")),
    ("contract_end_date", ("계약기간종료일", "계약종료일", "종료일")),
    ("contract_date", ("계약수주일자", "계약수주일")),
)


class LineageResolutionStatus(StrEnum):
    """Outcome of resolving one correction filing to its predecessor."""

    RESOLVED = "resolved"
    RESOLVED_BY_FINGERPRINT = "resolved_by_fingerprint"
    RESOLVED_BY_CORRECTION_TABLE = "resolved_by_correction_table"
    MISSING_RELATED_DATE = "missing_related_date"
    NO_IN_CORPUS_CANDIDATE = "no_in_corpus_candidate"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class CorrectionLineageLink:
    """One correction filing and its conservatively resolved predecessor."""

    correction_filing_id: str
    correction_receipt_number: str
    corp_code: str
    related_filing_date: date | None
    predecessor_filing_id: str | None
    predecessor_receipt_number: str | None
    status: LineageResolutionStatus
    candidate_filing_ids: tuple[str, ...]
    evidence: SemanticField | None
    fingerprint_score: int | None = None
    fingerprint_compared: int | None = None
    correction_table_score: int | None = None
    correction_table_compared: int | None = None


@dataclass(frozen=True, slots=True)
class SupplyContractLineage:
    """Resolved correction links plus root lookup for in-corpus chains."""

    links: tuple[CorrectionLineageLink, ...]
    root_by_filing_id: dict[str, str]


def resolve_supply_contract_lineage(
    packages: Iterable[FilingPackage],
    *,
    reader: ExchangeFieldReader | None = None,
    correction_reader: ExchangeCorrectionReader | None = None,
) -> SupplyContractLineage:
    """Resolve correction predecessor links without free-text fuzzy matching."""

    package_list = list(packages)
    field_reader = reader or ExchangeFieldReader()
    table_reader = correction_reader or ExchangeCorrectionReader()
    by_company_date: dict[tuple[str, date], list[FilingPackage]] = defaultdict(list)
    by_filing_id = {package.filing_id: package for package in package_list}

    for package in package_list:
        by_company_date[(package.company.corp_code, package.filing.receipt_date)].append(package)

    event_by_filing_id = {
        package.filing_id: extract_supply_contract(package, reader=field_reader).event
        for package in package_list
    }

    links: list[CorrectionLineageLink] = []
    predecessor_by_filing_id: dict[str, str] = {}

    for package in package_list:
        if not package.correction.is_correction:
            continue

        evidence = _find_related_date_field(field_reader.read_package(package))
        related_date = _parse_date(evidence.value if evidence is not None else None)
        if related_date is None:
            links.append(
                _link(
                    package,
                    related_date=None,
                    status=LineageResolutionStatus.MISSING_RELATED_DATE,
                    evidence=evidence,
                )
            )
            continue

        candidates = [
            candidate
            for candidate in by_company_date.get((package.company.corp_code, related_date), [])
            if candidate.filing_id != package.filing_id
            and candidate.filing.receipt_number < package.filing.receipt_number
        ]
        candidates.sort(key=lambda item: (item.filing.receipt_number, item.filing_id))

        if not candidates:
            links.append(
                _link(
                    package,
                    related_date=related_date,
                    status=LineageResolutionStatus.NO_IN_CORPUS_CANDIDATE,
                    evidence=evidence,
                )
            )
            continue

        if len(candidates) == 1:
            predecessor = candidates[0]
            predecessor_by_filing_id[package.filing_id] = predecessor.filing_id
            links.append(
                _link(
                    package,
                    related_date=related_date,
                    status=LineageResolutionStatus.RESOLVED,
                    predecessor=predecessor,
                    candidates=candidates,
                    evidence=evidence,
                )
            )
            continue

        fingerprint = _resolve_by_fingerprint(
            package,
            candidates,
            event_by_filing_id=event_by_filing_id,
        )
        if fingerprint is not None:
            predecessor, score, compared = fingerprint
            predecessor_by_filing_id[package.filing_id] = predecessor.filing_id
            links.append(
                _link(
                    package,
                    related_date=related_date,
                    status=LineageResolutionStatus.RESOLVED_BY_FINGERPRINT,
                    predecessor=predecessor,
                    candidates=candidates,
                    evidence=evidence,
                    fingerprint_score=score,
                    fingerprint_compared=compared,
                )
            )
            continue

        correction_table = _resolve_by_correction_table(
            package,
            candidates,
            rows=table_reader.read_package(package),
            event_by_filing_id=event_by_filing_id,
        )
        if correction_table is not None:
            predecessor, score, compared = correction_table
            predecessor_by_filing_id[package.filing_id] = predecessor.filing_id
            links.append(
                _link(
                    package,
                    related_date=related_date,
                    status=LineageResolutionStatus.RESOLVED_BY_CORRECTION_TABLE,
                    predecessor=predecessor,
                    candidates=candidates,
                    evidence=evidence,
                    correction_table_score=score,
                    correction_table_compared=compared,
                )
            )
            continue

        links.append(
            _link(
                package,
                related_date=related_date,
                status=LineageResolutionStatus.AMBIGUOUS,
                candidates=candidates,
                evidence=evidence,
            )
        )

    root_by_filing_id = {
        filing_id: _find_root(filing_id, predecessor_by_filing_id, by_filing_id)
        for filing_id in by_filing_id
    }
    return SupplyContractLineage(links=tuple(links), root_by_filing_id=root_by_filing_id)


def _resolve_by_fingerprint(
    correction: FilingPackage,
    candidates: list[FilingPackage],
    *,
    event_by_filing_id: dict[str, Any],
) -> tuple[FilingPackage, int, int] | None:
    correction_event = event_by_filing_id[correction.filing_id]
    ranked: list[tuple[int, int, str, FilingPackage]] = []

    for candidate in candidates:
        candidate_event = event_by_filing_id[candidate.filing_id]
        score = 0
        compared = 0
        for field_name in MATCH_FIELDS:
            current = _normalise_match_value(getattr(correction_event, field_name, None))
            previous = _normalise_match_value(getattr(candidate_event, field_name, None))
            if current is None or previous is None:
                continue
            compared += 1
            if current == previous:
                score += 1
        ranked.append((score, compared, candidate.filing.receipt_number, candidate))

    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    best_score, best_compared, _, best_candidate = ranked[0]
    if best_score < MIN_FINGERPRINT_MATCHES:
        return None

    best_peers = [
        item
        for item in ranked
        if item[0] == best_score and item[1] == best_compared
    ]
    if len(best_peers) != 1:
        return None
    return best_candidate, best_score, best_compared


def _resolve_by_correction_table(
    correction: FilingPackage,
    candidates: list[FilingPackage],
    *,
    rows: Iterable[CorrectionRow],
    event_by_filing_id: dict[str, Any],
) -> tuple[FilingPackage, int, int] | None:
    del correction
    before_values = _correction_before_values(rows)
    if not before_values:
        return None

    ranked: list[tuple[int, int, str, FilingPackage]] = []
    for candidate in candidates:
        event = event_by_filing_id[candidate.filing_id]
        score = 0
        compared = 0
        for field_name, before in before_values.items():
            previous = _normalise_match_value(getattr(event, field_name, None))
            if previous is None or before is None:
                continue
            compared += 1
            if previous == before:
                score += 1
        ranked.append((score, compared, candidate.filing.receipt_number, candidate))

    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    best_score, best_compared, _, best_candidate = ranked[0]
    if best_score < 1:
        return None

    best_peers = [
        item
        for item in ranked
        if item[0] == best_score and item[1] == best_compared
    ]
    if len(best_peers) != 1:
        return None
    return best_candidate, best_score, best_compared


def _correction_before_values(rows: Iterable[CorrectionRow]) -> dict[str, str]:
    values: dict[str, str] = {}
    for row in rows:
        field_name = _correction_item_field(row.item)
        if field_name is None:
            continue
        before = _normalise_correction_value(field_name, row.before)
        if before is not None and field_name not in values:
            values[field_name] = before
    return values


def _correction_item_field(item: str) -> str | None:
    normalised = _normalise_label(item)
    for field_name, aliases in CORRECTION_ITEM_FIELDS:
        if any(_normalise_label(alias) in normalised for alias in aliases):
            return field_name
    return None


def _normalise_label(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", value.lower())


def _normalise_correction_value(field_name: str, value: str) -> str | None:
    text = value.strip()
    if not text or text in {"-", "해당없음", "해당 없음"}:
        return None

    if field_name in {"contract_start_date", "contract_end_date", "contract_date"}:
        parsed = _parse_date(text)
        return parsed.isoformat() if parsed is not None else None
    if field_name in {"contract_amount", "recent_revenue"}:
        cleaned = re.sub(r"[^0-9+-]", "", text)
        return cleaned.lstrip("+") or None
    if field_name == "revenue_ratio":
        cleaned = text.replace(",", "").replace("%", "").strip()
        return cleaned or None
    return _normalise_match_value(text)


def _normalise_match_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = "".join(str(value).split()).lower()
    return text or None


def _find_related_date_field(fields: Iterable[SemanticField]) -> SemanticField | None:
    for field in fields:
        if field.path_key == RELATED_FILING_DATE_PATH:
            return field
    return None


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None

    text = value.strip()
    if not text or text == "-":
        return None

    korean_match = KOREAN_DATE_PATTERN.fullmatch(text)
    if korean_match is not None:
        try:
            return date(
                int(korean_match.group("year")),
                int(korean_match.group("month")),
                int(korean_match.group("day")),
            )
        except ValueError:
            return None

    normalised = text.replace(".", "-").replace("/", "-")
    try:
        return date.fromisoformat(normalised)
    except ValueError:
        return None


def _link(
    package: FilingPackage,
    *,
    related_date: date | None,
    status: LineageResolutionStatus,
    predecessor: FilingPackage | None = None,
    candidates: Iterable[FilingPackage] = (),
    evidence: SemanticField | None,
    fingerprint_score: int | None = None,
    fingerprint_compared: int | None = None,
    correction_table_score: int | None = None,
    correction_table_compared: int | None = None,
) -> CorrectionLineageLink:
    return CorrectionLineageLink(
        correction_filing_id=package.filing_id,
        correction_receipt_number=package.filing.receipt_number,
        corp_code=package.company.corp_code,
        related_filing_date=related_date,
        predecessor_filing_id=predecessor.filing_id if predecessor is not None else None,
        predecessor_receipt_number=(
            predecessor.filing.receipt_number if predecessor is not None else None
        ),
        status=status,
        candidate_filing_ids=tuple(candidate.filing_id for candidate in candidates),
        evidence=evidence,
        fingerprint_score=fingerprint_score,
        fingerprint_compared=fingerprint_compared,
        correction_table_score=correction_table_score,
        correction_table_compared=correction_table_compared,
    )


def _find_root(
    filing_id: str,
    predecessor_by_filing_id: dict[str, str],
    by_filing_id: dict[str, FilingPackage],
) -> str:
    current = filing_id
    seen: set[str] = set()
    while current in predecessor_by_filing_id:
        if current in seen:
            raise ValueError(f"Correction lineage cycle detected at {current}")
        seen.add(current)
        predecessor = predecessor_by_filing_id[current]
        if predecessor not in by_filing_id:
            break
        current = predecessor
    return current
