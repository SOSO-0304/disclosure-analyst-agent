"""Profile correction-lineage signals in the Supply Contract canonical subset."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from disclosure_agent.extractors.exchange_corrections import ExchangeCorrectionReader
from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader
from disclosure_agent.storage.jsonl import read_canonical

SUBSET = Path("data/processed/subsets/supply-contract-v22.jsonl")
LINEAGE_PATHS = (
    "정정일자",
    "1. 정정관련 공시서류",
    "2. 정정관련 공시서류제출일",
    "3. 정정사유",
    "※ 관련공시",
)


def _first_non_missing(values: list[str]) -> str | None:
    for value in values:
        text = value.strip()
        if text and text != "-":
            return text
    return None


def main() -> None:
    if not SUBSET.is_file():
        raise SystemExit(f"Subset not found: {SUBSET}")

    field_reader = ExchangeFieldReader()
    correction_reader = ExchangeCorrectionReader()
    packages = 0
    corrections = 0
    original_receipt_metadata = 0
    correction_table_packages = 0
    correction_rows = 0
    path_presence = Counter()
    path_values: dict[str, Counter[str]] = {path: Counter() for path in LINEAGE_PATHS}
    corrected_items = Counter()
    correction_reasons = Counter()

    for package in read_canonical(SUBSET):
        packages += 1
        if not package.correction.is_correction:
            continue

        corrections += 1
        if package.correction.original_receipt_number is not None:
            original_receipt_metadata += 1

        fields = field_reader.read_package(package)
        by_path: dict[str, list[str]] = {}
        for field in fields:
            by_path.setdefault(field.path_key, []).append(field.value)

        for path in LINEAGE_PATHS:
            value = _first_non_missing(by_path.get(path, []))
            if value is None:
                continue
            path_presence[path] += 1
            path_values[path][value] += 1
            if path == "3. 정정사유":
                correction_reasons[value] += 1

        rows = correction_reader.read_package(package)
        if rows:
            correction_table_packages += 1
            correction_rows += len(rows)
            corrected_items.update(row.item.strip() or "<blank>" for row in rows)

    print("=== supply contract correction lineage profile ===")
    print(f"packages                         {packages}")
    print(f"corrections                      {corrections}")
    print(f"metadata original receipt        {original_receipt_metadata}/{corrections}")
    print(f"correction-table packages        {correction_table_packages}/{corrections}")
    print(f"correction rows                  {correction_rows}")
    print()
    print("lineage field                         packages   coverage")
    print("----------------------------------  ---------  --------")
    for path in LINEAGE_PATHS:
        count = path_presence[path]
        coverage = count / corrections * 100 if corrections else 0.0
        print(f"{path:<34}  {count:>9}  {coverage:>7.2f}%")

    print("\n=== top correction reasons ===")
    for value, count in correction_reasons.most_common(20):
        print(f"{count:>5}  {value}")

    print("\n=== top corrected items ===")
    for value, count in corrected_items.most_common(50):
        print(f"{count:>5}  {value}")

    print("\n=== representative lineage values ===")
    for path in LINEAGE_PATHS:
        print(f"[{path}]")
        for value, count in path_values[path].most_common(10):
            print(f"  {count:>5}  {value}")


if __name__ == "__main__":
    main()
