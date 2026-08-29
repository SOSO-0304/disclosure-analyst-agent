"""Project generic facts into database-ready mappings."""

from __future__ import annotations

from typing import Any

from disclosure_agent.facts.generic import GenericFact

Row = dict[str, Any]


def project_generic_fact(fact: GenericFact, *, load_run_id: str) -> Row:
    """Convert one extracted generic fact into a persistence mapping."""

    return {
        "fact_id": fact.fact_id,
        "load_run_id": load_run_id,
        "filing_id": fact.filing_id,
        "document_id": fact.document_id,
        "section_id": fact.section_id,
        "block_id": fact.block_id,
        "table_id": fact.table_id,
        "row_index": fact.row_index,
        "column_index": fact.column_index,
        "fact_kind": fact.fact_kind.value,
        "label_text": fact.label_text,
        "header_text": fact.header_text,
        "path_text": fact.path_text,
        "value_text": fact.value_text,
        "raw_value": fact.raw_value,
        "numeric_value": fact.numeric_value,
        "unit_raw": fact.unit_raw,
        "currency": fact.currency,
        "concept_code": fact.concept_code,
        "context_ref": fact.context_ref,
        "source_locator": fact.source_locator,
    }
