#!/usr/bin/env python3
"""Run semantic retrieval with lightweight lexical reranking."""

from __future__ import annotations

import argparse

from disclosure_agent.config import get_settings
from disclosure_agent.llm.clova_embedding_client import ClovaEmbeddingClient
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
    parser.add_argument("--year", type=int)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=40)
    parser.add_argument("--preview-chars", type=int, default=900)
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

    print("=== reranked semantic retrieval ===")
    print(f"query                           {args.query}")
    print(f"company                         {args.company or 'all'}")
    print(f"year                            {args.year or 'all'}")
    print(f"candidates                      {len(candidates)}")
    print(f"hits                            {len(hits)}")

    for index, item in enumerate(hits, start=1):
        hit = item.hit
        matched = ",".join(item.matched_terms) if item.matched_terms else "-"
        print(
            f"\n[{index}] final={item.final_score:.6f} semantic={hit.similarity:.6f} "
            f"lexical={item.lexical_score:.6f}"
        )
        print(f"  matched={matched} report={hit.report_name!r}")
        print(f"  chunk={hit.chunk_id} filing={hit.filing_id}")
        print(f"  document={hit.document_id} section={hit.section_id}")
        print(f"  tables={','.join(hit.table_ids) if hit.table_ids else '-'}")
        preview = hit.content_text[: args.preview_chars].replace("\n", " ")
        print(f"  text={preview}")


if __name__ == "__main__":
    main()
