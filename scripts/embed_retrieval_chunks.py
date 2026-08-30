#!/usr/bin/env python3
"""Embed persisted retrieval chunks with CLOVA Studio Embedding v2."""

from __future__ import annotations

import argparse

from disclosure_agent.config import get_settings
from disclosure_agent.llm.clova_embedding_client import (
    CLOVA_EMBEDDING_MODEL,
    ClovaEmbeddingClient,
)
from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.retrieval_embedding_repository import RetrievalEmbeddingRepository


def _api_key() -> str:
    settings = get_settings()
    api_key = settings.clova_studio_api_key or settings.hcx_api_key
    if not api_key:
        raise SystemExit("Set CLOVA_STUDIO_API_KEY or HCX_API_KEY in .env before embedding")
    return api_key


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--company")
    scope.add_argument("--all-companies", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")

    engine = get_engine(args.database_url)
    embedded = 0
    input_tokens = 0

    with ClovaEmbeddingClient(_api_key()) as client:
        with session_scope(engine) as session:
            repository = RetrievalEmbeddingRepository(session)
            while args.limit is None or embedded < args.limit:
                remaining = args.limit - embedded if args.limit is not None else args.batch_size
                batch_limit = min(args.batch_size, remaining)
                batch = repository.pending_chunks(
                    company_name=args.company,
                    limit=batch_limit,
                )
                if not batch:
                    break

                for chunk in batch:
                    result = client.embed(chunk.content_text)
                    repository.save_embedding(
                        chunk_id=chunk.chunk_id,
                        vector=result.vector,
                        model=CLOVA_EMBEDDING_MODEL,
                        input_tokens=result.input_tokens,
                    )
                    embedded += 1
                    input_tokens += result.input_tokens

                session.commit()
                print(
                    f"embedded={embedded} input_tokens={input_tokens} "
                    f"last_chunk={batch[-1].chunk_id}"
                )

    print("=== retrieval embedding load ===")
    print(f"company                         {args.company or 'all'}")
    print(f"embedded                        {embedded}")
    print(f"input tokens                    {input_tokens}")
    print(f"model                           {CLOVA_EMBEDDING_MODEL}")


if __name__ == "__main__":
    main()
