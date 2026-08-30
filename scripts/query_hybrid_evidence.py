#!/usr/bin/env python3
"""Run routed SQL plus semantic retrieval and render one evidence pack."""

from __future__ import annotations

import argparse
import re

from disclosure_agent.config import get_settings
from disclosure_agent.llm.clova_embedding_client import ClovaEmbeddingClient
from disclosure_agent.retrieval.evidence_pack import (
    build_hybrid_evidence_pack,
    render_evidence_pack,
)
from disclosure_agent.retrieval.hybrid_search import HybridRetriever
from disclosure_agent.retrieval.query_router import route_query
from disclosure_agent.storage.database import get_engine, session_scope


def _api_key() -> str:
    settings = get_settings()
    api_key = settings.clova_studio_api_key or settings.hcx_api_key
    if not api_key:
        raise SystemExit("Set CLOVA_STUDIO_API_KEY or HCX_API_KEY in .env before retrieval")
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

    query_vector = None
    if route.uses_semantic:
        with ClovaEmbeddingClient(_api_key()) as client:
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

    print("=== HYBRID RETRIEVAL ===")
    print(f"company                         {args.company}")
    print(f"year                            {year or 'unresolved'}")
    print(f"rails                           {','.join(rail.value for rail in route.rails)}")
    print(f"route_terms                     {','.join(route.matched_terms) or '-'}")
    print(f"structured_items                {len(result.structured_items)}")
    print(f"semantic_items                  {len(result.semantic_hits)}")
    skipped = ",".join(rail.value for rail in result.skipped_rails) or "-"
    print(f"skipped_rails                   {skipped}")
    print()
    print(render_evidence_pack(pack))


if __name__ == "__main__":
    main()
