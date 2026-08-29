"""Resolve Supply Contract correction lineage using explicit disclosure dates.

The resolver is intentionally conservative.  A correction is linked only when
``2. 정정관련 공시서류제출일`` identifies exactly one filing for the same
company inside the selected Supply Contract corpus.  Ambiguous or out-of-corpus
predecessors are surfaced as unresolved states instead of being guessed from
text similarity.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from disclosure_agent.domain.models import FilingPackage
from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader, SemanticField

RELATED_FILING_DATE_PATH = "2. 정정관련 공시서류제출일"


class LineageResolutionStatus(StrEnum):
    """Outcome of resolving one correction filing to its predecessor."""

    RESOLVED = "resolved"
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


@dataclass(frozen=True, slots=True)
class SupplyContractLineage:
    """Resolved correction links plus root lookup for in-corpus chains."""

    links: tuple[CorrectionLineageLink, ...]
    root_by_filing_id: dict[str, str]


def resolve_supply_contract_lineage(
    packages: Iterable[FilingPackage],
    *,
    reader: ExchangeFieldReader | None = None,
) -> SupplyContractLineage:
    """Resolve correction predecessor links without fuzzy matching.

    All supplied packages are indexed by ``(corp_code, receipt_date)``.  For a
    correction filing, the explicit related-filing submission date is read from
    the disclosure body.  Exactly one same-company package on that date yields
    a resolved predecessor; zero or multiple candidates remain unresolved.
    """

    package_list = list(packages)
    field_reader = reader or ExchangeFieldReader()
    by_company_date: dict[tuple[str, date], list[FilingPackage]] = defaultdict(list)
    by_filing_id = {package.filing_id: package for package in package_list}

    for package in package_list:
        by_company_date[(package.company.corp_code, package.filing.receipt_date)].append(package)

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

        if len(candidates) > 1:
            links.append(
                _link(
                    package,
                    related_date=related_date,
                    status=LineageResolutionStatus.AMBIGUOUS,
                    candidates=candidates,
                    evidence=evidence,
                )
            )
            continue

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

    root_by_filing_id = {
        filing_id: _find_root(filing_id, predecessor_by_filing_id, by_filing_id)
        for filing_id in by_filing_id
    }
    return SupplyContractLineage(links=tuple(links), root_by_filing_id=root_by_filing_id)


def _find_related_date_field(fields: Iterable[SemanticField]) -> SemanticField | None:
    for field in fields:
        if field.path_key == RELATED_FILING_DATE_PATH:
            return field
    return None


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    text = value.strip().replace(".", "-").replace("/", "-")
    if not text or text == "-":
        return None
    try:
        return date.fromisoformat(text)
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
