"""Service for materialising facility-investment typed events from generic facts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy.orm import Session

from disclosure_agent.extractors.facility_investment import extract_facility_investment
from disclosure_agent.storage.facility_investment_repository import FacilityInvestmentRepository


@dataclass(frozen=True, slots=True)
class FacilityInvestmentIngestionResult:
    """Counts and field coverage from one deterministic typed-event projection."""

    candidate_filings: int
    events: int
    evidence: int
    corrections: int
    coverage: dict[str, int]


def ingest_facility_investment_events(
    *,
    session: Session,
    expected_candidate_count: int | None = 43,
) -> FacilityInvestmentIngestionResult:
    """Project 신규시설투자등 filings already present in the source/fact database."""

    repository = FacilityInvestmentRepository(session)
    candidates = repository.read_candidates()
    if expected_candidate_count is not None and len(candidates) != expected_candidate_count:
        raise ValueError(
            "Facility-investment candidate count mismatch: "
            f"expected={expected_candidate_count}, actual={len(candidates)}"
        )

    coverage: Counter[str] = Counter()
    projections = []
    corrections = 0
    for candidate in candidates:
        if candidate.is_correction:
            corrections += 1
        extraction = extract_facility_investment(
            filing_id=candidate.filing_id,
            receipt_number=candidate.receipt_number,
            company_name=candidate.company_name,
            stock_code=candidate.stock_code,
            is_correction=candidate.is_correction,
            facts=candidate.facts,
        )
        for attribute in extraction.evidence:
            coverage[attribute] += 1
        projections.append((candidate, extraction.event, extraction.evidence))

    event_count, evidence_count = repository.replace_events(projections=projections)
    return FacilityInvestmentIngestionResult(
        candidate_filings=len(candidates),
        events=event_count,
        evidence=evidence_count,
        corrections=corrections,
        coverage=dict(sorted(coverage.items())),
    )
