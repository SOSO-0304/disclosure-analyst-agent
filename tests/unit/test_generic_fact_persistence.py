from __future__ import annotations

from decimal import Decimal

from disclosure_agent.facts.generic import FactKind, GenericFact
from disclosure_agent.storage.generic_fact_models import GenericFactRow
from disclosure_agent.storage.generic_fact_persistence import project_generic_fact


def test_generic_fact_model_registers_with_shared_metadata() -> None:
    metadata = GenericFactRow.__table__.metadata
    assert "source_tables" in metadata.tables
    assert "generic_facts" in metadata.tables


def test_project_generic_fact_preserves_atomic_value_and_evidence() -> None:
    fact = GenericFact(
        fact_id="fact:abc",
        fact_kind=FactKind.NUMERIC,
        filing_id="filing:1",
        document_id="document:1",
        section_id="section:1",
        block_id="block:1",
        table_id="table:1",
        row_index=2,
        column_index=3,
        label_text="매출액",
        header_text="2025년",
        path_text="재무 | 2025년 | 매출액",
        value_text="1,000",
        raw_value="1,000",
        numeric_value=Decimal("1000"),
        unit_raw="백만원",
        currency="KRW",
        concept_code="ifrs-full_Revenue",
        context_ref="ctx-2025",
        source_locator={"source_file_id": "source:1", "xpath": "/TABLE[1]/TR[2]/TD[2]"},
    )

    row = project_generic_fact(fact, load_run_id="run:1")

    assert row["load_run_id"] == "run:1"
    assert row["fact_kind"] == "numeric"
    assert row["numeric_value"] == Decimal("1000")
    assert row["concept_code"] == "ifrs-full_Revenue"
    assert row["source_locator"] == fact.source_locator
