"""Application service for deterministic Supply Contract lifecycle questions."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from disclosure_agent.domain.events import EventType
from disclosure_agent.storage.supply_contract_query_repository import (
    EvidenceRecord,
    SupplyContractQueryRepository,
    TerminatedContractRecord,
)

ROOT_FORMATION_EVIDENCE_ATTRIBUTES = ("contract_date", "contract_name", "counterparty")
LATEST_FORMATION_EVIDENCE_ATTRIBUTES = (
    "contract_name",
    "contract_amount",
    "counterparty",
)
TERMINATION_EVIDENCE_ATTRIBUTES = ("termination_date", "termination_reason", "contract_name")


@dataclass(frozen=True, slots=True)
class TerminatedContractFinding:
    """One SQL-grounded contract termination finding plus cell-level evidence."""

    contract: TerminatedContractRecord
    root_formation_evidence: tuple[EvidenceRecord, ...]
    latest_formation_evidence: tuple[EvidenceRecord, ...]
    termination_evidence: tuple[EvidenceRecord, ...]


@dataclass(frozen=True, slots=True)
class TerminatedContractsInYearResult:
    """Answer payload for 'contracts formed in year and later terminated'."""

    year: int
    company_name: str | None
    findings: tuple[TerminatedContractFinding, ...]

    @property
    def exists(self) -> bool:
        """Whether at least one deterministically linked termination exists."""

        return bool(self.findings)


def find_terminated_contracts_formed_in_year(
    *,
    session: Session,
    year: int,
    company_name: str | None = None,
) -> TerminatedContractsInYearResult:
    """Return contracts formed in ``year`` that have a resolved later termination."""

    repository = SupplyContractQueryRepository(session)
    contracts = repository.find_terminated_contracts_formed_in_year(
        year=year,
        company_name=company_name,
    )
    findings: list[TerminatedContractFinding] = []
    for contract in contracts:
        root_formation_event_id = (
            f"{EventType.SUPPLY_CONTRACT.value}:{contract.root_filing_id}"
        )
        latest_formation_event_id = (
            f"{EventType.SUPPLY_CONTRACT.value}:{contract.latest_formation_filing_id}"
        )
        termination_event_id = (
            f"{EventType.SUPPLY_CONTRACT_TERMINATION.value}:"
            f"{contract.termination_filing_id}"
        )
        findings.append(
            TerminatedContractFinding(
                contract=contract,
                root_formation_evidence=repository.evidence_for_event(
                    root_formation_event_id,
                    attributes=ROOT_FORMATION_EVIDENCE_ATTRIBUTES,
                ),
                latest_formation_evidence=repository.evidence_for_event(
                    latest_formation_event_id,
                    attributes=LATEST_FORMATION_EVIDENCE_ATTRIBUTES,
                ),
                termination_evidence=repository.evidence_for_event(
                    termination_event_id,
                    attributes=TERMINATION_EVIDENCE_ATTRIBUTES,
                ),
            )
        )

    return TerminatedContractsInYearResult(
        year=year,
        company_name=company_name,
        findings=tuple(findings),
    )
