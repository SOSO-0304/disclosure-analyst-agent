"""Profile semantic Exchange-field paths across the Supply Contract subset."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader
from disclosure_agent.storage.jsonl import read_canonical

DEFAULT_INPUT = Path("data/processed/subsets/supply-contract-v22.jsonl")
DEFAULT_TOP = 100


def profile(path: Path, *, top: int = DEFAULT_TOP) -> None:
    reader = ExchangeFieldReader()
    package_count = 0
    field_count = 0
    path_counts: Counter[str] = Counter()
    package_path_presence: Counter[str] = Counter()

    for package in read_canonical(path):
        package_count += 1
        fields = reader.read_package(package)
        field_count += len(fields)
        keys = [field.path_key for field in fields]
        path_counts.update(keys)
        package_path_presence.update(set(keys))

    print("=== supply contract field profile ===")
    print(f"packages                  {package_count}")
    print(f"semantic fields           {field_count}")
    print(f"unique paths              {len(path_counts)}")
    print()
    print("count  packages  path")
    print("-----  --------  ----")
    for path_key, count in path_counts.most_common(top):
        present = package_path_presence[path_key]
        print(f"{count:5d}  {present:8d}  {path_key}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Profile ExchangeFieldReader output for Supply Contract filings."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    profile(args.input, top=args.top)


if __name__ == "__main__":
    main()
