"""Application service for deterministic Supply Contract lifecycle questions."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from disclosure_agent.domain.events import EventType
from disclosure_agent.storage.supply_contract_query_repository import (
    EvidenceRecord,
    SupplyContractFormationRecord,
    SupplyContractQueryRepository,
    TerminatedContractRecord,
)

FORMATION_EVIDENCE_ATTRIBUTES = (
    "contract_date",
    "contract_name",
    "contract_amount",
    "counterparty",
)
ROOT_FORMATION_EVIDENCE_ATTRIBUTES = ("contract_date", "contract_name", "counterparty")
LATEST_FORMATION_EVIDENCE_ATTRIBUTES = (
    "contract_name",
    "contract_amount",
    "counterparty",
)
TERMINATION_EVIDENCE_ATTRIBUTES = ("termination_date", "termination_reason", "contract_name")


@dataclass(frozen=True, slots=True)
class SupplyContractFormationStepFinding:
    """One formation/correction step plus cell-level evidence."""

    formation: SupplyContractFormationRecord
    evidence: tuple[EvidenceRecord, ...]


@dataclass(frozen=True, slots=True)
class TerminatedContractFinding:
    """One SQL-grounded contract termination finding plus cell-level evidence."""

    contract: TerminatedContractRecord
    root_formation_evidence: tuple[EvidenceRecord, ...]
    latest_formation_evidence: tuple[EvidenceRecord, ...]
    termination_evidence: tuple[EvidenceRecord, ...]
    formation_steps: tuple[SupplyContractFormationStepFinding, ...] = ()


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

    @property
    def status(self) -> str:
        """Conservative answerability for lifecycle details."""

        if not self.findings:
            return "NO_MATCH"
        if all(finding.contract.correction_lineage_complete for finding in self.findings):
            return "ANSWERABLE"
        return "PARTIAL"


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
        root_formation_event_id = f"{EventType.SUPPLY_CONTRACT.value}:{contract.root_filing_id}"
        latest_formation_event_id = (
            f"{EventType.SUPPLY_CONTRACT.value}:{contract.latest_formation_filing_id}"
        )
        termination_event_id = (
            f"{EventType.SUPPLY_CONTRACT_TERMINATION.value}:{contract.termination_filing_id}"
        )

        formation_steps = []
        for formation in repository.formation_chain_for_root(contract.root_filing_id):
            event_id = f"{EventType.SUPPLY_CONTRACT.value}:{formation.filing_id}"
            formation_steps.append(
                SupplyContractFormationStepFinding(
                    formation=formation,
                    evidence=repository.evidence_for_event(
                        event_id,
                        attributes=FORMATION_EVIDENCE_ATTRIBUTES,
                    ),
                )
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
                formation_steps=tuple(formation_steps),
            )
        )

    return TerminatedContractsInYearResult(
        year=year,
        company_name=company_name,
        findings=tuple(findings),
    )
