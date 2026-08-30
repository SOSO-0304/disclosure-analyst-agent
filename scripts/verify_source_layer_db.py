"""Verify the promoted generic source layer against its accepted load manifest."""

from __future__ import annotations

import argparse

from sqlalchemy import func, select, text

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

EXPECTED_FIXED = {
    "companies": 70,
    "filings": 4204,
    "documents": 4619,
    "tables": 1580832,
}
EXPECTED_PARSE_STATUS = {
    "success": 4513,
    "partial": 106,
}
STAGING_TABLES = (
    "source_companies",
    "source_filings",
    "source_documents",
    "source_sections",
    "source_blocks",
    "source_tables",
)

MODELS = {
    "companies": SourceCompanyRow,
    "filings": SourceFilingRow,
    "documents": SourceDocumentRow,
    "sections": SourceSectionRow,
    "blocks": SourceBlockRow,
    "tables": SourceTableRow,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    failures: list[str] = []
    with session_scope(engine) as session:
        runs = session.scalars(
            select(LoadRunRow).where(LoadRunRow.status == "completed")
        ).all()
        if len(runs) != 1:
            raise SystemExit(f"Expected one deterministic completed load run, found {len(runs)}")
        run = runs[0]
        actual = {
            name: session.scalar(select(func.count()).select_from(model)) or 0
            for name, model in MODELS.items()
        }
        parse_status_counts = dict(
            session.execute(
                select(SourceDocumentRow.parse_status, func.count()).group_by(
                    SourceDocumentRow.parse_status
                )
            ).all()
        )
        staging_not_empty = [
            table_name
            for table_name in STAGING_TABLES
            if session.execute(
                text(
                    f"SELECT EXISTS (SELECT 1 FROM source_staging.{table_name})"
                )
            ).scalar_one()
        ]

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

    for status in sorted(set(EXPECTED_PARSE_STATUS) | set(parse_status_counts)):
        expected = EXPECTED_PARSE_STATUS.get(status, 0)
        value = parse_status_counts.get(status, 0)
        marker = "OK" if expected == value else "MISMATCH"
        print(f"parse:{status:<14} {value:>10}  accepted={expected:<10} {marker}")
    if parse_status_counts != EXPECTED_PARSE_STATUS:
        failures.append(
            f"parse statuses: actual={parse_status_counts}, "
            f"accepted={EXPECTED_PARSE_STATUS}"
        )

    staging_marker = "OK" if not staging_not_empty else "NOT EMPTY"
    print(f"staging empty{'':<8} {staging_marker}")
    if staging_not_empty:
        failures.append("staging tables not empty: " + ", ".join(staging_not_empty))

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
