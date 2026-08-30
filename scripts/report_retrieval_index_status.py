#!/usr/bin/env python3
"""Report retrieval chunk and embedding coverage across the source corpus."""

from __future__ import annotations

import argparse

from sqlalchemy import case, func, select

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import SourceCompanyRow
from disclosure_agent.storage.retrieval_chunk_models import RetrievalChunkRow


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--show-companies", action="store_true")
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        source_company_count = session.scalar(select(func.count()).select_from(SourceCompanyRow)) or 0
        indexed_company_count = session.scalar(
            select(func.count(func.distinct(RetrievalChunkRow.corp_code)))
        ) or 0

        totals = session.execute(
            select(
                func.count(RetrievalChunkRow.chunk_id).label("chunks"),
                func.sum(
                    case((RetrievalChunkRow.embedding.is_not(None), 1), else_=0)
                ).label("embedded"),
                func.sum(RetrievalChunkRow.char_count).label("characters"),
                func.sum(
                    case(
                        (RetrievalChunkRow.embedding.is_not(None), RetrievalChunkRow.char_count),
                        else_=0,
                    )
                ).label("embedded_characters"),
                func.sum(RetrievalChunkRow.embedding_input_tokens).label("embedded_tokens"),
            )
        ).one()

        chunks = int(totals.chunks or 0)
        embedded = int(totals.embedded or 0)
        characters = int(totals.characters or 0)
        embedded_characters = int(totals.embedded_characters or 0)
        embedded_tokens = int(totals.embedded_tokens or 0)
        pending = chunks - embedded
        pending_characters = characters - embedded_characters

        tokens_per_char = 0.0
        estimated_pending_tokens = 0
        if embedded_characters > 0 and embedded_tokens > 0:
            tokens_per_char = embedded_tokens / embedded_characters
            estimated_pending_tokens = round(pending_characters * tokens_per_char)

        company_rows = ()
        if args.show_companies:
            company_rows = session.execute(
                select(
                    SourceCompanyRow.listed_name.label("company_name"),
                    func.count(RetrievalChunkRow.chunk_id).label("chunks"),
                    func.sum(
                        case((RetrievalChunkRow.embedding.is_not(None), 1), else_=0)
                    ).label("embedded"),
                )
                .outerjoin(
                    RetrievalChunkRow,
                    RetrievalChunkRow.corp_code == SourceCompanyRow.corp_code,
                )
                .group_by(SourceCompanyRow.corp_code, SourceCompanyRow.listed_name)
                .order_by(SourceCompanyRow.listed_name, SourceCompanyRow.corp_code)
            ).all()

    print("=== retrieval index status ===")
    print(f"source companies                {source_company_count}")
    print(f"indexed companies               {indexed_company_count}")
    print(f"chunks                          {chunks}")
    print(f"embedded                        {embedded}")
    print(f"pending                         {pending}")
    print(f"characters                      {characters}")
    print(f"embedded characters             {embedded_characters}")
    print(f"embedded input tokens           {embedded_tokens}")
    print(f"observed tokens/char            {tokens_per_char:.6f}")
    print(f"estimated pending tokens        {estimated_pending_tokens}")

    if args.show_companies:
        print()
        print("=== company coverage ===")
        for row in company_rows:
            row_chunks = int(row.chunks or 0)
            row_embedded = int(row.embedded or 0)
            print(
                f"{row.company_name:<30} chunks={row_chunks:<7} "
                f"embedded={row_embedded:<7} pending={row_chunks - row_embedded}"
            )


if __name__ == "__main__":
    main()
