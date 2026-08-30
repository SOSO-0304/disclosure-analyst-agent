#!/usr/bin/env python3
"""Run hybrid retrieval and generate one grounded HyperCLOVA X answer."""

from __future__ import annotations

import argparse
import re

from disclosure_agent.config import get_settings
from disclosure_agent.llm.clova_embedding_client import ClovaEmbeddingClient
from disclosure_agent.llm.hcx_client import HCX_MODEL, HcxClient
from disclosure_agent.llm.prompts import GROUNDING_SYSTEM_PROMPT, build_grounded_answer_prompt
from disclosure_agent.retrieval.evidence_pack import (
    build_hybrid_evidence_pack,
    render_evidence_pack,
)
from disclosure_agent.retrieval.hybrid_search import HybridRetriever
from disclosure_agent.retrieval.query_router import route_query
from disclosure_agent.retrieval.source_references import (
    build_source_references,
    render_source_references,
)
from disclosure_agent.storage.database import get_engine, session_scope

NO_MATCH_ANSWER = "제공된 공시에서 확인되지 않는다."


def _api_key() -> str:
    settings = get_settings()
    api_key = settings.clova_studio_api_key or settings.hcx_api_key
    if not api_key:
        raise SystemExit("Set CLOVA_STUDIO_API_KEY or HCX_API_KEY in .env before generation")
    return api_key


def _infer_year(
    *,
    explicit_year: int | None,
    report_name: str | None,
    query: str,
) -> int | None:
    if explicit_year is not None:
        return explicit_year

    for text in (report_name or "", query):
        match = re.search(r"\b(20\d{2})\b", text)
        if match is not None:
            return int(match.group(1))
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--query", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--year", type=int)
    parser.add_argument("--filing-id")
    parser.add_argument("--report-name")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=40)
    parser.add_argument("--max-total-chars", type=int, default=12000)
    parser.add_argument("--max-completion-tokens", type=int, default=1200)
    parser.add_argument("--show-evidence", action="store_true")
    args = parser.parse_args()

    if args.candidate_k < args.top_k:
        raise SystemExit("--candidate-k must be greater than or equal to --top-k")
    if args.candidate_k > 50:
        raise SystemExit("--candidate-k must be at most 50")

    route = route_query(args.query)
    year = _infer_year(
        explicit_year=args.year,
        report_name=args.report_name,
        query=args.query,
    )

    api_key = _api_key()
    query_vector = None
    if route.uses_semantic:
        with ClovaEmbeddingClient(api_key) as client:
            query_vector = client.embed(args.query).vector

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        result = HybridRetriever(session).retrieve(
            query=args.query,
            route=route,
            query_vector=query_vector,
            company_name=args.company,
            year=year,
            filing_id=args.filing_id,
            report_name=args.report_name,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
        )
        pack = build_hybrid_evidence_pack(
            args.query,
            structured_items=result.structured_items,
            semantic_hits=result.semantic_hits,
            max_semantic_items=args.top_k,
            max_total_chars=args.max_total_chars,
        )
        source_references = build_source_references(session, pack)

    print("=== GROUNDED ANSWER ===")
    print(f"company                         {args.company}")
    print(f"year                            {year or 'unresolved'}")
    print(f"model                           {HCX_MODEL}")
    print(f"rails                           {','.join(rail.value for rail in route.rails)}")
    print(f"evidence_count                  {len(pack.items)}")

    if pack.retrieval_status == "NO_MATCH":
        print("generation                      skipped_no_match")
        print("answer:")
        print(NO_MATCH_ANSWER)
        print()
        print(render_source_references(source_references))
        return

    prompt = build_grounded_answer_prompt(args.query, pack)
    with HcxClient(api_key) as client:
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
