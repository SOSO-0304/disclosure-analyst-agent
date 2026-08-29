"""Project canonical packages into compact generic source-layer database rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from disclosure_agent.domain.models import FilingPackage

Row = dict[str, Any]


@dataclass(frozen=True, slots=True)
class SourcePackageRows:
    """Database-ready rows for one effective canonical filing package."""

    company: Row
    filing: Row
    documents: tuple[Row, ...]
    sections: tuple[Row, ...]
    blocks: tuple[Row, ...]
    tables: tuple[Row, ...]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "companies": 1,
            "filings": 1,
            "documents": len(self.documents),
            "sections": len(self.sections),
            "blocks": len(self.blocks),
            "tables": len(self.tables),
        }


def project_source_package(package: FilingPackage, *, load_run_id: str) -> SourcePackageRows:
    """Convert one package without exploding canonical table cells into SQL rows."""

    company: Row = {
        "corp_code": package.company.corp_code,
        "stock_code": package.company.stock_code,
        "corp_name": package.company.corp_name,
        "listed_name": package.company.listed_name,
        "industry": package.company.industry,
        "sector": package.company.sector,
    }
    filing: Row = {
        "filing_id": package.filing_id,
        "load_run_id": load_run_id,
        "corp_code": package.company.corp_code,
        "receipt_number": package.filing.receipt_number,
        "document_group": package.filing.document_group.value,
        "document_subtype": package.filing.document_subtype,
        "report_name": package.filing.report_name_raw,
        "receipt_date": package.filing.receipt_date,
        "filer_name": package.filing.filer_name,
        "is_correction": package.correction.is_correction,
        "schema_version": package.schema_version,
    }

    documents: list[Row] = []
    sections: list[Row] = []
    blocks: list[Row] = []
    tables: list[Row] = []

    for document in package.documents:
        summary = document.parse_summary
        documents.append(
            {
                "document_id": document.document_id,
                "filing_id": package.filing_id,
                "load_run_id": load_run_id,
                "document_role": document.document_role.value,
                "title_raw": document.title_raw,
                "title_normalized": document.title_normalized,
                "primary_source_file_id": document.primary_source_file_id,
                "source_file_ids": list(document.source_file_ids),
                "parse_status": summary.status.value,
                "parser_name": summary.parser_name,
                "parser_version": summary.parser_version,
                "recovered": summary.recovered,
                "emitted_section_count": summary.emitted_section_count,
                "emitted_block_count": summary.emitted_block_count,
                "emitted_table_count": summary.emitted_table_count,
            }
        )

        for section in document.sections:
            sections.append(
                {
                    "section_id": section.section_id,
                    "document_id": document.document_id,
                    "filing_id": package.filing_id,
                    "load_run_id": load_run_id,
                    "parent_section_id": section.parent_section_id,
                    "section_order": section.order,
                    "section_level": section.level,
                    "title_raw": section.title_raw,
                    "title_normalized": section.title_normalized,
                    "source_locator": _json(section.source_locator),
                    "attributes_raw": section.attributes_raw,
                }
            )

        for block in document.blocks:
            table_id = block.table.table_id if block.table is not None else None
            locator = _json(block.source_locator)
            blocks.append(
                {
                    "block_id": block.block_id,
                    "document_id": document.document_id,
                    "filing_id": package.filing_id,
                    "load_run_id": load_run_id,
                    "section_id": block.section_id,
                    "block_order": block.order,
                    "block_type": block.block_type.value,
                    "text_raw": block.text_raw,
                    "text_normalized": block.text_normalized,
                    "heading_level": block.heading_level,
                    "table_id": table_id,
                    "source_locator": locator,
                    "attributes_raw": block.attributes_raw,
                }
            )
            if block.table is None:
                continue
            table = block.table
            cells = [cell.model_dump(mode="json") for cell in table.cells]
            normalized_parts = [
                value
                for value in (
                    table.caption_normalized,
                    *(cell.text_normalized for cell in table.cells),
                )
                if value
            ]
            tables.append(
                {
                    "table_id": table.table_id,
                    "block_id": block.block_id,
                    "document_id": document.document_id,
                    "filing_id": package.filing_id,
                    "load_run_id": load_run_id,
                    "caption_raw": table.caption_raw,
                    "caption_normalized": table.caption_normalized,
                    "row_count": table.row_count,
                    "column_count": table.column_count,
                    "header_row_indices": list(table.header_row_indices),
                    "parent_table_id": table.parent_table_id,
                    "parent_cell_locator": _json(table.parent_cell_locator),
                    "source_locator": locator,
                    "normalized_text": " ".join(normalized_parts),
                    "grid": {
                        "header_row_indices": list(table.header_row_indices),
                        "cells": cells,
                    },
                    "attributes_raw": table.attributes_raw,
                }
            )

    return SourcePackageRows(
        company=company,
        filing=filing,
        documents=tuple(documents),
        sections=tuple(sections),
        blocks=tuple(blocks),
        tables=tuple(tables),
    )


def _json(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value
