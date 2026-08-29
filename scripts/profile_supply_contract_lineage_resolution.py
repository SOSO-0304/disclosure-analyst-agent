"""Profile deterministic in-corpus Supply Contract correction lineage resolution."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from disclosure_agent.extractors.supply_contract_lineage import (
    LineageResolutionStatus,
    resolve_supply_contract_lineage,
)
from disclosure_agent.storage.jsonl import read_canonical

SUBSET = Path("data/processed/subsets/supply-contract-v22.jsonl")


def main() -> None:
    if not SUBSET.is_file():
        raise SystemExit(f"Subset not found: {SUBSET}")

    packages = list(read_canonical(SUBSET))
    lineage = resolve_supply_contract_lineage(packages)
    counts = Counter(link.status for link in lineage.links)

    resolved = counts[LineageResolutionStatus.RESOLVED]
    total = len(lineage.links)
    unique_roots = len(set(lineage.root_by_filing_id.values()))

    print("=== supply contract lineage resolution ===")
    print(f"packages                         {len(packages)}")
    print(f"corrections                      {total}")
    print(f"resolved direct predecessors     {resolved}/{total}")
    print(f"unique in-corpus roots           {unique_roots}")
    print()
    print("status                         count   coverage")
    print("-----------------------------  ------  --------")
    for status in LineageResolutionStatus:
        count = counts[status]
        coverage = count / total * 100 if total else 0.0
        print(f"{status.value:<29}  {count:>6}  {coverage:>7.2f}%")

    unresolved = [link for link in lineage.links if link.status is not LineageResolutionStatus.RESOLVED]
    if unresolved:
        print()
        print("=== unresolved examples ===")
        for link in unresolved[:30]:
            related = link.related_filing_date.isoformat() if link.related_filing_date else "-"
            print(
                f"{link.status.value:<23} "
                f"receipt={link.correction_receipt_number} "
                f"related_date={related} "
                f"candidates={len(link.candidate_filing_ids)}"
            )


if __name__ == "__main__":
    main()
