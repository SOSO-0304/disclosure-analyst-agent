#!/usr/bin/env python3
"""Build a bounded evidence pack from reranked semantic retrieval."""

from __future__ import annotations

import argparse

from disclosure_agent.config import get_settings
from disclosure_agent.llm.clova_embedding_client import ClovaEmbeddingClient
from disclosure_agent.retrieval.evidence_pack import (
    build_semantic_evidence_pack,
    render_evidence_pack,
)
from disclosure_agent.retrieval.reranker import rerank_semantic_hits
from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.retrieval_embedding_repository import RetrievalEmbeddingRepository


def _api_key() -> str:
    settings = get_settings()
    api_key = settings.clova_studio_api_key or settings.hcx_api_key
    if not api_key:
        raise SystemExit("Set CLOVA_STUDIO_API_KEY or HCX_API_KEY in .env before retrieval")
    return api_key


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--query", required=True)
    parser.add_argument("--company")
    parser.add_argument("--filing-id")
    parser.add_argument("--report-name")
    parser.add_argument("--year", type=int)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=40)
    parser.add_argument("--max-chars-per-item", type=int, default=3200)
    parser.add_argument("--max-total-chars", type=int, default=12000)
    args = parser.parse_args()

    if args.candidate_k < args.top_k:
        raise SystemExit("--candidate-k must be greater than or equal to --top-k")
    if args.candidate_k > 50:
        raise SystemExit("--candidate-k must be at most 50")

    with ClovaEmbeddingClient(_api_key()) as client:
        query_embedding = client.embed(args.query)

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        candidates = RetrievalEmbeddingRepository(session).search(
            query_vector=query_embedding.vector,
            company_name=args.company,
            filing_id=args.filing_id,
            report_name=args.report_name,
            year=args.year,
            top_k=args.candidate_k,
        )

    hits = rerank_semantic_hits(
        args.query,
        candidates,
        company_name=args.company,
        year=args.year,
        top_k=args.top_k,
    )
    pack = build_semantic_evidence_pack(
        args.query,
        hits,
        max_items=args.top_k,
        max_chars_per_item=args.max_chars_per_item,
        max_total_chars=args.max_total_chars,
    )
    print(render_evidence_pack(pack))


if __name__ == "__main__":
    main()
