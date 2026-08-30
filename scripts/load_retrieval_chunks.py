#!/usr/bin/env python3
"""Build semantic retrieval chunks from persisted canonical source blocks."""

from __future__ import annotations

import argparse

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.retrieval_chunk_repository import RetrievalChunkRepository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--company", action="append", default=[])
    parser.add_argument("--max-chars", type=int, default=3200)
    parser.add_argument("--overlap-chars", type=int, default=240)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        stats = RetrievalChunkRepository(session).rebuild_chunks(
            companies=tuple(args.company),
            max_chars=args.max_chars,
            overlap_chars=args.overlap_chars,
            batch_size=args.batch_size,
        )

    print("=== retrieval chunk load ===")
    print(f"companies                       {len(args.company) or 'all'}")
    print(f"documents                       {stats.documents}")
    print(f"source blocks                   {stats.source_blocks}")
    print(f"chunks                          {stats.chunks}")
    print(f"table chunks                    {stats.table_chunks}")
    print(f"characters                      {stats.characters}")


if __name__ == "__main__":
    main()
