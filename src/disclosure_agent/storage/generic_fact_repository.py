"""Staging-first PostgreSQL repository for generic canonical facts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import MetaData, Table, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from disclosure_agent.storage.generic_fact_models import GenericFactRow

STAGING_SCHEMA = "source_staging"
INSERT_BATCH_SIZE = 1000


class GenericFactRepository:
    """Stage generic facts, validate source references, then promote atomically."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._metadata = MetaData()
        self._staging: Table | None = None

    def prepare_staging(self) -> None:
        """Clear only generic-fact staging rows inside the source-load transaction."""

        self.session.execute(text(f"TRUNCATE TABLE {STAGING_SCHEMA}.generic_facts"))

    def stage_rows(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Insert rows into the UNLOGGED generic-fact staging table."""

        table = self._stage_table()
        materialized = list(rows)
        for start in range(0, len(materialized), INSERT_BATCH_SIZE):
            batch = materialized[start : start + INSERT_BATCH_SIZE]
            if batch:
                self.session.execute(table.insert(), batch)
        return len(materialized)

    def validate_staging(self, *, expected_count: int) -> int:
        """Validate count, identity uniqueness, and references to staged source rows."""

        table = self._stage_table()
        actual = int(self.session.execute(select(func.count()).select_from(table)).scalar_one())
        if actual != expected_count:
            raise ValueError(
                f"Generic fact staging count mismatch: expected={expected_count}, actual={actual}"
            )

        checks = {
            "fact_filing_missing": """
                SELECT count(*) FROM source_staging.generic_facts g
                LEFT JOIN source_staging.source_filings f USING (filing_id)
                WHERE f.filing_id IS NULL
            """,
            "fact_document_missing": """
                SELECT count(*) FROM source_staging.generic_facts g
                LEFT JOIN source_staging.source_documents d USING (document_id)
                WHERE d.document_id IS NULL
            """,
            "fact_block_missing": """
                SELECT count(*) FROM source_staging.generic_facts g
                LEFT JOIN source_staging.source_blocks b USING (block_id)
                WHERE b.block_id IS NULL
            """,
            "fact_table_missing": """
                SELECT count(*) FROM source_staging.generic_facts g
                LEFT JOIN source_staging.source_tables t USING (table_id)
                WHERE t.table_id IS NULL
            """,
            "fact_source_identity_mismatch": """
                SELECT count(*) FROM source_staging.generic_facts g
                JOIN source_staging.source_tables t USING (table_id)
                WHERE t.block_id IS DISTINCT FROM g.block_id
                   OR t.document_id IS DISTINCT FROM g.document_id
                   OR t.filing_id IS DISTINCT FROM g.filing_id
            """,
            "fact_block_section_mismatch": """
                SELECT count(*) FROM source_staging.generic_facts g
                JOIN source_staging.source_blocks b USING (block_id)
                WHERE b.section_id IS DISTINCT FROM g.section_id
            """,
            "duplicate_fact_id": """
                SELECT count(*) - count(DISTINCT fact_id)
                FROM source_staging.generic_facts
            """,
            "duplicate_fact_cell": """
                SELECT COALESCE(sum(n - 1), 0)
                FROM (
                    SELECT count(*) AS n
                    FROM source_staging.generic_facts
                    GROUP BY table_id, row_index, column_index
                    HAVING count(*) > 1
                ) duplicates
            """,
        }
        failures: dict[str, int] = {}
        for name, sql in checks.items():
            value = int(self.session.execute(text(sql)).scalar_one())
            if value:
                failures[name] = value
        if failures:
            details = ", ".join(f"{key}={value}" for key, value in sorted(failures.items()))
            raise ValueError(f"Generic fact staging integrity failure: {details}")
        return actual

    def promote(self) -> None:
        """Upsert current facts and prune facts no longer emitted by the extractor."""

        final = GenericFactRow.__table__
        staging = self._stage_table()
        columns = [column.name for column in final.columns]
        selected = select(*(staging.c[name] for name in columns))
        statement = insert(final).from_select(columns, selected)
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["fact_id"],
                set_={
                    name: getattr(statement.excluded, name) for name in columns if name != "fact_id"
                },
            )
        )
        self.session.execute(
            text(
                """
                DELETE FROM generic_facts final
                WHERE NOT EXISTS (
                    SELECT 1 FROM source_staging.generic_facts stage
                    WHERE stage.fact_id = final.fact_id
                )
                """
            )
        )

    def _stage_table(self) -> Table:
        if self._staging is None:
            bind = self.session.get_bind()
            self._staging = Table(
                "generic_facts",
                self._metadata,
                schema=STAGING_SCHEMA,
                autoload_with=bind,
            )
        return self._staging
