from __future__ import annotations

from datetime import date

from sqlalchemy.dialects import postgresql

from disclosure_agent.storage.supply_contract_query_repository import (
    _terminated_contracts_statement,
)


def test_terminated_contract_query_uses_original_contract_year_and_company() -> None:
    statement = _terminated_contracts_statement(year=2025, company_name="테스트회사")
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "root_event.contract_date" in sql
    assert "supply_contract_lifecycle.status" in sql
    assert "supply_contract_lifecycle.correction_count" in sql
    assert "supply_contract_lifecycle.correction_lineage_complete" in sql
    assert "supply_contract_termination_links.status" in sql
    assert "supply_contract_termination_events.termination_date >= root_event.contract_date" in sql
    assert "companies.listed_name" in sql
    assert date(2025, 1, 1) in compiled.params.values()
    assert date(2026, 1, 1) in compiled.params.values()
    assert "테스트회사" in compiled.params.values()
