"""Query contracts formed in a year that were later deterministically terminated."""

from __future__ import annotations

import argparse

from disclosure_agent.services.supply_contract_analysis import (
    find_terminated_contracts_formed_in_year,
)
from disclosure_agent.storage.database import get_engine, session_scope


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--company")
    parser.add_argument("--database-url")
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        result = find_terminated_contracts_formed_in_year(
            session=session,
            year=args.year,
            company_name=args.company,
        )

    print("=== terminated contracts formed in year ===")
    print(f"year                     {result.year}")
    print(f"company                  {result.company_name or '-'}")
    print(f"exists                   {result.exists}")
    print(f"findings                 {len(result.findings)}")

    for index, finding in enumerate(result.findings, start=1):
        contract = finding.contract
        print()
        print(f"[{index}] {contract.company_name}")
        print(f"contract_date            {contract.contract_date.isoformat()}")
        print(f"contract_name            {contract.contract_name or '-'}")
        print(f"counterparty             {contract.counterparty or '-'}")
        print(f"contract_amount          {contract.contract_amount or '-'}")
        print(f"root_receipt             {contract.root_receipt_number}")
        print(f"latest_receipt           {contract.latest_formation_receipt_number}")
        print(f"lineage_complete         {contract.correction_lineage_complete}")
        print(
            "termination_date         "
            f"{contract.termination_date.isoformat() if contract.termination_date else '-'}"
        )
        print(f"termination_reason       {contract.termination_reason or '-'}")
        print(f"termination_receipt      {contract.termination_receipt_number}")
        print("root formation evidence")
        for evidence in finding.root_formation_evidence:
            print(
                f"  {evidence.attribute}: {evidence.value_text} "
                f"[table={evidence.table_id} row={evidence.row_index} "
                f"col={evidence.value_column_index}]"
            )
        print("latest formation evidence")
        for evidence in finding.latest_formation_evidence:
            print(
                f"  {evidence.attribute}: {evidence.value_text} "
                f"[table={evidence.table_id} row={evidence.row_index} "
                f"col={evidence.value_column_index}]"
            )
        print("termination evidence")
        for evidence in finding.termination_evidence:
            print(
                f"  {evidence.attribute}: {evidence.value_text} "
                f"[table={evidence.table_id} row={evidence.row_index} "
                f"col={evidence.value_column_index}]"
            )


if __name__ == "__main__":
    main()
