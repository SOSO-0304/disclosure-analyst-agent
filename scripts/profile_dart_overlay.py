"""Profile before/after preservation metrics for the selective DART 2.2.1 overlay."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from disclosure_agent.storage.jsonl import read_canonical

DEFAULT_BASE = Path("data/processed/canonical-v22-smoke.jsonl")
DEFAULT_OVERLAY = Path("data/processed/canonical-dart-221-overlay.jsonl")


def _dart_documents(package):
    return [
        document
        for document in package.documents
        if document.parse_summary.parser_name == "DartParser"
    ]


def _has_markup_recovery(document) -> bool:
    return any(issue.issue_code == "markup_recovery" for issue in document.parse_issues)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY)
    args = parser.parse_args()

    overlay = {package.filing_id: package for package in read_canonical(args.overlay)}
    if not overlay:
        raise SystemExit(f"Overlay is empty: {args.overlay}")

    stats: Counter[str] = Counter()
    base_docs = {}
    overlay_docs = {}
    receipts = {}

    for package in overlay.values():
        stats["overlay_packages"] += 1
        receipts[package.filing_id] = package.filing.receipt_number
        for document in _dart_documents(package):
            overlay_docs[document.document_id] = document
            stats[f"after_status:{document.parse_summary.status.value}"] += 1
            stats[f"after_version:{document.parse_summary.parser_version}"] += 1
            stats["after_tables"] += document.parse_summary.emitted_table_count
            if _has_markup_recovery(document):
                stats["after_markup_recovery_docs"] += 1

    found_base_ids: set[str] = set()
    for package in read_canonical(args.base):
        if package.filing_id not in overlay:
            continue
        found_base_ids.add(package.filing_id)
        if package.filing.receipt_number != receipts[package.filing_id]:
            raise SystemExit(f"Receipt mismatch for overlay filing {package.filing_id}")
        for document in _dart_documents(package):
            base_docs[document.document_id] = document
            stats[f"before_status:{document.parse_summary.status.value}"] += 1
            stats[f"before_version:{document.parse_summary.parser_version}"] += 1
            stats["before_tables"] += document.parse_summary.emitted_table_count
            if _has_markup_recovery(document):
                stats["before_markup_recovery_docs"] += 1

    missing = sorted(set(overlay) - found_base_ids)
    if missing:
        raise SystemExit(f"Overlay filing IDs missing from base: {missing[:10]}")

    common_docs = sorted(set(base_docs) & set(overlay_docs))
    stats["compared_dart_documents"] = len(common_docs)
    for document_id in common_docs:
        before = base_docs[document_id]
        after = overlay_docs[document_id]
        before_tables = before.parse_summary.emitted_table_count
        after_tables = after.parse_summary.emitted_table_count
        if after_tables > before_tables:
            stats["documents_table_increase"] += 1
        elif after_tables < before_tables:
            stats["documents_table_decrease"] += 1
        else:
            stats["documents_table_equal"] += 1
        stats[
            f"transition:{before.parse_summary.status.value}->{after.parse_summary.status.value}"
        ] += 1

    print("=== DART 2.2.1 overlay profile ===")
    print(f"overlay packages                 {stats['overlay_packages']}")
    print(f"compared DART documents         {stats['compared_dart_documents']}")
    print(f"before tables                   {stats['before_tables']}")
    print(f"after tables                    {stats['after_tables']}")
    print(f"table delta                     {stats['after_tables'] - stats['before_tables']:+d}")
    print(
        "table doc deltas                "
        f"increase={stats['documents_table_increase']} "
        f"equal={stats['documents_table_equal']} "
        f"decrease={stats['documents_table_decrease']}"
    )
    print(
        "markup recovery docs            "
        f"before={stats['before_markup_recovery_docs']} "
        f"after={stats['after_markup_recovery_docs']}"
    )

    print("\nstatus transitions")
    for key in sorted(key for key in stats if key.startswith("transition:")):
        print(f"{key.removeprefix('transition:'):<28} {stats[key]}")

    print("\nafter parser versions")
    for key in sorted(key for key in stats if key.startswith("after_version:")):
        print(f"{key.removeprefix('after_version:'):<28} {stats[key]}")

    remaining_partial = [
        document
        for document in overlay_docs.values()
        if document.parse_summary.status.value == "partial"
    ]
    if remaining_partial:
        print("\nremaining partial DART documents")
        for document in remaining_partial:
            issues = sorted({issue.issue_code for issue in document.parse_issues})
            print(f"{document.document_id} issues={','.join(issues)}")


if __name__ == "__main__":
    main()
