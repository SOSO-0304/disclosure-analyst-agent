#!/usr/bin/env python3
"""Inspect one generic fact and the canonical table/block context around it."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import SourceBlockRow, SourceTableRow
from disclosure_agent.storage.generic_fact_models import GenericFactRow


def _pretty(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--fact-id", required=True)
    parser.add_argument("--radius", type=int, default=5)
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        fact = session.get(GenericFactRow, args.fact_id)
        if fact is None:
            raise SystemExit(f"fact not found: {args.fact_id}")

        block = session.get(SourceBlockRow, fact.block_id)
        table = session.get(SourceTableRow, fact.table_id)

        print("=== FACT ===")
        print(f"fact_id          {fact.fact_id}")
        print(f"filing_id        {fact.filing_id}")
        print(f"document_id      {fact.document_id}")
        print(f"section_id       {fact.section_id}")
        print(f"block_id         {fact.block_id}")
        print(f"table_id         {fact.table_id}")
        print(f"row/col          {fact.row_index}/{fact.column_index}")
        print(f"label            {fact.label_text!r}")
        print(f"header           {fact.header_text!r}")
        print(f"raw_value        {fact.raw_value!r}")
        print(f"numeric_value    {fact.numeric_value}")
        print(f"unit_raw         {fact.unit_raw!r}")
        print(f"currency         {fact.currency!r}")
        print(f"path             {fact.path_text}")
        print("source_locator:")
        print(_pretty(fact.source_locator))

        if table is not None:
            print("\n=== TABLE ===")
            print(f"caption_raw       {table.caption_raw!r}")
            print(f"caption_normalized {table.caption_normalized!r}")
            print(f"row_count         {table.row_count}")
            print(f"column_count      {table.column_count}")
            print(f"header_rows       {table.header_row_indices}")
            print(f"parent_table_id   {table.parent_table_id!r}")
            print("normalized_text:")
            print(table.normalized_text)
            print("grid:")
            print(_pretty(table.grid))
            print("source_locator:")
            print(_pretty(table.source_locator))
            print("attributes_raw:")
            print(_pretty(table.attributes_raw))

        if block is not None:
            print("\n=== TARGET BLOCK ===")
            print(f"block_order       {block.block_order}")
            print(f"block_type        {block.block_type}")
            print(f"text_raw          {block.text_raw!r}")
            print(f"text_normalized   {block.text_normalized!r}")
            print("source_locator:")
            print(_pretty(block.source_locator))
            print("attributes_raw:")
            print(_pretty(block.attributes_raw))

            lower = max(0, block.block_order - args.radius)
            upper = block.block_order + args.radius
            nearby = session.scalars(
                select(SourceBlockRow)
                .where(
                    SourceBlockRow.document_id == block.document_id,
                    SourceBlockRow.block_order >= lower,
                    SourceBlockRow.block_order <= upper,
                )
                .order_by(SourceBlockRow.block_order)
            ).all()

            print("\n=== NEARBY BLOCKS ===")
            for item in nearby:
                marker = " <TARGET>" if item.block_id == block.block_id else ""
                print(
                    f"[{item.block_order}] type={item.block_type} "
                    f"section={item.section_id} table={item.table_id}{marker}"
                )
                if item.block_type == "table" and item.table_id:
                    nearby_table = session.get(SourceTableRow, item.table_id)
                    if nearby_table is not None:
                        caption = nearby_table.caption_normalized or nearby_table.caption_raw
                        print(f"  caption={caption!r}")
                        print(f"  rows={nearby_table.row_count} cols={nearby_table.column_count}")
                        print(f"  text={nearby_table.normalized_text[:1200]!r}")
                else:
                    text = item.text_normalized or item.text_raw or ""
                    print(f"  text={text[:1200]!r}")


if __name__ == "__main__":
    main()
