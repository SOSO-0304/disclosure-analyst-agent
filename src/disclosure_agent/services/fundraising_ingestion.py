"""Service for materialising canonical fundraising events from source-table grids."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy.orm import Session

from disclosure_agent.extractors.fundraising import (
    FundraisingInstrument,
    canonicalize_fundraising_occurrences,
    extract_fundraising_occurrences,
)
from disclosure_agent.storage.fundraising_repository import FundraisingRepository


@dataclass(frozen=True, slots=True)
class FundraisingIngestionResult:
    """Counts and field coverage from one deterministic fundraising projection."""

    target_sections: int
    candidate_tables: int
    source_occurrences: int
    canonical_events: int
    source_rows: int
    issue_date_coverage: int
    amount_coverage: int
    type_counts: dict[str, int]


def ingest_fundraising_events(
    *,
    session: Session,
    expected_candidate_tables: int | None = 720,
) -> FundraisingIngestionResult:
    """Extract, deduplicate, and persist the four supported fundraising instruments."""

    repository = FundraisingRepository(session)
    candidates = repository.read_candidates()
    candidate_count = len(candidates.tables)
    if expected_candidate_tables is not None and candidate_count != expected_candidate_tables:
        raise ValueError(
            "Fundraising candidate table count mismatch: "
            f"expected={expected_candidate_tables}, actual={candidate_count}"
        )

    occurrences = []
    for table in candidates.tables:
        occurrences.extend(
            extract_fundraising_occurrences(
                filing_id=table.filing_id,
                corp_code=table.corp_code,
                company_name=table.company_name,
                receipt_date=table.receipt_date,
                table_id=table.table_id,
                grid=table.grid,
                normalized_text=table.context_text,
            )
        )

    occurrence_tuple = tuple(occurrences)
    events = canonicalize_fundraising_occurrences(occurrence_tuple)
    event_count, source_count = repository.replace_events(
        events=events,
        occurrences=occurrence_tuple,
    )
    type_counts = Counter(event.occurrence.instrument_type.value for event in events)
    for instrument in FundraisingInstrument:
        type_counts.setdefault(instrument.value, 0)

    return FundraisingIngestionResult(
        target_sections=candidates.target_section_count,
        candidate_tables=candidate_count,
        source_occurrences=len(occurrence_tuple),
        canonical_events=event_count,
        source_rows=source_count,
        issue_date_coverage=sum(item.issue_date is not None for item in occurrence_tuple),
        amount_coverage=sum(item.amount_krw is not None for item in occurrence_tuple),
        type_counts=dict(sorted(type_counts.items())),
    )
