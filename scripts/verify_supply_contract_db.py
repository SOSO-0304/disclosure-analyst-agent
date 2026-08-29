"""Verify persisted Supply Contract row counts and lifecycle invariants."""

from __future__ import annotations

import argparse

from sqlalchemy import func, select

from disclosure_agent.storage.database import get_engine, session_scope
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

EXPECTED_COUNTS = {
    "companies": 34,
    "disclosures": 1127,
    "disclosure_events": 1127,
    "supply_contract_events": 1106,
    "correction_links": 563,
    "termination_events": 20,
    "termination_links": 20,
    "succession_events": 1,
    "lifecycle_states": 815,
    "succession_lifecycle_states": 1,
    "evidence": 11355,
}

TABLE_MODELS = {
    "companies": CompanyRow,
    "disclosures": DisclosureRow,
    "disclosure_events": DisclosureEventRow,
    "supply_contract_events": SupplyContractEventRow,
    "correction_links": SupplyContractCorrectionLinkRow,
    "termination_events": SupplyContractTerminationEventRow,
    "termination_links": SupplyContractTerminationLinkRow,
    "succession_events": SupplyContractSuccessionEventRow,
    "lifecycle_states": SupplyContractLifecycleRow,
    "succession_lifecycle_states": SupplyContractSuccessionLifecycleRow,
    "evidence": EventEvidenceRow,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    mismatches: list[str] = []
    with session_scope(engine) as session:
        actual_counts = {
            name: session.scalar(select(func.count()).select_from(model)) or 0
            for name, model in TABLE_MODELS.items()
        }
        latest_contract_rows = session.scalar(
            select(func.count())
            .select_from(SupplyContractEventRow)
            .where(SupplyContractEventRow.is_latest_for_root.is_(True))
        ) or 0
        terminated_roots = session.scalar(
            select(func.count())
            .select_from(SupplyContractLifecycleRow)
            .where(SupplyContractLifecycleRow.status == "terminated")
        ) or 0
        external_successions = session.scalar(
            select(func.count())
            .select_from(SupplyContractSuccessionLifecycleRow)
            .where(SupplyContractSuccessionLifecycleRow.predecessor_scope == "external")
        ) or 0

    print("=== supply contract database verification ===")
    for name, expected in EXPECTED_COUNTS.items():
        actual = actual_counts[name]
        marker = "OK" if actual == expected else "MISMATCH"
        print(f"{name:<30} {actual:>8}  expected={expected:<8} {marker}")
        if actual != expected:
            mismatches.append(f"{name}: actual={actual}, expected={expected}")

    invariants = {
        "latest contract rows": (latest_contract_rows, 815),
        "terminated roots": (terminated_roots, 10),
        "external succession states": (external_successions, 1),
    }
    print()
    for name, (actual, expected) in invariants.items():
        marker = "OK" if actual == expected else "MISMATCH"
        print(f"{name:<30} {actual:>8}  expected={expected:<8} {marker}")
        if actual != expected:
            mismatches.append(f"{name}: actual={actual}, expected={expected}")

    if mismatches:
        raise SystemExit("Database verification failed: " + "; ".join(mismatches))

    print("status                         verified")


if __name__ == "__main__":
    main()
