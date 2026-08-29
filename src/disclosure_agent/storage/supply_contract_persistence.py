"""Transform the Supply Contract lifecycle slice into database-ready rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from disclosure_agent.domain.events import EventType
from disclosure_agent.domain.models import FilingPackage, SourceLocator
from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader, SemanticField
from disclosure_agent.extractors.supply_contract import extract_supply_contract
from disclosure_agent.extractors.supply_contract_lifecycle import (
    project_supply_contract_lifecycle,
)
from disclosure_agent.extractors.supply_contract_succession import (
    extract_supply_contract_succession,
    is_supply_contract_succession,
)
from disclosure_agent.extractors.supply_contract_termination import (
    extract_supply_contract_termination,
)
from disclosure_agent.extractors.supply_contract_termination_lineage import (
    FORMATION_SUBTYPE,
    TERMINATION_SUBTYPE,
)

Row = dict[str, Any]


@dataclass(frozen=True, slots=True)
class SupplyContractPersistenceBundle:
    """Database-ready rows for one deterministic Supply Contract snapshot."""

    companies: tuple[Row, ...]
    disclosures: tuple[Row, ...]
    disclosure_events: tuple[Row, ...]
    supply_contract_events: tuple[Row, ...]
    correction_links: tuple[Row, ...]
    termination_events: tuple[Row, ...]
    termination_links: tuple[Row, ...]
    succession_events: tuple[Row, ...]
    lifecycle_states: tuple[Row, ...]
    succession_lifecycle_states: tuple[Row, ...]
    evidence: tuple[Row, ...]

    @property
    def counts(self) -> dict[str, int]:
        """Return stable table-row counts for diagnostics and tests."""

        return {
            "companies": len(self.companies),
            "disclosures": len(self.disclosures),
            "disclosure_events": len(self.disclosure_events),
            "supply_contract_events": len(self.supply_contract_events),
            "correction_links": len(self.correction_links),
            "termination_events": len(self.termination_events),
            "termination_links": len(self.termination_links),
            "succession_events": len(self.succession_events),
            "lifecycle_states": len(self.lifecycle_states),
            "succession_lifecycle_states": len(self.succession_lifecycle_states),
            "evidence": len(self.evidence),
        }


def build_supply_contract_persistence_bundle(
    packages: list[FilingPackage],
    *,
    reader: ExchangeFieldReader | None = None,
) -> SupplyContractPersistenceBundle:
    """Build relational rows without writing to the database."""

    field_reader = reader or ExchangeFieldReader()
    projection = project_supply_contract_lifecycle(packages, reader=field_reader)
    formations = [
        package for package in packages if package.filing.document_subtype == FORMATION_SUBTYPE
    ]
    terminations = [
        package for package in packages if package.filing.document_subtype == TERMINATION_SUBTYPE
    ]
    successions = [package for package in packages if is_supply_contract_succession(package)]

    companies = _company_rows(packages)
    disclosures = tuple(_disclosure_row(package) for package in packages)

    correction_by_id = {
        link.correction_filing_id: link for link in projection.correction_lineage.links
    }
    latest_by_root = {
        state.root_filing_id: state.latest_formation_filing_id for state in projection.states
    }

    disclosure_events: list[Row] = []
    supply_contract_events: list[Row] = []
    termination_events: list[Row] = []
    succession_events: list[Row] = []
    evidence: list[Row] = []

    for package in formations:
        extraction = extract_supply_contract(package, reader=field_reader)
        event = extraction.event
        event_id = _event_id(package.filing_id, EventType.SUPPLY_CONTRACT)
        root_id = projection.correction_lineage.root_by_filing_id[package.filing_id]
        correction_link = correction_by_id.get(package.filing_id)
        disclosure_events.append(
            _event_envelope(
                package,
                event_id=event_id,
                event_type=EventType.SUPPLY_CONTRACT,
                event_date=event.contract_date or package.filing.receipt_date,
            )
        )
        supply_contract_events.append(
            {
                "filing_id": package.filing_id,
                "event_id": event_id,
                "root_filing_id": root_id,
                "predecessor_filing_id": (
                    correction_link.predecessor_filing_id if correction_link else None
                ),
                "lineage_status": correction_link.status.value if correction_link else None,
                "is_latest_for_root": latest_by_root[root_id] == package.filing_id,
                "contract_type": event.contract_type,
                "contract_name": event.contract_name,
                "contract_amount": event.contract_amount,
                "recent_revenue": event.recent_revenue,
                "revenue_ratio": event.revenue_ratio,
                "counterparty": event.counterparty,
                "relationship": event.relationship,
                "region": event.region,
                "contract_start_date": event.contract_start_date,
                "contract_end_date": event.contract_end_date,
                "contract_date": event.contract_date,
                "major_conditions": event.major_conditions,
            }
        )
        evidence.extend(_evidence_rows(event_id, package.filing_id, extraction.evidence))

    for package in terminations:
        extraction = extract_supply_contract_termination(package, reader=field_reader)
        event = extraction.event
        event_id = _event_id(package.filing_id, EventType.SUPPLY_CONTRACT_TERMINATION)
        disclosure_events.append(
            _event_envelope(
                package,
                event_id=event_id,
                event_type=EventType.SUPPLY_CONTRACT_TERMINATION,
                event_date=event.termination_date or package.filing.receipt_date,
            )
        )
        termination_events.append(
            {
                "filing_id": package.filing_id,
                "event_id": event_id,
                "termination_type": event.termination_type,
                "contract_name": event.contract_name,
                "termination_amount": event.termination_amount,
                "recent_revenue": event.recent_revenue,
                "revenue_ratio": event.revenue_ratio,
                "counterparty": event.counterparty,
                "relationship": event.relationship,
                "contract_start_date": event.contract_start_date,
                "contract_end_date": event.contract_end_date,
                "termination_reason": event.termination_reason,
                "termination_date": event.termination_date,
                "notes": event.notes,
                "related_disclosures": event.related_disclosures,
            }
        )
        evidence.extend(_evidence_rows(event_id, package.filing_id, extraction.evidence))

    for package in successions:
        extraction = extract_supply_contract_succession(package, reader=field_reader)
        event = extraction.event
        event_id = _event_id(package.filing_id, EventType.SUPPLY_CONTRACT_SUCCESSION)
        disclosure_events.append(
            _event_envelope(
                package,
                event_id=event_id,
                event_type=EventType.SUPPLY_CONTRACT_SUCCESSION,
                event_date=event.decision_date or package.filing.receipt_date,
            )
        )
        succession_events.append(
            {
                "filing_id": package.filing_id,
                "event_id": event_id,
                "title": event.title,
                "contract_type": event.contract_type,
                "succession_amount_krw": event.succession_amount_krw,
                "succession_amount_usd": event.succession_amount_usd,
                "disclosed_exchange_rate": event.disclosed_exchange_rate,
                "fulfilled_amount_usd": event.fulfilled_amount_usd,
                "fulfillment_ratio": event.fulfillment_ratio,
                "counterparty": event.counterparty,
                "contract_start_date": event.contract_start_date,
                "contract_end_date": event.contract_end_date,
                "decision_date": event.decision_date,
                "correction_reason": event.correction_reason,
                "notes": event.notes,
                "related_disclosures": event.related_disclosures,
                "source_contract_reference_dates": [
                    value.isoformat() for value in event.source_contract_reference_dates
                ],
            }
        )
        evidence.extend(_evidence_rows(event_id, package.filing_id, extraction.evidence))

    correction_links = tuple(
        {
            "correction_filing_id": link.correction_filing_id,
            "predecessor_filing_id": link.predecessor_filing_id,
            "root_filing_id": projection.correction_lineage.root_by_filing_id[
                link.correction_filing_id
            ],
            "related_filing_date": link.related_filing_date,
            "status": link.status.value,
            "candidate_filing_ids": list(link.candidate_filing_ids),
            "fingerprint_score": link.fingerprint_score,
            "fingerprint_compared": link.fingerprint_compared,
            "correction_table_score": link.correction_table_score,
            "correction_table_compared": link.correction_table_compared,
        }
        for link in projection.correction_lineage.links
    )
    termination_links = tuple(
        {
            "termination_filing_id": link.termination_filing_id,
            "matched_formation_filing_id": link.matched_formation_filing_id,
            "root_filing_id": link.root_formation_filing_id,
            "status": link.status.value,
            "related_formation_dates": [
                value.isoformat() for value in link.related_formation_dates
            ],
            "candidate_filing_ids": list(link.candidate_filing_ids),
            "fingerprint_score": link.fingerprint_score,
            "fingerprint_compared": link.fingerprint_compared,
        }
        for link in projection.termination_lineage.links
    )
    lifecycle_states = tuple(
        {
            "root_filing_id": state.root_filing_id,
            "corp_code": state.corp_code,
            "latest_formation_filing_id": state.latest_formation_filing_id,
            "latest_receipt_date": state.latest_formation_receipt_date,
            "correction_count": state.correction_count,
            "correction_lineage_complete": state.correction_lineage_complete,
            "status": state.status.value,
            "termination_filing_ids": list(state.termination_filing_ids),
            "latest_termination_date": state.latest_termination_date,
        }
        for state in projection.states
    )
    succession_lifecycle_states = tuple(
        {
            "succession_filing_id": state.succession_filing_id,
            "corp_code": state.corp_code,
            "predecessor_scope": state.predecessor_scope.value,
            "status": state.status.value,
            "source_contract_reference_dates": [
                value.isoformat() for value in state.source_contract_reference_dates
            ],
            "matched_source_root_filing_ids": list(state.matched_source_root_filing_ids),
            "contract_end_date": state.contract_end_date,
            "decision_date": state.decision_date,
        }
        for state in projection.succession_states
    )

    return SupplyContractPersistenceBundle(
        companies=companies,
        disclosures=disclosures,
        disclosure_events=tuple(disclosure_events),
        supply_contract_events=tuple(supply_contract_events),
        correction_links=correction_links,
        termination_events=tuple(termination_events),
        termination_links=termination_links,
        succession_events=tuple(succession_events),
        lifecycle_states=lifecycle_states,
        succession_lifecycle_states=succession_lifecycle_states,
        evidence=tuple(evidence),
    )


def _company_rows(packages: list[FilingPackage]) -> tuple[Row, ...]:
    rows: dict[str, Row] = {}
    for package in packages:
        rows[package.company.corp_code] = {
            "corp_code": package.company.corp_code,
            "stock_code": package.company.stock_code or None,
            "corp_name": package.company.corp_name,
            "listed_name": package.company.listed_name,
            "industry": package.company.industry or None,
            "sector": package.company.sector or None,
        }
    return tuple(rows[key] for key in sorted(rows))


def _disclosure_row(package: FilingPackage) -> Row:
    return {
        "filing_id": package.filing_id,
        "receipt_number": package.filing.receipt_number,
        "corp_code": package.company.corp_code,
        "document_group": package.filing.document_group.value,
        "document_subtype": package.filing.document_subtype,
        "report_name": package.filing.report_name_raw,
        "receipt_date": package.filing.receipt_date,
        "filer_name": package.filing.filer_name,
        "is_correction": package.correction.is_correction,
        "schema_version": package.schema_version,
    }


def _event_id(filing_id: str, event_type: EventType) -> str:
    return f"{event_type.value}:{filing_id}"


def _event_envelope(
    package: FilingPackage,
    *,
    event_id: str,
    event_type: EventType,
    event_date: object,
) -> Row:
    return {
        "event_id": event_id,
        "filing_id": package.filing_id,
        "corp_code": package.company.corp_code,
        "event_type": event_type.value,
        "event_date": event_date,
    }


def _evidence_rows(
    event_id: str,
    filing_id: str,
    evidence: dict[str, SemanticField],
) -> list[Row]:
    rows: list[Row] = []
    for attribute, field in evidence.items():
        rows.append(
            {
                "event_id": event_id,
                "filing_id": filing_id,
                "attribute": attribute,
                "document_id": field.document_id,
                "table_id": field.table_id,
                "path": field.path_key,
                "row_index": field.row_index,
                "value_column_index": field.value_column_index,
                "value_text": field.value,
                "raw_value": field.raw_value,
                "value_locator": _locator_json(field.value_locator),
                "label_locators": [_locator_json(locator) for locator in field.label_locators],
            }
        )
    return rows


def _locator_json(locator: SourceLocator | None) -> dict[str, object] | None:
    if locator is None:
        return None
    return locator.model_dump(mode="json")
