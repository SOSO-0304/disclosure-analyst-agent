"""Query contracts formed in a year that were later deterministically terminated."""

from __future__ import annotations

import argparse

from disclosure_agent.rendering.money import format_krw
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
    print(f"status                   {result.status}")
    print(f"exists                   {result.exists}")
    print(f"findings                 {len(result.findings)}")

    for index, finding in enumerate(result.findings, start=1):
        contract = finding.contract
        contract_amount = (
            format_krw(contract.contract_amount)
            if contract.contract_amount is not None
            else "-"
        )

        print()
        print(f"[{index}] {contract.company_name}")
        print(f"contract_date            {contract.contract_date.isoformat()}")
        print(f"contract_name            {contract.contract_name or '-'}")
        print(f"counterparty             {contract.counterparty or '-'}")
        print(f"contract_amount          {contract_amount}")
        print(f"root_receipt             {contract.root_receipt_number}")
        print(f"latest_receipt           {contract.latest_formation_receipt_number}")
        print(f"correction_count         {contract.correction_count}")
        print(f"lineage_complete         {contract.correction_lineage_complete}")
        print(f"termination_link         {contract.termination_link_status}")
        print(
            "termination_date         "
            f"{contract.termination_date.isoformat() if contract.termination_date else '-'}"
        )
        print(f"termination_reason       {contract.termination_reason or '-'}")
        print(f"termination_receipt      {contract.termination_receipt_number}")

        print("formation chain")
        for step_index, step in enumerate(finding.formation_steps, start=1):
            formation = step.formation
            formation_amount = (
                format_krw(formation.contract_amount)
                if formation.contract_amount is not None
                else "-"
            )
            stage = "root" if not formation.is_correction else f"correction_{step_index - 1}"
            if formation.is_latest_for_root:
                stage += "_latest"
            print(
                f"  [{step_index}] {stage} filing={formation.receipt_number} "
                f"receipt_date={formation.receipt_date.isoformat()}"
            )
            print(f"      contract_name       {formation.contract_name or '-'}")
            print(f"      contract_amount     {formation_amount}")
            print(f"      counterparty        {formation.counterparty or '-'}")
            print(f"      lineage_status      {formation.lineage_status or '-'}")

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
