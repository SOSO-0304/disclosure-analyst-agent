"""Deterministic public source references for grounded answers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from disclosure_agent.retrieval.evidence_pack import EvidencePack
from disclosure_agent.storage.db_models import DisclosureRow, SourceFilingRow


@dataclass(frozen=True, slots=True)
class SourceReference:
    """One cited disclosure shared by one or more evidence items."""

    filing_id: str
    report_name: str
    receipt_date: date | None
    evidence_labels: tuple[str, ...]


def build_source_references(session: Session, pack: EvidencePack) -> tuple[SourceReference, ...]:
    """Resolve evidence filing IDs to deduplicated public disclosure references."""

    grouped: dict[str, list[str]] = {}
    report_names: dict[str, str] = {}

    for item in pack.items:
        grouped.setdefault(item.filing_id, []).append(f"E{item.rank}")
        report_names.setdefault(item.filing_id, item.report_name)

    references = []
    for filing_id, evidence_labels in grouped.items():
        source_filing = session.get(SourceFilingRow, filing_id)
        disclosure = None
        if source_filing is None:
            disclosure = session.get(DisclosureRow, filing_id)

        report_name = report_names[filing_id]
        receipt_date = None
        if source_filing is not None:
            report_name = source_filing.report_name
            receipt_date = source_filing.receipt_date
        elif disclosure is not None:
            report_name = disclosure.report_name
            receipt_date = disclosure.receipt_date

        references.append(
            SourceReference(
                filing_id=filing_id,
                report_name=report_name,
                receipt_date=receipt_date,
                evidence_labels=tuple(evidence_labels),
            )
        )

    return tuple(references)


def render_source_references(references: tuple[SourceReference, ...]) -> str:
    """Render the public evidence footer required for every answer."""

    lines = ["근거 공시"]
    if not references:
        lines.append("- 확인된 근거 공시 없음")
        return "\n".join(lines)

    for reference in references:
        receipt_date = (
            reference.receipt_date.isoformat()
            if reference.receipt_date is not None
            else "확인되지 않음"
        )
        lines.append(f"- {reference.report_name} | 공시일: {receipt_date}")
    return "\n".join(lines)
