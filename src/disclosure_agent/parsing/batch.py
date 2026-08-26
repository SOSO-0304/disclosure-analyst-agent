"""Validated and atomic batch conversion from corpus sources to canonical JSONL."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import orjson

from disclosure_agent.domain.models import ParseStatus
from disclosure_agent.inventory.builder import InventoryBuilder, validate_manifest_rows
from disclosure_agent.parsing.document_parser import DocumentParser
from disclosure_agent.storage.jsonl import append_canonical


def parse_corpus(
    corpus_root: str | Path,
    output_path: str | Path,
    *,
    compute_hashes: bool = True,
) -> Counter[str]:
    """Parse all manifest rows, preserving the prior output until completion."""

    root = Path(corpus_root)
    manifest_path = root / "manifest.jsonl"
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(f"{output.suffix}.tmp")
    failure_path = output.with_name(f"{output.stem}.failures.jsonl")
    temporary_failures = failure_path.with_suffix(f"{failure_path.suffix}.tmp")
    temporary_output.unlink(missing_ok=True)
    temporary_failures.unlink(missing_ok=True)
    temporary_output.touch()

    with manifest_path.open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    entries = validate_manifest_rows(rows)

    inventory = InventoryBuilder(root, compute_hashes=compute_hashes)
    print("Indexing source files once...")
    source_index = inventory.build_source_index()
    indexed_files = sum(len(paths) for paths in source_index.values())
    print(f"Indexed {indexed_files} source files for {len(source_index)} receipts.")

    parser = DocumentParser()
    stats: Counter[str] = Counter()
    total = len(entries)
    for number, entry in enumerate(entries, 1):
        try:
            _, sources = inventory.build(entry)
            package = parser.parse(sources, manifest=entry)
            append_canonical(temporary_output, package)
            stats["packages"] += 1
            stats[f"packages:{entry.doc_group.value}"] += 1
            package_failed = False
            for document in package.documents:
                status = document.parse_summary.status
                stats[f"documents:{status.value}"] += 1
                if status in {ParseStatus.FAILED, ParseStatus.UNSUPPORTED}:
                    package_failed = True
            if package_failed:
                stats["packages_with_failed_documents"] += 1
        # A single corrupt inventory row must be recorded without losing the batch.
        except Exception as exc:  # noqa: BLE001
            stats["inventory_failures"] += 1
            failure = {
                "doc_id": entry.doc_id,
                "doc_group": entry.doc_group.value,
                "file_path": entry.file_path,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            with temporary_failures.open("ab") as stream:
                stream.write(orjson.dumps(failure, option=orjson.OPT_SORT_KEYS))
                stream.write(b"\n")

        if number % 100 == 0 or number == total:
            print(
                f"[{number}/{total}] packages={stats['packages']} "
                f"inventory_failures={stats['inventory_failures']}"
            )

    temporary_output.replace(output)
    if temporary_failures.exists():
        temporary_failures.replace(failure_path)
    else:
        failure_path.unlink(missing_ok=True)
    return stats


def main() -> None:
    """CLI entry point for canonical batch parsing."""

    import argparse

    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("corpus_root", type=Path)
    argument_parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/canonical.jsonl"),
    )
    argument_parser.add_argument(
        "--skip-hashes",
        action="store_true",
        help="Skip source SHA-256 calculation for a faster local smoke test.",
    )
    arguments = argument_parser.parse_args()
    stats = parse_corpus(
        arguments.corpus_root,
        arguments.output,
        compute_hashes=not arguments.skip_hashes,
    )
    print("\n=== canonical parsing ===")
    for key in sorted(stats):
        print(f"{key:36} {stats[key]}")


if __name__ == "__main__":
    main()
