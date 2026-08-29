from __future__ import annotations

from disclosure_agent.storage.db_models import Base


def test_supply_contract_vertical_slice_tables_are_declared() -> None:
    expected = {
        "companies",
        "disclosures",
        "disclosure_events",
        "supply_contract_events",
        "supply_contract_correction_links",
        "supply_contract_termination_events",
        "supply_contract_termination_links",
        "supply_contract_succession_events",
        "supply_contract_lifecycle",
        "supply_contract_succession_lifecycle",
        "event_evidence",
    }

    assert expected <= set(Base.metadata.tables)


def test_evidence_keeps_canonical_table_provenance_columns() -> None:
    table = Base.metadata.tables["event_evidence"]

    assert {
        "event_id",
        "filing_id",
        "attribute",
        "document_id",
        "table_id",
        "path",
        "row_index",
        "value_column_index",
        "value_text",
        "raw_value",
        "value_locator",
        "label_locators",
    } <= set(table.columns.keys())


def test_lifecycle_tables_keep_resolution_state() -> None:
    lifecycle = Base.metadata.tables["supply_contract_lifecycle"]
    termination = Base.metadata.tables["supply_contract_termination_links"]
    succession = Base.metadata.tables["supply_contract_succession_lifecycle"]

    assert {
        "root_filing_id",
        "latest_formation_filing_id",
        "correction_lineage_complete",
        "status",
        "termination_filing_ids",
    } <= set(lifecycle.columns.keys())
    assert {
        "matched_formation_filing_id",
        "root_filing_id",
        "status",
        "candidate_filing_ids",
    } <= set(termination.columns.keys())
    assert {
        "predecessor_scope",
        "status",
        "source_contract_reference_dates",
        "matched_source_root_filing_ids",
    } <= set(succession.columns.keys())
