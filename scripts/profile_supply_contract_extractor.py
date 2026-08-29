"""Profile Supply Contract extractor coverage over the canonical subset."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from disclosure_agent.extractors.supply_contract import extract_supply_contract
from disclosure_agent.storage.jsonl import read_canonical

SUBSET = Path("data/processed/subsets/supply-contract-v22.jsonl")
FIELDS = (
    "contract_type",
    "contract_name",
    "contract_amount",
    "recent_revenue",
    "revenue_ratio",
    "counterparty",
    "relationship",
    "region",
    "contract_start_date",
    "contract_end_date",
    "contract_date",
    "major_conditions",
)


def main() -> None:
    if not SUBSET.is_file():
        raise SystemExit(f"Subset not found: {SUBSET}")

    present = Counter()
    evidence_present = Counter()
    packages = 0
    corrections = 0

    for package in read_canonical(SUBSET):
        packages += 1
        corrections += int(package.correction.is_correction)
        result = extract_supply_contract(package)
        event = result.event
        for field in FIELDS:
            if getattr(event, field) is not None:
                present[field] += 1
            if field in result.evidence:
                evidence_present[field] += 1

    print("=== supply contract extractor coverage ===")
    print(f"packages                  {packages}")
    print(f"corrections               {corrections}")
    print()
    print("field                     value     evidence   coverage")
    print("------------------------  --------  ---------  --------")
    for field in FIELDS:
        value_count = present[field]
        evidence_count = evidence_present[field]
        coverage = value_count / packages * 100 if packages else 0.0
        print(f"{field:<24}  {value_count:>8}  {evidence_count:>9}  {coverage:>7.2f}%")


if __name__ == "__main__":
    main()
