#!/usr/bin/env python3
"""Resolve a fundraising question, aggregate canonical events, and ask HCX to explain it."""

from __future__ import annotations

import argparse

from disclosure_agent.config import get_settings
from disclosure_agent.llm.hcx_client import HCX_MODEL, HcxClient
from disclosure_agent.llm.prompts import GROUNDING_SYSTEM_PROMPT, build_grounded_answer_prompt
from disclosure_agent.retrieval.evidence_pack import render_evidence_pack
from disclosure_agent.retrieval.fundraising_evidence import build_fundraising_evidence_pack
from disclosure_agent.retrieval.fundraising_query_resolver import resolve_fundraising_query_target
from disclosure_agent.retrieval.source_references import (
    build_source_references,
    render_source_references,
)
from disclosure_agent.services.fundraising_analysis import FundraisingAnalysisService
from disclosure_agent.storage.database import get_engine, session_scope

PARTIAL_ANSWER = "제공된 공시에서 자금조달 금액을 모두 확정할 수 없습니다."
NO_MATCH_ANSWER = "제공된 공시에서 해당 연도의 자금조달 이벤트를 확인하지 못했습니다."


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
    parser.add_argument("--year", type=int, help="Fallback year when the query omits one")
    parser.add_argument("--max-total-chars", type=int, default=12000)
    parser.add_argument("--max-completion-tokens", type=int, default=1200)
    parser.add_argument("--show-evidence", action="store_true")
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        target = resolve_fundraising_query_target(
            session,
            query=args.query,
            fallback_company=args.company,
            fallback_year=args.year,
        )
        if target.status != "RESOLVED" or target.company_name is None or target.year is None:
            raise SystemExit(
                f"Fundraising target resolution failed: {target.reason or target.status}"
            )

        result = FundraisingAnalysisService(session).analyze(
            company_name=target.company_name,
            year=target.year,
        )
        pack = build_fundraising_evidence_pack(
            session,
            query=args.query,
            result=result,
            max_total_chars=args.max_total_chars,
        )
        source_references = build_source_references(session, pack)

    print("=== GROUNDED FUNDRAISING ANSWER ===")
    print(f"query                           {args.query}")
    print(f"company                         {result.company_name}")
    print(f"year                            {result.year}")
    print(f"analysis_status                 {result.status}")
    print(f"event_count                     {result.event_count}")
    print(f"evidence_count                  {len(pack.items)}")
    print(f"model                           {HCX_MODEL}")

    if result.status == "NO_MATCH":
        print("generation                      skipped_no_match")
        print("answer:")
        print(NO_MATCH_ANSWER)
        print()
        print(render_source_references(source_references))
        if args.show_evidence:
            print()
            print(render_evidence_pack(pack))
        return

    if result.status == "PARTIAL":
        print("generation                      skipped_incomplete_analysis")
        print("answer:")
        print(PARTIAL_ANSWER)
        print()
        print(render_source_references(source_references))
        if args.show_evidence:
            print()
            print(render_evidence_pack(pack))
        return

    prompt = build_grounded_answer_prompt(args.query, pack)
    with HcxClient(_api_key()) as client:
        answer = client.answer(
            system_prompt=GROUNDING_SYSTEM_PROMPT,
            user_prompt=prompt,
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
