#!/usr/bin/env python3
"""Build retrieval chunks only for source companies that are not indexed yet."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import SourceCompanyRow
from disclosure_agent.storage.retrieval_chunk_models import RetrievalChunkRow
from disclosure_agent.storage.retrieval_chunk_repository import RetrievalChunkRepository


@dataclass(frozen=True, slots=True)
class CompanyScope:
    """One source company that still needs retrieval chunks."""

    corp_code: str
    company_name: str


def missing_companies(session: Session) -> tuple[CompanyScope, ...]:
    """Return source companies with no persisted retrieval chunk."""

    indexed_corp_codes = select(RetrievalChunkRow.corp_code).distinct()
    rows = session.execute(
        select(SourceCompanyRow.corp_code, SourceCompanyRow.listed_name)
        .where(~SourceCompanyRow.corp_code.in_(indexed_corp_codes))
        .order_by(SourceCompanyRow.listed_name, SourceCompanyRow.corp_code)
    )
    return tuple(
        CompanyScope(corp_code=row.corp_code, company_name=row.listed_name) for row in rows
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--max-chars", type=int, default=3200)
    parser.add_argument("--overlap-chars", type=int, default=240)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--limit-companies", type=int)
    args = parser.parse_args()

    if args.limit_companies is not None and args.limit_companies < 1:
        raise SystemExit("--limit-companies must be at least 1")

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        companies = missing_companies(session)

    if args.limit_companies is not None:
        companies = companies[: args.limit_companies]

    print("=== missing retrieval chunk load ===")
    print(f"companies to build              {len(companies)}")

    total_documents = 0
    total_source_blocks = 0
    total_chunks = 0
    total_table_chunks = 0
    total_characters = 0

    for index, company in enumerate(companies, start=1):
        with session_scope(engine) as session:
            stats = RetrievalChunkRepository(session).rebuild_chunks(
                companies=(company.company_name,),
                max_chars=args.max_chars,
                overlap_chars=args.overlap_chars,
                batch_size=args.batch_size,
            )

        total_documents += stats.documents
        total_source_blocks += stats.source_blocks
        total_chunks += stats.chunks
        total_table_chunks += stats.table_chunks
        total_characters += stats.characters
        print(
            f"[{index}/{len(companies)}] {company.company_name} "
            f"documents={stats.documents} chunks={stats.chunks} "
            f"characters={stats.characters}"
        )

    print()
    print("=== missing retrieval chunk totals ===")
    print(f"documents                       {total_documents}")
    print(f"source blocks                   {total_source_blocks}")
    print(f"chunks                          {total_chunks}")
    print(f"table chunks                    {total_table_chunks}")
    print(f"characters                      {total_characters}")


if __name__ == "__main__":
    main()
