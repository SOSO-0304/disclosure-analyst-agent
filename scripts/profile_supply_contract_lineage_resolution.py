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

    direct = counts[LineageResolutionStatus.RESOLVED]
    fingerprint = counts[LineageResolutionStatus.RESOLVED_BY_FINGERPRINT]
    resolved = direct + fingerprint
    total = len(lineage.links)
    unique_roots = len(set(lineage.root_by_filing_id.values()))

    print("=== supply contract lineage resolution ===")
    print(f"packages                         {len(packages)}")
    print(f"corrections                      {total}")
    print(f"resolved direct predecessors     {direct}/{total}")
    print(f"resolved by fingerprint          {fingerprint}/{total}")
    print(f"resolved total                   {resolved}/{total}")
    print(f"unique in-corpus roots           {unique_roots}")
    print()
    print("status                         count   coverage")
    print("-----------------------------  ------  --------")
    for status in LineageResolutionStatus:
        count = counts[status]
        coverage = count / total * 100 if total else 0.0
        print(f"{status.value:<29}  {count:>6}  {coverage:>7.2f}%")

    fingerprint_links = [
        link
        for link in lineage.links
        if link.status is LineageResolutionStatus.RESOLVED_BY_FINGERPRINT
    ]
    if fingerprint_links:
        print()
        print("=== fingerprint resolution examples ===")
        for link in fingerprint_links[:20]:
            print(
                f"receipt={link.correction_receipt_number} "
                f"related_date={link.related_filing_date} "
                f"candidates={len(link.candidate_filing_ids)} "
                f"score={link.fingerprint_score}/{link.fingerprint_compared} "
                f"predecessor={link.predecessor_receipt_number}"
            )

    ambiguous = [
        link
        for link in lineage.links
        if link.status is LineageResolutionStatus.AMBIGUOUS
    ]
    if ambiguous:
        print()
        print("=== ambiguous details ===")
        for link in ambiguous:
            print(
                f"receipt={link.correction_receipt_number} "
                f"related_date={link.related_filing_date} "
                f"candidates={','.join(link.candidate_filing_ids)}"
            )

    missing_related_date = [
        link
        for link in lineage.links
        if link.status is LineageResolutionStatus.MISSING_RELATED_DATE
    ]
    if missing_related_date:
        print()
        print("=== missing related date receipts ===")
        for link in missing_related_date:
            print(link.correction_receipt_number)

    unresolved = [
        link
        for link in lineage.links
        if link.status
        not in {
            LineageResolutionStatus.RESOLVED,
            LineageResolutionStatus.RESOLVED_BY_FINGERPRINT,
        }
    ]
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
