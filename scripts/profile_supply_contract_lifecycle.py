"""Profile effective Supply Contract lifecycle states on the 1,127-filing subset."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from disclosure_agent.extractors.supply_contract_lifecycle import (
    SupplyContractLifecycleStatus,
    project_supply_contract_lifecycle,
)
from disclosure_agent.extractors.supply_contract_termination_lineage import (
    TerminationLineageStatus,
)
from disclosure_agent.storage.jsonl import read_canonical

SUBSET = Path("data/processed/subsets/supply-contract-lifecycle-v22.jsonl")


def main() -> None:
    if not SUBSET.is_file():
        raise SystemExit(f"Subset not found: {SUBSET}")

    packages = list(read_canonical(SUBSET))
    projection = project_supply_contract_lifecycle(packages)
    state_counts = Counter(state.status for state in projection.states)
    termination_counts = Counter(link.status for link in projection.termination_lineage.links)

    complete = sum(state.correction_lineage_complete for state in projection.states)
    terminated = [
        state
        for state in projection.states
        if state.status is SupplyContractLifecycleStatus.TERMINATED
    ]

    print("=== supply contract lifecycle projection ===")
    print(f"packages                         {len(packages)}")
    print(f"in-corpus contract roots         {len(projection.states)}")
    print(f"correction lineage complete      {complete}/{len(projection.states)}")
    print(f"active roots                     {state_counts[SupplyContractLifecycleStatus.ACTIVE]}")
    print(
        "terminated roots                 "
        f"{state_counts[SupplyContractLifecycleStatus.TERMINATED]}"
    )
    print()
    print("termination linkage            count")
    print("-----------------------------  -----")
    for status in TerminationLineageStatus:
        print(f"{status.value:<29}  {termination_counts[status]:>5}")

    print()
    print("=== terminated in-corpus roots ===")
    for state in terminated:
        print(
            f"root={state.root_receipt_number} "
            f"latest={state.latest_formation_receipt_number} "
            f"corrections={state.correction_count} "
            f"lineage_complete={state.correction_lineage_complete} "
            f"terminations={','.join(state.termination_receipt_numbers)}"
        )

    unresolved = [
        link
        for link in projection.termination_lineage.links
        if link.status
        not in {
            TerminationLineageStatus.RESOLVED,
            TerminationLineageStatus.RESOLVED_BY_FINGERPRINT,
        }
    ]
    if unresolved:
        print()
        print("=== unresolved termination filings ===")
        for link in unresolved:
            dates = ",".join(value.isoformat() for value in link.related_formation_dates) or "-"
            print(
                f"termination={link.termination_receipt_number} "
                f"status={link.status.value} "
                f"dates={dates} "
                f"candidates={len(link.candidate_filing_ids)}"
            )


if __name__ == "__main__":
    main()
