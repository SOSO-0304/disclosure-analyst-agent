"""Project Supply Contract formation/correction/termination links into lifecycle state.

The projection is intentionally conservative. A contract is marked terminated only
when a termination filing has been deterministically linked to an in-corpus
formation root. Unresolved or out-of-corpus termination filings are retained in
the termination lineage but never guessed onto an in-corpus contract.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from disclosure_agent.domain.models import FilingPackage
from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader
from disclosure_agent.extractors.supply_contract_lineage import (
    LineageResolutionStatus,
    SupplyContractLineage,
    resolve_supply_contract_lineage,
)
from disclosure_agent.extractors.supply_contract_succession import (
    extract_supply_contract_succession,
    is_supply_contract_succession,
)
from disclosure_agent.extractors.supply_contract_termination_lineage import (
    FORMATION_SUBTYPE,
    TERMINATION_SUBTYPE,
    SupplyContractTerminationLineage,
    TerminationLineageStatus,
    resolve_supply_contract_termination_lineage,
)


class SupplyContractLifecycleStatus(StrEnum):
    """Effective state of one in-corpus Supply Contract formation chain."""

    ACTIVE = "active"
    TERMINATED = "terminated"


class SupplyContractSuccessionPredecessorScope(StrEnum):
    """Whether the source contract for a succession event exists in the corpus."""

    IN_CORPUS = "in_corpus"
    EXTERNAL = "external"


class SupplyContractSuccessionStatus(StrEnum):
    """Effective state of a succeeded contract represented by a special filing."""

    SUCCEEDED = "succeeded"
    SUCCEEDED_ENDED = "succeeded_ended"


@dataclass(frozen=True, slots=True)
class SupplyContractLifecycleState:
    """Effective lifecycle state for one in-corpus formation root."""

    root_filing_id: str
    root_receipt_number: str
    corp_code: str
    company_name: str
    latest_formation_filing_id: str
    latest_formation_receipt_number: str
    latest_formation_receipt_date: date
    correction_count: int
    correction_lineage_complete: bool
    status: SupplyContractLifecycleStatus
    termination_filing_ids: tuple[str, ...]
    termination_receipt_numbers: tuple[str, ...]
    latest_termination_date: date | None


@dataclass(frozen=True, slots=True)
class SupplyContractSuccessionLifecycleState:
    """Lifecycle state for a contract brought in through succession."""

    succession_filing_id: str
    succession_receipt_number: str
    corp_code: str
    company_name: str
    source_contract_reference_dates: tuple[date, ...]
    matched_source_root_filing_ids: tuple[str, ...]
    predecessor_scope: SupplyContractSuccessionPredecessorScope
    status: SupplyContractSuccessionStatus
    contract_end_date: date | None
    decision_date: date | None


@dataclass(frozen=True, slots=True)
class SupplyContractLifecycleProjection:
    """Lifecycle states plus the underlying conservative lineage results."""

    states: tuple[SupplyContractLifecycleState, ...]
    succession_states: tuple[SupplyContractSuccessionLifecycleState, ...]
    correction_lineage: SupplyContractLineage
    termination_lineage: SupplyContractTerminationLineage


def project_supply_contract_lifecycle(
    packages: Iterable[FilingPackage],
    *,
    reader: ExchangeFieldReader | None = None,
) -> SupplyContractLifecycleProjection:
    """Build effective in-corpus contract states without heuristic linkage."""

    package_list = list(packages)
    field_reader = reader or ExchangeFieldReader()
    formations = [
        package for package in package_list if package.filing.document_subtype == FORMATION_SUBTYPE
    ]
    terminations = [
        package
        for package in package_list
        if package.filing.document_subtype == TERMINATION_SUBTYPE
    ]
    successions = [package for package in package_list if is_supply_contract_succession(package)]

    correction_lineage = resolve_supply_contract_lineage(formations, reader=field_reader)
    termination_lineage = resolve_supply_contract_termination_lineage(
        package_list,
        reader=field_reader,
    )

    formation_by_id = {package.filing_id: package for package in formations}
    termination_by_id = {package.filing_id: package for package in terminations}

    members_by_root: dict[str, list[FilingPackage]] = defaultdict(list)
    for package in formations:
        root = correction_lineage.root_by_filing_id[package.filing_id]
        members_by_root[root].append(package)

    correction_status_by_id = {
        link.correction_filing_id: link.status for link in correction_lineage.links
    }
    resolved_correction_statuses = {
        LineageResolutionStatus.RESOLVED,
        LineageResolutionStatus.RESOLVED_BY_FINGERPRINT,
        LineageResolutionStatus.RESOLVED_BY_CORRECTION_TABLE,
    }

    terminations_by_root: dict[str, list[FilingPackage]] = defaultdict(list)
    resolved_termination_statuses = {
        TerminationLineageStatus.RESOLVED,
        TerminationLineageStatus.RESOLVED_BY_FINGERPRINT,
    }
    for link in termination_lineage.links:
        if (
            link.status not in resolved_termination_statuses
            or link.root_formation_filing_id is None
        ):
            continue
        package = termination_by_id.get(link.termination_filing_id)
        if package is not None:
            terminations_by_root[link.root_formation_filing_id].append(package)

    states: list[SupplyContractLifecycleState] = []
    for root_id, members in members_by_root.items():
        root = formation_by_id[root_id]
        members.sort(key=lambda item: (item.filing.receipt_date, item.filing.receipt_number))
        latest = members[-1]
        corrections = [package for package in members if package.correction.is_correction]
        lineage_complete = all(
            correction_status_by_id.get(package.filing_id) in resolved_correction_statuses
            for package in corrections
        )

        linked_terminations = terminations_by_root.get(root_id, [])
        linked_terminations.sort(
            key=lambda item: (item.filing.receipt_date, item.filing.receipt_number)
        )
        status = (
            SupplyContractLifecycleStatus.TERMINATED
            if linked_terminations
            else SupplyContractLifecycleStatus.ACTIVE
        )

        states.append(
            SupplyContractLifecycleState(
                root_filing_id=root_id,
                root_receipt_number=root.filing.receipt_number,
                corp_code=root.company.corp_code,
                company_name=root.company.listed_name,
                latest_formation_filing_id=latest.filing_id,
                latest_formation_receipt_number=latest.filing.receipt_number,
                latest_formation_receipt_date=latest.filing.receipt_date,
                correction_count=len(corrections),
                correction_lineage_complete=lineage_complete,
                status=status,
                termination_filing_ids=tuple(package.filing_id for package in linked_terminations),
                termination_receipt_numbers=tuple(
                    package.filing.receipt_number for package in linked_terminations
                ),
                latest_termination_date=(
                    linked_terminations[-1].filing.receipt_date if linked_terminations else None
                ),
            )
        )

    states.sort(key=lambda state: (state.corp_code, state.root_receipt_number))
    succession_states = _project_succession_states(
        successions,
        formations=formations,
        correction_lineage=correction_lineage,
        reader=field_reader,
    )
    return SupplyContractLifecycleProjection(
        states=tuple(states),
        succession_states=succession_states,
        correction_lineage=correction_lineage,
        termination_lineage=termination_lineage,
    )


def _project_succession_states(
    successions: list[FilingPackage],
    *,
    formations: list[FilingPackage],
    correction_lineage: SupplyContractLineage,
    reader: ExchangeFieldReader,
) -> tuple[SupplyContractSuccessionLifecycleState, ...]:
    formation_by_company_date: dict[tuple[str, date], list[FilingPackage]] = defaultdict(list)
    for package in formations:
        formation_by_company_date[(package.company.corp_code, package.filing.receipt_date)].append(
            package
        )

    states: list[SupplyContractSuccessionLifecycleState] = []
    for package in successions:
        extraction = extract_supply_contract_succession(package, reader=reader)
        event = extraction.event
        roots: list[str] = []
        for reference_date in event.source_contract_reference_dates:
            candidates = formation_by_company_date.get(
                (package.company.corp_code, reference_date),
                [],
            )
            for candidate in candidates:
                root_id = correction_lineage.root_by_filing_id.get(candidate.filing_id)
                if root_id is not None and root_id not in roots:
                    roots.append(root_id)

        predecessor_scope = (
            SupplyContractSuccessionPredecessorScope.IN_CORPUS
            if roots
            else SupplyContractSuccessionPredecessorScope.EXTERNAL
        )
        explicitly_ended = bool(
            event.correction_reason and "계약기간 종료" in event.correction_reason
        )
        status = (
            SupplyContractSuccessionStatus.SUCCEEDED_ENDED
            if explicitly_ended
            else SupplyContractSuccessionStatus.SUCCEEDED
        )
        states.append(
            SupplyContractSuccessionLifecycleState(
                succession_filing_id=package.filing_id,
                succession_receipt_number=package.filing.receipt_number,
                corp_code=package.company.corp_code,
                company_name=package.company.listed_name,
                source_contract_reference_dates=event.source_contract_reference_dates,
                matched_source_root_filing_ids=tuple(roots),
                predecessor_scope=predecessor_scope,
                status=status,
                contract_end_date=event.contract_end_date,
                decision_date=event.decision_date,
            )
        )

    states.sort(key=lambda state: (state.corp_code, state.succession_receipt_number))
    return tuple(states)
