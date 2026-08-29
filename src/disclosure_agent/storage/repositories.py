"""Database repositories."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from disclosure_agent.storage.db_models import (
    CompanyRow,
    DisclosureEventRow,
    DisclosureRow,
    EventEvidenceRow,
    SupplyContractCorrectionLinkRow,
    SupplyContractEventRow,
    SupplyContractLifecycleRow,
    SupplyContractSuccessionEventRow,
    SupplyContractSuccessionLifecycleRow,
    SupplyContractTerminationEventRow,
    SupplyContractTerminationLinkRow,
)
from disclosure_agent.storage.supply_contract_persistence import (
    SupplyContractPersistenceBundle,
)


class SupplyContractRepository:
    """Upsert one deterministic Supply Contract persistence snapshot."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_bundle(self, bundle: SupplyContractPersistenceBundle) -> None:
        """Persist the complete slice in foreign-key-safe order."""

        _upsert(self.session, CompanyRow, bundle.companies, ("corp_code",))
        _upsert(self.session, DisclosureRow, bundle.disclosures, ("filing_id",))
        _upsert(self.session, DisclosureEventRow, bundle.disclosure_events, ("event_id",))
        _upsert(
            self.session,
            SupplyContractEventRow,
            bundle.supply_contract_events,
            ("filing_id",),
        )
        _upsert(
            self.session,
            SupplyContractCorrectionLinkRow,
            bundle.correction_links,
            ("correction_filing_id",),
        )
        _upsert(
            self.session,
            SupplyContractTerminationEventRow,
            bundle.termination_events,
            ("filing_id",),
        )
        _upsert(
            self.session,
            SupplyContractTerminationLinkRow,
            bundle.termination_links,
            ("termination_filing_id",),
        )
        _upsert(
            self.session,
            SupplyContractSuccessionEventRow,
            bundle.succession_events,
            ("filing_id",),
        )
        _upsert(
            self.session,
            SupplyContractLifecycleRow,
            bundle.lifecycle_states,
            ("root_filing_id",),
        )
        _upsert(
            self.session,
            SupplyContractSuccessionLifecycleRow,
            bundle.succession_lifecycle_states,
            ("succession_filing_id",),
        )
        _upsert(
            self.session,
            EventEvidenceRow,
            bundle.evidence,
            ("event_id", "attribute"),
            exclude_update=("evidence_id",),
        )


def _upsert(
    session: Session,
    model: type[Any],
    rows: Iterable[Mapping[str, Any]],
    conflict_columns: tuple[str, ...],
    *,
    exclude_update: tuple[str, ...] = (),
) -> None:
    materialized = list(rows)
    if not materialized:
        return

    statement = insert(model).values(materialized)
    excluded = set(conflict_columns) | set(exclude_update)
    update_values = {
        column.name: getattr(statement.excluded, column.name)
        for column in model.__table__.columns
        if column.name not in excluded
    }
    session.execute(
        statement.on_conflict_do_update(
            index_elements=list(conflict_columns),
            set_=update_values,
        )
    )
