"""Profile database-ready Supply Contract rows without touching PostgreSQL."""

from __future__ import annotations

from pathlib import Path

from disclosure_agent.storage.jsonl import read_canonical
from disclosure_agent.storage.supply_contract_persistence import (
    build_supply_contract_persistence_bundle,
)

SUBSET = Path("data/processed/subsets/supply-contract-lifecycle-v22.jsonl")


def main() -> None:
    if not SUBSET.is_file():
        raise SystemExit(f"Subset not found: {SUBSET}")

    packages = list(read_canonical(SUBSET))
    bundle = build_supply_contract_persistence_bundle(packages)

    expected = {
        "disclosures": 1127,
        "disclosure_events": 1127,
        "supply_contract_events": 1106,
        "correction_links": 563,
        "termination_events": 20,
        "termination_links": 20,
        "succession_events": 1,
        "lifecycle_states": 815,
        "succession_lifecycle_states": 1,
    }
    for name, count in expected.items():
        actual = bundle.counts[name]
        if actual != count:
            raise SystemExit(f"Unexpected {name}: expected {count}, got {actual}")

    print("=== supply contract persistence profile ===")
    for name, count in bundle.counts.items():
        print(f"{name:<30} {count:>8}")

    resolved_termination_links = sum(
        row["root_filing_id"] is not None for row in bundle.termination_links
    )
    latest_contract_rows = sum(
        bool(row["is_latest_for_root"]) for row in bundle.supply_contract_events
    )
    external_successions = sum(
        row["predecessor_scope"] == "external" for row in bundle.succession_lifecycle_states
    )

    print()
    print(f"latest contract rows              {latest_contract_rows}")
    print(f"resolved termination roots        {resolved_termination_links}")
    print(f"external succession states        {external_successions}")

    if latest_contract_rows != 815:
        raise SystemExit(
            f"Expected one latest formation per root (815), got {latest_contract_rows}"
        )
    if resolved_termination_links != 10:
        raise SystemExit(
            f"Expected 10 in-corpus termination links, got {resolved_termination_links}"
        )
    if external_successions != 1:
        raise SystemExit(f"Expected one external succession, got {external_successions}")


if __name__ == "__main__":
    main()
