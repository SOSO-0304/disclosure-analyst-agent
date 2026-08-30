#!/usr/bin/env python3
"""Report listed-name and legal-name aliases in the source company master."""

from __future__ import annotations

import argparse

from sqlalchemy import select

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import SourceCompanyRow


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        rows = session.execute(
            select(
                SourceCompanyRow.corp_code,
                SourceCompanyRow.stock_code,
                SourceCompanyRow.listed_name,
                SourceCompanyRow.corp_name,
            )
            .where(SourceCompanyRow.listed_name != SourceCompanyRow.corp_name)
            .order_by(SourceCompanyRow.listed_name)
        ).all()

    print("=== company aliases ===")
    print(f"alias pairs                      {len(rows)}")
    for row in rows:
        print(
            f"{row.listed_name:<20} -> {row.corp_name:<25} "
            f"stock={row.stock_code or '-'} corp={row.corp_code}"
        )


if __name__ == "__main__":
    main()
