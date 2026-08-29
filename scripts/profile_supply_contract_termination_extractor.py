"""Profile typed Supply Contract Termination extractor coverage."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from disclosure_agent.extractors.supply_contract_termination import (
    extract_supply_contract_termination,
)
from disclosure_agent.storage.jsonl import read_canonical

SUBSET = Path("data/processed/subsets/supply-contract-lifecycle-v22.jsonl")
TERMINATION_SUBTYPE = "단일판매공급계약해지"
FIELDS = (
    "termination_type",
    "contract_name",
    "termination_amount",
    "recent_revenue",
    "revenue_ratio",
    "counterparty",
    "relationship",
    "contract_start_date",
    "contract_end_date",
    "termination_reason",
    "termination_date",
    "notes",
    "related_disclosures",
)


def main() -> None:
    if not SUBSET.is_file():
        raise SystemExit(f"Lifecycle subset not found: {SUBSET}")

    packages = [
        package
        for package in read_canonical(SUBSET)
        if package.filing.document_subtype == TERMINATION_SUBTYPE
    ]
    if not packages:
        raise SystemExit("No termination packages found")

    values = Counter()
    evidence = Counter()
    for package in packages:
        result = extract_supply_contract_termination(package)
        for field_name in FIELDS:
            if getattr(result.event, field_name) is not None:
                values[field_name] += 1
            if field_name in result.evidence:
                evidence[field_name] += 1

    print("=== supply contract termination extractor coverage ===")
    print(f"packages                  {len(packages)}")
    print()
    print("field                     value     evidence   coverage")
    print("------------------------  --------  ---------  --------")
    for field_name in FIELDS:
        count = values[field_name]
        coverage = count / len(packages) * 100
        print(
            f"{field_name:<24}  {count:>8}  {evidence[field_name]:>9}  {coverage:>7.2f}%"
        )


if __name__ == "__main__":
    main()
