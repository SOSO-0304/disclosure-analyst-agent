#!/usr/bin/env python3
"""Resolve a contract-lifecycle question and ask HCX to explain deterministic findings."""

from __future__ import annotations

import argparse

from disclosure_agent.config import get_settings
from disclosure_agent.llm.grounded_generation import generate_grounded_answer
from disclosure_agent.llm.hcx_client import HCX_MODEL, HcxClient
from disclosure_agent.llm.prompts import GROUNDING_SYSTEM_PROMPT, build_grounded_answer_prompt
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


def _api_key() -> str:
    settings = get_settings()
    api_key = settings.clova_studio_api_key or settings.hcx_api_key
    if not api_key:
        raise SystemExit("Set CLOVA_STUDIO_API_KEY or HCX_API_KEY in .env before generation")
    return api_key


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--query", required=True)
    parser.add_argument("--company", help="Fallback company when the query omits one")
    parser.add_argument("--year", type=int, help="Fallback original contract year")
    parser.add_argument("--max-total-chars", type=int, default=12000)
    parser.add_argument("--max-completion-tokens", type=int, default=1400)
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
    print(f"model                           {HCX_MODEL}")

    if result.status == "NO_MATCH":
        print("generation                      skipped_no_confirmed_termination")
        print("answer:")
        print(
            f"제공된 공시에서 {target.company_name}가 {target.year}년에 체결한 계약 중 "
            "원계약과 확정적으로 연결된 해지 계약을 확인하지 못했습니다."
        )
        print()
        print(render_source_references(source_references))
        if args.show_evidence:
            print()
            print(render_evidence_pack(pack))
        return

    if result.status == "PARTIAL":
        print("generation                      skipped_incomplete_correction_lineage")
        print("answer:")
        print(
            f"{target.company_name}가 {target.year}년에 체결한 계약 중 이후 해지된 계약은 "
            "확인되지만, 일부 계약의 정정공시 계보가 불완전해 최종 계약조건까지는 "
            "확정할 수 없습니다."
        )
        print()
        print(render_source_references(source_references))
        if args.show_evidence:
            print()
            print(render_evidence_pack(pack))
        return

    prompt = build_grounded_answer_prompt(args.query, pack)
    with HcxClient(_api_key()) as client:
        answer = generate_grounded_answer(
            client,
            system_prompt=GROUNDING_SYSTEM_PROMPT,
            user_prompt=prompt,
            evidence_count=len(pack.items),
            max_completion_tokens=args.max_completion_tokens,
        )

    print(f"finish_reason                   {answer.finish_reason}")
    print(f"prompt_tokens                   {answer.prompt_tokens}")
    print(f"completion_tokens               {answer.completion_tokens}")
    print(f"total_tokens                    {answer.total_tokens}")
    print("answer:")
    print(answer.content)
    print()
    print(render_source_references(source_references))

    if args.show_evidence:
        print()
        print(render_evidence_pack(pack))


if __name__ == "__main__":
    main()
