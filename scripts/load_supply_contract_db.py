"""Load the validated Supply Contract lifecycle subset into PostgreSQL."""

from __future__ import annotations

import argparse
from pathlib import Path

from disclosure_agent.storage.database import create_schema, get_engine, session_scope
from disclosure_agent.storage.jsonl import read_canonical
from disclosure_agent.storage.repositories import SupplyContractRepository
from disclosure_agent.storage.supply_contract_persistence import (
    build_supply_contract_persistence_bundle,
)

DEFAULT_INPUT = Path("data/processed/subsets/supply-contract-lifecycle-v22.jsonl")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--database-url")
    parser.add_argument(
        "--create-schema",
        action="store_true",
        help="Create declared SQLAlchemy tables before loading (dev/bootstrap only).",
    )
    args = parser.parse_args()

    if not args.input.is_file():
        raise SystemExit(f"Subset not found: {args.input}")

    packages = list(read_canonical(args.input))
    bundle = build_supply_contract_persistence_bundle(packages)
    engine = get_engine(args.database_url)

    if args.create_schema:
        create_schema(engine)

    with session_scope(engine) as session:
        SupplyContractRepository(session).upsert_bundle(bundle)

    print("=== supply contract PostgreSQL load ===")
    for name, count in bundle.counts.items():
        print(f"{name:<30} {count:>8}")
    print("status                         committed")


if __name__ == "__main__":
    main()
