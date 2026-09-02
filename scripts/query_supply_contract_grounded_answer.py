#!/usr/bin/env python3
"""Resolve a Supply Contract termination question with deterministic evidence."""

from __future__ import annotations

import argparse

from disclosure_agent.rendering.supply_contract import (
    render_supply_contract_termination_answer,
)
from disclosure_agent.retrieval.evidence_pack import render_evidence_pack
from disclosure_agent.retrieval.source_references import (
    build_source_references,
    render_source_references,
)
from disclosure_agent.retrieval.supply_contract_evidence import (
    build_supply_contract_evidence_pack,
)
from disclosure_agent.retrieval.supply_contract_query_resolver import (
    resolve_supply_contract_query_target,
)
from disclosure_agent.services.supply_contract_analysis import (
    find_terminated_contracts_formed_in_year,
)
from disclosure_agent.storage.database import get_engine, session_scope


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--query", required=True)
    parser.add_argument("--company", help="Fallback company when the query omits one")
    parser.add_argument("--year", type=int, help="Fallback original contract year")
    parser.add_argument("--max-total-chars", type=int, default=12000)
    parser.add_argument("--show-evidence", action="store_true")
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        target = resolve_supply_contract_query_target(
            session,
            query=args.query,
            fallback_company=args.company,
            fallback_year=args.year,
        )
        if target.status != "RESOLVED" or target.company_name is None or target.year is None:
            raise SystemExit(
                f"Supply contract target resolution failed: {target.reason or target.status}"
            )

        result = find_terminated_contracts_formed_in_year(
            session=session,
            year=target.year,
            company_name=target.company_name,
        )
        pack = build_supply_contract_evidence_pack(
            session,
            query=args.query,
            result=result,
            max_total_chars=args.max_total_chars,
        )
        source_references = build_source_references(session, pack)

    print("=== GROUNDED SUPPLY CONTRACT ANSWER ===")
    print(f"query                           {args.query}")
    print(f"company                         {target.company_name}")
    print(f"formation_year                  {target.year}")
    print(f"analysis_status                 {result.status}")
    print(f"terminated_contracts            {len(result.findings)}")
    print(f"evidence_count                  {len(pack.items)}")
    print("generator                       deterministic")

    if result.status == "NO_MATCH":
        print("answer:")
        print(
            f"제공된 공시에서 {target.company_name}가 {target.year}년에 체결한 계약 중 "
            "원계약과 확정적으로 연결된 해지 계약을 확인하지 못했습니다."
        )
    elif result.status == "PARTIAL":
        print("answer:")
        print(
            f"{target.company_name}가 {target.year}년에 체결한 계약 중 이후 해지된 계약은 "
            "확인되지만, 일부 계약의 정정공시 계보가 불완전해 최종 계약조건까지는 "
            "확정할 수 없습니다."
        )
    else:
        print("answer:")
        print(render_supply_contract_termination_answer(result, pack))

    print()
    print(render_source_references(source_references))

    if args.show_evidence:
        print()
        print(render_evidence_pack(pack))


if __name__ == "__main__":
    main()
