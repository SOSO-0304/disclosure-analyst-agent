"""Read-side repository for Supply Contract lifecycle questions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session, aliased

from disclosure_agent.storage.db_models import (
    CompanyRow,
    DisclosureRow,
    EventEvidenceRow,
    SupplyContractEventRow,
    SupplyContractLifecycleRow,
    SupplyContractTerminationEventRow,
    SupplyContractTerminationLinkRow,
)

RESOLVED_TERMINATION_STATUSES = ("resolved", "resolved_by_fingerprint")


@dataclass(frozen=True, slots=True)
class TerminatedContractRecord:
    """One in-corpus contract formed in a target year and later terminated."""

    corp_code: str
    company_name: str
    root_filing_id: str
    root_receipt_number: str
    contract_date: date
    latest_formation_filing_id: str
    latest_formation_receipt_number: str
    contract_name: str | None
    contract_amount: int | None
    counterparty: str | None
    correction_lineage_complete: bool
    termination_filing_id: str
    termination_receipt_number: str
    termination_date: date | None
    termination_reason: str | None


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """Stored field evidence pointing back to canonical table cells."""

    event_id: str
    filing_id: str
    attribute: str
    document_id: str
    table_id: str
    path: str
    row_index: int
    value_column_index: int
    value_text: str
    raw_value: str
    value_locator: dict[str, object] | None
    label_locators: list[dict[str, object] | None]


class SupplyContractQueryRepository:
    """Execute deterministic SQL queries over the persisted contract slice."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def find_terminated_contracts_formed_in_year(
        self,
        *,
        year: int,
        company_name: str | None = None,
    ) -> tuple[TerminatedContractRecord, ...]:
        """Find contracts whose original contract date is in ``year`` and later terminated."""

        statement = _terminated_contracts_statement(year=year, company_name=company_name)
        rows = self.session.execute(statement).mappings().all()
        return tuple(TerminatedContractRecord(**dict(row)) for row in rows)

    def evidence_for_event(
        self,
        event_id: str,
        *,
        attributes: tuple[str, ...] = (),
    ) -> tuple[EvidenceRecord, ...]:
        """Return canonical-cell evidence for an event, optionally limited by attribute."""

        statement = select(
            EventEvidenceRow.event_id,
            EventEvidenceRow.filing_id,
            EventEvidenceRow.attribute,
            EventEvidenceRow.document_id,
            EventEvidenceRow.table_id,
            EventEvidenceRow.path,
            EventEvidenceRow.row_index,
            EventEvidenceRow.value_column_index,
            EventEvidenceRow.value_text,
            EventEvidenceRow.raw_value,
            EventEvidenceRow.value_locator,
            EventEvidenceRow.label_locators,
        ).where(EventEvidenceRow.event_id == event_id)
        if attributes:
            statement = statement.where(EventEvidenceRow.attribute.in_(attributes))
        statement = statement.order_by(EventEvidenceRow.attribute)
        rows = self.session.execute(statement).mappings().all()
        return tuple(EvidenceRecord(**dict(row)) for row in rows)


def _terminated_contracts_statement(
    *,
    year: int,
    company_name: str | None,
) -> Select[tuple[object, ...]]:
    root_event = aliased(SupplyContractEventRow, name="root_event")
    latest_event = aliased(SupplyContractEventRow, name="latest_event")
    root_disclosure = aliased(DisclosureRow, name="root_disclosure")
    latest_disclosure = aliased(DisclosureRow, name="latest_disclosure")
    termination_disclosure = aliased(DisclosureRow, name="termination_disclosure")

    start = date(year, 1, 1)
    end = date(year + 1, 1, 1)

    statement = (
        select(
            CompanyRow.corp_code.label("corp_code"),
            CompanyRow.listed_name.label("company_name"),
            SupplyContractLifecycleRow.root_filing_id.label("root_filing_id"),
            root_disclosure.receipt_number.label("root_receipt_number"),
            root_event.contract_date.label("contract_date"),
            SupplyContractLifecycleRow.latest_formation_filing_id.label(
                "latest_formation_filing_id"
            ),
            latest_disclosure.receipt_number.label("latest_formation_receipt_number"),
            latest_event.contract_name.label("contract_name"),
            latest_event.contract_amount.label("contract_amount"),
            latest_event.counterparty.label("counterparty"),
            SupplyContractLifecycleRow.correction_lineage_complete.label(
                "correction_lineage_complete"
            ),
            SupplyContractTerminationLinkRow.termination_filing_id.label(
                "termination_filing_id"
            ),
            termination_disclosure.receipt_number.label("termination_receipt_number"),
            SupplyContractTerminationEventRow.termination_date.label("termination_date"),
            SupplyContractTerminationEventRow.termination_reason.label("termination_reason"),
        )
        .join(
            CompanyRow,
            CompanyRow.corp_code == SupplyContractLifecycleRow.corp_code,
        )
        .join(
            root_event,
            root_event.filing_id == SupplyContractLifecycleRow.root_filing_id,
        )
        .join(
            root_disclosure,
            root_disclosure.filing_id == SupplyContractLifecycleRow.root_filing_id,
        )
        .join(
            latest_event,
            latest_event.filing_id
            == SupplyContractLifecycleRow.latest_formation_filing_id,
        )
        .join(
            latest_disclosure,
            latest_disclosure.filing_id
            == SupplyContractLifecycleRow.latest_formation_filing_id,
        )
        .join(
            SupplyContractTerminationLinkRow,
            SupplyContractTerminationLinkRow.root_filing_id
            == SupplyContractLifecycleRow.root_filing_id,
        )
        .join(
            SupplyContractTerminationEventRow,
            SupplyContractTerminationEventRow.filing_id
            == SupplyContractTerminationLinkRow.termination_filing_id,
        )
        .join(
            termination_disclosure,
            termination_disclosure.filing_id
            == SupplyContractTerminationLinkRow.termination_filing_id,
        )
        .where(
            SupplyContractLifecycleRow.status == "terminated",
            SupplyContractTerminationLinkRow.status.in_(RESOLVED_TERMINATION_STATUSES),
            root_event.contract_date >= start,
            root_event.contract_date < end,
            SupplyContractTerminationEventRow.termination_date >= root_event.contract_date,
        )
        .order_by(
            CompanyRow.listed_name,
            root_event.contract_date,
            SupplyContractTerminationEventRow.termination_date,
            termination_disclosure.receipt_number,
        )
    )

    if company_name:
        statement = statement.where(
            or_(
                CompanyRow.listed_name == company_name,
                CompanyRow.corp_name == company_name,
            )
        )
    return statement
