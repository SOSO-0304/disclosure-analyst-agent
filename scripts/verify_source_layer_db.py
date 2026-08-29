"""Verify the promoted generic source layer against its accepted load manifest."""

from __future__ import annotations

import argparse

from sqlalchemy import func, select

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import (
    LoadRunRow,
    SourceBlockRow,
    SourceCompanyRow,
    SourceDocumentRow,
    SourceFilingRow,
    SourceSectionRow,
    SourceTableRow,
)
from disclosure_agent.storage.generic_fact_models import GenericFactRow

EXPECTED_FIXED = {
    "companies": 70,
    "filings": 4204,
    "documents": 4619,
    "tables": 1580832,
}

MODELS = {
    "companies": SourceCompanyRow,
    "filings": SourceFilingRow,
    "documents": SourceDocumentRow,
    "sections": SourceSectionRow,
    "blocks": SourceBlockRow,
    "tables": SourceTableRow,
    "facts": GenericFactRow,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    failures: list[str] = []
    with session_scope(engine) as session:
        runs = session.scalars(select(LoadRunRow).where(LoadRunRow.status == "completed")).all()
        if len(runs) != 1:
            raise SystemExit(f"Expected one deterministic completed load run, found {len(runs)}")
        run = runs[0]
        actual = {
            name: session.scalar(select(func.count()).select_from(model)) or 0
            for name, model in MODELS.items()
        }

    recorded = run.counts
    print("=== source layer database verification ===")
    for name in MODELS:
        expected = recorded.get(name)
        value = actual[name]
        marker = "OK" if expected == value else "MISMATCH"
        print(f"{name:<20} {value:>10}  manifest={expected!s:<10} {marker}")
        if expected != value:
            failures.append(f"{name}: actual={value}, manifest={expected}")

    for name, expected in EXPECTED_FIXED.items():
        value = actual[name]
        if value != expected:
            failures.append(f"{name}: actual={value}, accepted={expected}")

    if actual["facts"] <= 0:
        failures.append("facts: generic fact extraction produced no rows")

    manifest = run.manifest or {}
    if manifest.get("effective_packages") != 4204:
        failures.append("load manifest effective_packages is not 4204")
    if manifest.get("effective_documents") != 4619:
        failures.append("load manifest effective_documents is not 4619")
    if manifest.get("effective_tables") != 1580832:
        failures.append("load manifest effective_tables is not 1580832")

    print(f"load run             {run.load_run_id}")
    print(f"manifest sha256      {run.manifest_sha256}")
    if failures:
        raise SystemExit("Source layer verification failed: " + "; ".join(failures))
    print("status               verified")


if __name__ == "__main__":
    main()
