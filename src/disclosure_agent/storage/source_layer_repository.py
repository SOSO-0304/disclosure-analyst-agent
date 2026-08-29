"""Staging-first PostgreSQL repository for the generic canonical source layer."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import MetaData, Table, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from disclosure_agent.storage.db_models import (
    CompanyRow,
    LoadRunRow,
    SourceBlockRow,
    SourceDocumentRow,
    SourceFilingRow,
    SourceSectionRow,
    SourceTableRow,
)
from disclosure_agent.storage.effective_canonical import EffectiveCanonicalManifest

STAGING_SCHEMA = "source_staging"
INSERT_BATCH_SIZE = 500
_STAGE_TABLES = ("companies", "source_filings", "source_documents", "source_sections", "source_blocks", "source_tables")


class SourceLayerRepository:
    """Load source rows into an isolated staging schema, validate, then promote."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._metadata = MetaData()
        self._staging: dict[str, Table] = {}

    def prepare_staging(self) -> None:
        """Serialize source loads and clear only the dedicated staging schema."""

        self.session.execute(text("SELECT pg_advisory_xact_lock(742041221)"))
        tables = ", ".join(f"{STAGING_SCHEMA}.{name}" for name in reversed(_STAGE_TABLES))
        self.session.execute(text(f"TRUNCATE TABLE {tables}"))

    def stage_rows(self, table_name: str, rows: Iterable[Mapping[str, Any]]) -> int:
        """Insert typed batches into an UNLOGGED staging table."""

        table = self._stage_table(table_name)
        materialized = list(rows)
        for start in range(0, len(materialized), INSERT_BATCH_SIZE):
            batch = materialized[start : start + INSERT_BATCH_SIZE]
            if batch:
                self.session.execute(table.insert(), batch)
        return len(materialized)

    def validate_staging(
        self,
        *,
        expected_counts: Mapping[str, int],
        manifest: EffectiveCanonicalManifest,
    ) -> dict[str, int]:
        """Check counts and canonical foreign-key relationships before public writes."""

        counts = {
            name: self._count_stage(table)
            for name, table in {
                "companies": "companies",
                "filings": "source_filings",
                "documents": "source_documents",
                "sections": "source_sections",
                "blocks": "source_blocks",
                "tables": "source_tables",
            }.items()
        }
        mismatches = {
            key: (expected_counts[key], actual)
            for key, actual in counts.items()
            if key in expected_counts and expected_counts[key] != actual
        }
        if counts["filings"] != manifest.effective_packages:
            mismatches["filings_vs_manifest"] = (manifest.effective_packages, counts["filings"])
        if counts["documents"] != manifest.effective_documents:
            mismatches["documents_vs_manifest"] = (
                manifest.effective_documents,
                counts["documents"],
            )
        if counts["tables"] != manifest.effective_tables:
            mismatches["tables_vs_manifest"] = (manifest.effective_tables, counts["tables"])
        if mismatches:
            details = ", ".join(
                f"{key}: expected={expected}, actual={actual}"
                for key, (expected, actual) in mismatches.items()
            )
            raise ValueError(f"Source staging count mismatch: {details}")

        checks = {
            "filing_company_missing": """
                SELECT count(*) FROM source_staging.source_filings f
                LEFT JOIN source_staging.companies c USING (corp_code)
                WHERE c.corp_code IS NULL
            """,
            "document_filing_missing": """
                SELECT count(*) FROM source_staging.source_documents d
                LEFT JOIN source_staging.source_filings f USING (filing_id)
                WHERE f.filing_id IS NULL
            """,
            "section_document_missing": """
                SELECT count(*) FROM source_staging.source_sections s
                LEFT JOIN source_staging.source_documents d USING (document_id)
                WHERE d.document_id IS NULL
            """,
            "section_parent_missing": """
                SELECT count(*) FROM source_staging.source_sections child
                LEFT JOIN source_staging.source_sections parent
                  ON parent.section_id = child.parent_section_id
                WHERE child.parent_section_id IS NOT NULL AND parent.section_id IS NULL
            """,
            "block_document_missing": """
                SELECT count(*) FROM source_staging.source_blocks b
                LEFT JOIN source_staging.source_documents d USING (document_id)
                WHERE d.document_id IS NULL
            """,
            "block_section_missing": """
                SELECT count(*) FROM source_staging.source_blocks b
                LEFT JOIN source_staging.source_sections s USING (section_id)
                WHERE b.section_id IS NOT NULL AND s.section_id IS NULL
            """,
            "table_block_missing": """
                SELECT count(*) FROM source_staging.source_tables t
                LEFT JOIN source_staging.source_blocks b USING (block_id)
                WHERE b.block_id IS NULL
            """,
            "table_parent_missing": """
                SELECT count(*) FROM source_staging.source_tables child
                LEFT JOIN source_staging.source_tables parent
                  ON parent.table_id = child.parent_table_id
                WHERE child.parent_table_id IS NOT NULL AND parent.table_id IS NULL
            """,
            "table_block_id_mismatch": """
                SELECT count(*) FROM source_staging.source_tables t
                JOIN source_staging.source_blocks b USING (block_id)
                WHERE b.table_id IS DISTINCT FROM t.table_id
            """,
            "table_blocks_without_table": """
                SELECT count(*) FROM source_staging.source_blocks b
                LEFT JOIN source_staging.source_tables t ON t.table_id = b.table_id
                WHERE b.table_id IS NOT NULL AND t.table_id IS NULL
            """,
        }
        failures: dict[str, int] = {}
        for name, sql in checks.items():
            value = int(self.session.execute(text(sql)).scalar_one())
            if value:
                failures[name] = value

        emitted = self.session.execute(
            text(
                """
                SELECT
                    COALESCE(sum(emitted_section_count), 0),
                    COALESCE(sum(emitted_block_count), 0),
                    COALESCE(sum(emitted_table_count), 0)
                FROM source_staging.source_documents
                """
            )
        ).one()
        for key, expected, actual in (
            ("document_section_sum", counts["sections"], int(emitted[0])),
            ("document_block_sum", counts["blocks"], int(emitted[1])),
            ("document_table_sum", counts["tables"], int(emitted[2])),
        ):
            if expected != actual:
                failures[key] = abs(expected - actual)

        for table_name, id_column in (
            ("source_filings", "filing_id"),
            ("source_documents", "document_id"),
            ("source_sections", "section_id"),
            ("source_blocks", "block_id"),
            ("source_tables", "table_id"),
        ):
            duplicate_count = int(
                self.session.execute(
                    text(
                        f"SELECT count(*) - count(DISTINCT {id_column}) "
                        f"FROM {STAGING_SCHEMA}.{table_name}"
                    )
                ).scalar_one()
            )
            if duplicate_count:
                failures[f"duplicate_{id_column}"] = duplicate_count

        if failures:
            details = ", ".join(f"{key}={value}" for key, value in sorted(failures.items()))
            raise ValueError(f"Source staging integrity failure: {details}")
        return counts

    def promote(
        self,
        *,
        load_run_id: str,
        manifest: EffectiveCanonicalManifest,
        counts: Mapping[str, int],
        started_at: datetime,
    ) -> None:
        """Register the run, upsert staged rows, then prune stale source-only rows."""

        completed_at = datetime.now(UTC)
        run_row = {
            "load_run_id": load_run_id,
            "status": "completed",
            "base_sha256": manifest.base_sha256,
            "overlay_sha256": manifest.overlay_sha256,
            "manifest_sha256": manifest.sha256,
            "manifest": manifest.to_dict(),
            "counts": dict(counts),
            "started_at": started_at,
            "completed_at": completed_at,
        }
        statement = insert(LoadRunRow).values(run_row)
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["load_run_id"],
                set_={
                    column.name: getattr(statement.excluded, column.name)
                    for column in LoadRunRow.__table__.columns
                    if column.name != "load_run_id"
                },
            )
        )

        self._promote_table(CompanyRow, "companies", ("corp_code",))
        self._promote_table(SourceFilingRow, "source_filings", ("filing_id",))
        self._promote_table(SourceDocumentRow, "source_documents", ("document_id",))
        self._promote_table(SourceSectionRow, "source_sections", ("section_id",))
        self._promote_table(SourceBlockRow, "source_blocks", ("block_id",))
        self._promote_table(SourceTableRow, "source_tables", ("table_id",))
        self._prune_stale_source_rows()

    def _stage_table(self, name: str) -> Table:
        if name not in self._staging:
            bind = self.session.get_bind()
            self._staging[name] = Table(
                name,
                self._metadata,
                schema=STAGING_SCHEMA,
                autoload_with=bind,
            )
        return self._staging[name]

    def _count_stage(self, name: str) -> int:
        table = self._stage_table(name)
        return int(self.session.execute(select(func.count()).select_from(table)).scalar_one())

    def _promote_table(
        self,
        model: type[Any],
        staging_name: str,
        conflict_columns: tuple[str, ...],
    ) -> None:
        final = model.__table__
        staging = self._stage_table(staging_name)
        columns = [column.name for column in final.columns]
        statement = insert(final).from_select(columns, select(*(staging.c[name] for name in columns)))
        excluded = set(conflict_columns)
        update_values = {
            name: getattr(statement.excluded, name) for name in columns if name not in excluded
        }
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=list(conflict_columns),
                set_=update_values,
            )
        )

    def _prune_stale_source_rows(self) -> None:
        statements = (
            """
            DELETE FROM source_tables final
            WHERE NOT EXISTS (
                SELECT 1 FROM source_staging.source_tables stage
                WHERE stage.table_id = final.table_id
            )
            """,
            """
            DELETE FROM source_blocks final
            WHERE NOT EXISTS (
                SELECT 1 FROM source_staging.source_blocks stage
                WHERE stage.block_id = final.block_id
            )
            """,
            """
            DELETE FROM source_sections final
            WHERE NOT EXISTS (
                SELECT 1 FROM source_staging.source_sections stage
                WHERE stage.section_id = final.section_id
            )
            """,
            """
            DELETE FROM source_documents final
            WHERE NOT EXISTS (
                SELECT 1 FROM source_staging.source_documents stage
                WHERE stage.document_id = final.document_id
            )
            """,
            """
            DELETE FROM source_filings final
            WHERE NOT EXISTS (
                SELECT 1 FROM source_staging.source_filings stage
                WHERE stage.filing_id = final.filing_id
            )
            """,
        )
        for statement in statements:
            self.session.execute(text(statement))
