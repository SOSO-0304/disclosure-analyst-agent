"""Profile the 20 Supply Contract termination filings in the lifecycle subset."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader
from disclosure_agent.storage.jsonl import read_canonical

SUBSET = Path("data/processed/subsets/supply-contract-lifecycle-v22.jsonl")
TERMINATION_SUBTYPE = "단일판매공급계약해지"


def main() -> None:
    if not SUBSET.is_file():
        raise SystemExit(f"Subset not found: {SUBSET}")

    reader = ExchangeFieldReader()
    path_counts: Counter[str] = Counter()
    path_package_counts: Counter[str] = Counter()
    value_examples: dict[str, Counter[str]] = {}
    packages = 0

    for package in read_canonical(SUBSET):
        if package.filing.document_subtype != TERMINATION_SUBTYPE:
            continue
        packages += 1
        fields = reader.read_package(package)
        seen_paths: set[str] = set()
        for field in fields:
            path = field.path_key
            path_counts[path] += 1
            seen_paths.add(path)
            values = value_examples.setdefault(path, Counter())
            value = " ".join(field.value.split())
            if value:
                values[value] += 1
        for path in seen_paths:
            path_package_counts[path] += 1

    print("=== supply contract termination field profile ===")
    print(f"packages                  {packages}")
    print(f"semantic fields           {sum(path_counts.values())}")
    print(f"unique paths              {len(path_counts)}")
    print()
    print("count  packages  path")
    print("-----  --------  ----")
    for path, count in path_counts.most_common():
        print(f"{count:>5}  {path_package_counts[path]:>8}  {path}")

    print()
    print("=== representative values ===")
    for path, _ in path_counts.most_common(30):
        print(f"[{path}]")
        for value, count in value_examples[path].most_common(5):
            print(f"  {count:>3}  {value}")


if __name__ == "__main__":
    main()
