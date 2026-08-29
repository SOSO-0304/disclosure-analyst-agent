"""Profile Supply Contract termination-to-formation lineage resolution."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader
from disclosure_agent.extractors.supply_contract_termination_lineage import (
    FORMATION_SUBTYPE,
    TERMINATION_SUBTYPE,
    TerminationLineageStatus,
    resolve_supply_contract_termination_lineage,
)
from disclosure_agent.storage.jsonl import read_canonical

SUBSET = Path("data/processed/subsets/supply-contract-lifecycle-v22.jsonl")


def main() -> None:
    if not SUBSET.is_file():
        raise SystemExit(f"Subset not found: {SUBSET}")

    packages = list(read_canonical(SUBSET))
    relevant = [
        package
        for package in packages
        if package.filing.document_subtype in {FORMATION_SUBTYPE, TERMINATION_SUBTYPE}
    ]
    by_filing_id = {package.filing_id: package for package in relevant}
    lineage = resolve_supply_contract_termination_lineage(
        relevant,
        reader=ExchangeFieldReader(),
    )
    counts = Counter(link.status for link in lineage.links)
    total = len(lineage.links)
    resolved = sum(
        counts[status]
        for status in (
            TerminationLineageStatus.RESOLVED,
            TerminationLineageStatus.RESOLVED_BY_FINGERPRINT,
        )
    )
    formation_total = sum(
        package.filing.document_subtype == FORMATION_SUBTYPE for package in relevant
    )

    print("=== supply contract termination lineage ===")
    print(f"formation packages              {formation_total}")
    print(f"termination packages            {total}")
    print(f"resolved total                  {resolved}/{total}")
    print()
    print("status                         count   coverage")
    print("-----------------------------  ------  --------")
    for status in TerminationLineageStatus:
        count = counts[status]
        coverage = count / total * 100 if total else 0.0
        print(f"{status.value:<29}  {count:>6}  {coverage:>7.2f}%")

    print()
    print("=== termination links ===")
    for link in lineage.links:
        dates = ",".join(item.isoformat() for item in link.related_formation_dates) or "-"
        matched = link.matched_formation_receipt_number or "-"
        root = "-"
        if link.root_formation_filing_id is not None:
            root_package = by_filing_id.get(link.root_formation_filing_id)
            root = (
                root_package.filing.receipt_number
                if root_package is not None
                else link.root_formation_filing_id
            )
        score = "-"
        if link.fingerprint_score is not None:
            score = f"{link.fingerprint_score}/{link.fingerprint_compared}"
        print(
            f"termination={link.termination_receipt_number} "
            f"status={link.status.value} dates={dates} "
            f"candidates={len(link.candidate_filing_ids)} "
            f"matched={matched} root={root} score={score}"
        )


if __name__ == "__main__":
    main()
