"""Resolve Supply Contract termination filings to in-corpus formation chains."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from disclosure_agent.domain.models import FilingPackage
from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader, SemanticField
from disclosure_agent.extractors.supply_contract import extract_supply_contract
from disclosure_agent.extractors.supply_contract_lineage import (
    SupplyContractLineage,
    resolve_supply_contract_lineage,
)
from disclosure_agent.extractors.supply_contract_termination import (
    SupplyContractTerminationExtraction,
    extract_supply_contract_termination,
)

FORMATION_SUBTYPE = "단일판매공급계약체결"
TERMINATION_SUBTYPE = "단일판매공급계약해지"
RELATED_DISCLOSURE_DATE_PATTERN = re.compile(r"(?P<date>\d{4}-\d{1,2}-\d{1,2})")
FORMATION_TEXT_PATTERN = re.compile(r"단일판매\s*[ㆍ·]?\s*공급계약\s*체결")
MATCH_FIELDS = (
    ("contract_name", "contract_name"),
    ("counterparty", "counterparty"),
    ("contract_start_date", "contract_start_date"),
    ("contract_end_date", "contract_end_date"),
    ("termination_type", "contract_type"),
)
MIN_FINGERPRINT_MATCHES = 2


class TerminationLineageStatus(StrEnum):
    """Outcome of resolving a termination filing to a formation chain."""

    RESOLVED = "resolved"
    RESOLVED_BY_FINGERPRINT = "resolved_by_fingerprint"
    NO_FORMATION_DATE = "no_formation_date"
    NO_IN_CORPUS_CANDIDATE = "no_in_corpus_candidate"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class TerminationLineageLink:
    """One termination filing and its resolved in-corpus formation chain."""

    termination_filing_id: str
    termination_receipt_number: str
    corp_code: str
    related_formation_dates: tuple[date, ...]
    matched_formation_filing_id: str | None
    matched_formation_receipt_number: str | None
    root_formation_filing_id: str | None
    status: TerminationLineageStatus
    candidate_filing_ids: tuple[str, ...]
    related_disclosures_evidence: SemanticField | None
    fingerprint_score: int | None = None
    fingerprint_compared: int | None = None


@dataclass(frozen=True, slots=True)
class SupplyContractTerminationLineage:
    """Resolved termination links and formation correction lineage used by them."""

    links: tuple[TerminationLineageLink, ...]
    formation_lineage: SupplyContractLineage


def resolve_supply_contract_termination_lineage(
    packages: Iterable[FilingPackage],
    *,
    reader: ExchangeFieldReader | None = None,
) -> SupplyContractTerminationLineage:
    """Resolve terminations using explicit related-disclosure dates, then fingerprints."""

    package_list = list(packages)
    field_reader = reader or ExchangeFieldReader()
    formations = [
        package
        for package in package_list
        if package.filing.document_subtype == FORMATION_SUBTYPE
    ]
    terminations = [
        package
        for package in package_list
        if package.filing.document_subtype == TERMINATION_SUBTYPE
    ]

    formation_lineage = resolve_supply_contract_lineage(formations, reader=field_reader)
    formation_events = {
        package.filing_id: extract_supply_contract(package, reader=field_reader).event
        for package in formations
    }
    formation_by_company_date: dict[tuple[str, date], list[FilingPackage]] = defaultdict(list)
    for package in formations:
        formation_by_company_date[(package.company.corp_code, package.filing.receipt_date)].append(
            package
        )

    links: list[TerminationLineageLink] = []
    for package in terminations:
        extraction = extract_supply_contract_termination(package, reader=field_reader)
        evidence = extraction.evidence.get("related_disclosures")
        related_dates = _extract_formation_dates(
            extraction.event.related_disclosures,
            extraction.event.notes,
        )

        if not related_dates:
            links.append(
                _link(
                    package,
                    related_dates=(),
                    status=TerminationLineageStatus.NO_FORMATION_DATE,
                    evidence=evidence,
                )
            )
            continue

        candidates = _collect_candidates(
            package,
            related_dates,
            formation_by_company_date=formation_by_company_date,
        )
        if not candidates:
            links.append(
                _link(
                    package,
                    related_dates=related_dates,
                    status=TerminationLineageStatus.NO_IN_CORPUS_CANDIDATE,
                    evidence=evidence,
                )
            )
            continue

        if len(candidates) == 1:
            predecessor = candidates[0]
            links.append(
                _link(
                    package,
                    related_dates=related_dates,
                    status=TerminationLineageStatus.RESOLVED,
                    predecessor=predecessor,
                    root_id=formation_lineage.root_by_filing_id.get(predecessor.filing_id),
                    candidates=candidates,
                    evidence=evidence,
                )
            )
            continue

        resolved = _resolve_by_fingerprint(
            extraction,
            candidates,
            formation_events=formation_events,
        )
        if resolved is None:
            links.append(
                _link(
                    package,
                    related_dates=related_dates,
                    status=TerminationLineageStatus.AMBIGUOUS,
                    candidates=candidates,
                    evidence=evidence,
                )
            )
            continue

        predecessor, score, compared = resolved
        links.append(
            _link(
                package,
                related_dates=related_dates,
                status=TerminationLineageStatus.RESOLVED_BY_FINGERPRINT,
                predecessor=predecessor,
                root_id=formation_lineage.root_by_filing_id.get(predecessor.filing_id),
                candidates=candidates,
                evidence=evidence,
                fingerprint_score=score,
                fingerprint_compared=compared,
            )
        )

    return SupplyContractTerminationLineage(
        links=tuple(links),
        formation_lineage=formation_lineage,
    )


def _extract_formation_dates(
    related_disclosures: str | None,
    notes: str | None,
) -> tuple[date, ...]:
    dates: list[date] = []
    if related_disclosures:
        matches = list(RELATED_DISCLOSURE_DATE_PATTERN.finditer(related_disclosures))
        for index, match in enumerate(matches):
            segment_end = matches[index + 1].start() if index + 1 < len(matches) else len(
                related_disclosures
            )
            segment = related_disclosures[match.start() : segment_end]
            if FORMATION_TEXT_PATTERN.search(segment):
                parsed = _parse_iso_date(match.group("date"))
                if parsed is not None:
                    dates.append(parsed)

    if not dates and notes:
        for raw in re.findall(r"\d{4}[.-]\d{1,2}[.-]\d{1,2}", notes):
            nearby = notes[max(0, notes.find(raw) - 40) : notes.find(raw) + len(raw) + 80]
            if "공시" not in nearby and "수주" not in nearby and "계약" not in nearby:
                continue
            parsed = _parse_iso_date(raw.replace(".", "-"))
            if parsed is not None:
                dates.append(parsed)

    return tuple(dict.fromkeys(dates))


def _parse_iso_date(value: str) -> date | None:
    parts = value.split("-")
    if len(parts) != 3:
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _collect_candidates(
    termination: FilingPackage,
    related_dates: tuple[date, ...],
    *,
    formation_by_company_date: dict[tuple[str, date], list[FilingPackage]],
) -> list[FilingPackage]:
    candidates: list[FilingPackage] = []
    seen: set[str] = set()
    for related_date in related_dates:
        for candidate in formation_by_company_date.get(
            (termination.company.corp_code, related_date),
            [],
        ):
            if candidate.filing.receipt_number >= termination.filing.receipt_number:
                continue
            if candidate.filing_id in seen:
                continue
            seen.add(candidate.filing_id)
            candidates.append(candidate)
    candidates.sort(key=lambda item: (item.filing.receipt_date, item.filing.receipt_number))
    return candidates


def _resolve_by_fingerprint(
    termination: SupplyContractTerminationExtraction,
    candidates: list[FilingPackage],
    *,
    formation_events: dict[str, object],
) -> tuple[FilingPackage, int, int] | None:
    ranked: list[tuple[int, int, str, FilingPackage]] = []
    for candidate in candidates:
        formation = formation_events[candidate.filing_id]
        score = 0
        compared = 0
        for termination_field, formation_field in MATCH_FIELDS:
            current = _normalise(getattr(termination.event, termination_field, None))
            previous = _normalise(getattr(formation, formation_field, None))
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
    peers = [item for item in ranked if item[0] == best_score and item[1] == best_compared]
    if len(peers) != 1:
        return None
    return best_candidate, best_score, best_compared


def _normalise(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = "".join(str(value).split()).lower()
    return text or None


def _link(
    package: FilingPackage,
    *,
    related_dates: tuple[date, ...],
    status: TerminationLineageStatus,
    evidence: SemanticField | None,
    predecessor: FilingPackage | None = None,
    root_id: str | None = None,
    candidates: Iterable[FilingPackage] = (),
    fingerprint_score: int | None = None,
    fingerprint_compared: int | None = None,
) -> TerminationLineageLink:
    return TerminationLineageLink(
        termination_filing_id=package.filing_id,
        termination_receipt_number=package.filing.receipt_number,
        corp_code=package.company.corp_code,
        related_formation_dates=related_dates,
        matched_formation_filing_id=predecessor.filing_id if predecessor else None,
        matched_formation_receipt_number=(
            predecessor.filing.receipt_number if predecessor else None
        ),
        root_formation_filing_id=root_id,
        status=status,
        candidate_filing_ids=tuple(candidate.filing_id for candidate in candidates),
        related_disclosures_evidence=evidence,
        fingerprint_score=fingerprint_score,
        fingerprint_compared=fingerprint_compared,
    )
