"""Application service for persisting the Supply Contract vertical slice."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from disclosure_agent.domain.models import FilingPackage
from disclosure_agent.storage.repositories import SupplyContractRepository
from disclosure_agent.storage.supply_contract_persistence import (
    SupplyContractPersistenceBundle,
    build_supply_contract_persistence_bundle,
)


@dataclass(frozen=True, slots=True)
class SupplyContractIngestionResult:
    """Stable ingestion counts returned to controllers."""

    counts: dict[str, int]


def ingest_supply_contract_packages(
    packages: list[FilingPackage],
    *,
    session: Session,
) -> SupplyContractIngestionResult:
    """Build the domain projection and persist it through the repository layer."""

    bundle: SupplyContractPersistenceBundle = build_supply_contract_persistence_bundle(packages)
    SupplyContractRepository(session).upsert_bundle(bundle)
    return SupplyContractIngestionResult(counts=bundle.counts)
