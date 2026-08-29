from __future__ import annotations

from collections import Counter
from pathlib import Path

from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader
from disclosure_agent.storage.jsonl import read_canonical

INPUT = Path("data/processed/subsets/supply-contract-lifecycle-v22.jsonl")
SUCCESSION_RECEIPT_NUMBER = "20241224800227"


def main() -> None:
    packages = [
        package
        for package in read_canonical(INPUT)
        if package.filing.receipt_number == SUCCESSION_RECEIPT_NUMBER
    ]
    if len(packages) != 1:
        raise SystemExit(
            "Expected exactly one succession filing "
            f"({SUCCESSION_RECEIPT_NUMBER}), found {len(packages)}"
        )

    package = packages[0]
    reader = ExchangeFieldReader()
    fields = reader.read_package(package)

    print("=== supply contract succession profile ===")
    print(f"receipt                  {package.filing.receipt_number}")
    print(f"company                  {package.company.listed_name}")
    print(f"subtype                  {package.filing.document_subtype}")
    print(f"report                   {package.filing.report_name_raw}")
    print(f"semantic fields          {len(fields)}")
    print(f"unique paths             {len({field.path_key for field in fields})}")
    print()

    counts = Counter(field.path_key for field in fields)
    print("=== semantic fields ===")
    for field in fields:
        value = " ".join(field.value.split())
        print(
            f"path={field.path_key}\n"
            f"  value={value}\n"
            f"  table={field.table_id} row={field.row_index} col={field.value_column_index}"
        )

    repeated = [(path, count) for path, count in counts.items() if count > 1]
    if repeated:
        print()
        print("=== repeated paths ===")
        for path, count in sorted(repeated, key=lambda item: (-item[1], item[0])):
            print(f"{count:3d}  {path}")


if __name__ == "__main__":
    main()
