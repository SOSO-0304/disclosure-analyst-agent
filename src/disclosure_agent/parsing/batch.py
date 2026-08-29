"""Validated, profiled, and atomic batch conversion to canonical JSONL."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any, BinaryIO

import orjson

from disclosure_agent.domain.models import FilingPackage, ParseStatus
from disclosure_agent.inventory.builder import InventoryBuilder, validate_manifest_rows
from disclosure_agent.parsing.document_parser import DocumentParser
from disclosure_agent.storage.jsonl import (
    COMPACT_CANONICAL_PROFILE,
    LEGACY_CANONICAL_PROFILE,
    CanonicalJsonlWriter,
)


def _milliseconds(start: float, end: float) -> float:
    return round((end - start) * 1_000, 3)


def _content_counts(package: FilingPackage) -> dict[str, int]:
    sections = blocks = tables = cells = 0
    for document in package.documents:
        sections += len(document.sections)
        blocks += len(document.blocks)
        for block in document.blocks:
            if block.table is None:
                continue
            tables += 1
            cells += len(block.table.cells)
    return {
        "documents": len(package.documents),
        "sections": sections,
        "blocks": blocks,
        "tables": tables,
        "cells": cells,
    }


def _write_profile_record(stream: BinaryIO, record: dict[str, Any]) -> None:
    stream.write(orjson.dumps(record, option=orjson.OPT_SORT_KEYS))
    stream.write(b"\n")


def parse_corpus(
    corpus_root: str | Path,
    output_path: str | Path,
    *,
    compute_hashes: bool = True,
    compact_output: bool = False,
    profile_output_path: str | Path | None = None,
    max_packages: int | None = None,
) -> Counter[str]:
    """Parse manifest rows while preserving the prior output until completion.

    ``compact_output`` changes only the JSON representation: omitted ``None``
    values are restored by Pydantic when the file is read. ``profile_output_path``
    records package-level stage timings and content counts for bottleneck analysis.
    """

    if max_packages is not None and max_packages < 1:
        raise ValueError("max_packages must be at least 1")

    root = Path(corpus_root)
    manifest_path = root / "manifest.jsonl"
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(f"{output.suffix}.tmp")
    failure_path = output.with_name(f"{output.stem}.failures.jsonl")
    temporary_failures = failure_path.with_suffix(f"{failure_path.suffix}.tmp")
    profile_path = Path(profile_output_path) if profile_output_path is not None else None
    temporary_profile = (
        profile_path.with_suffix(f"{profile_path.suffix}.tmp") if profile_path is not None else None
    )
    temporary_output.unlink(missing_ok=True)
    temporary_failures.unlink(missing_ok=True)
    if temporary_profile is not None:
        temporary_profile.parent.mkdir(parents=True, exist_ok=True)
        temporary_profile.unlink(missing_ok=True)

    with manifest_path.open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    entries = validate_manifest_rows(rows)
    if max_packages is not None:
        entries = entries[:max_packages]

    inventory = InventoryBuilder(root, compute_hashes=compute_hashes)
    print("Indexing source files once...")
    source_index = inventory.build_source_index()
    indexed_files = sum(len(paths) for paths in source_index.values())
    print(f"Indexed {indexed_files} source files for {len(source_index)} receipts.")

    parser = DocumentParser()
    stats: Counter[str] = Counter()
    total = len(entries)
    storage_profile = COMPACT_CANONICAL_PROFILE if compact_output else LEGACY_CANONICAL_PROFILE
    profile_stream = temporary_profile.open("wb") if temporary_profile is not None else None

    try:
        with CanonicalJsonlWriter(temporary_output, profile=storage_profile) as writer:
            for number, entry in enumerate(entries, 1):
                package_started = perf_counter()
                inventory_started = package_started
                package: FilingPackage | None = None
                try:
                    _, sources = inventory.build(entry)
                    inventory_finished = perf_counter()

                    parse_started = inventory_finished
                    package = parser.parse(sources, manifest=entry)
                    parse_finished = perf_counter()
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

                if package is not None:
                    # Serialization and I/O failures must abort the batch. Continuing after a
                    # partial write could produce a corrupted JSONL snapshot.
                    serialization_started = parse_finished
                    payload = writer.serialize(package)
                    serialization_finished = perf_counter()

                    write_started = serialization_finished
                    output_bytes = writer.write_serialized(payload)
                    write_finished = perf_counter()

                    stats["packages"] += 1
                    stats[f"packages:{entry.doc_group.value}"] += 1
                    stats["output_bytes"] += output_bytes
                    package_failed = False
                    for document in package.documents:
                        status = document.parse_summary.status
                        stats[f"documents:{status.value}"] += 1
                        if status in {ParseStatus.FAILED, ParseStatus.UNSUPPORTED}:
                            package_failed = True
                    if package_failed:
                        stats["packages_with_failed_documents"] += 1

                    if profile_stream is not None:
                        _write_profile_record(
                            profile_stream,
                            {
                                "filing_id": package.filing_id,
                                "document_group": entry.doc_group.value,
                                "source_bytes": sum(
                                    resolved.source.size_bytes or 0 for resolved in sources
                                ),
                                "output_bytes": output_bytes,
                                "inventory_ms": _milliseconds(
                                    inventory_started, inventory_finished
                                ),
                                "parse_ms": _milliseconds(parse_started, parse_finished),
                                "serialize_ms": _milliseconds(
                                    serialization_started, serialization_finished
                                ),
                                "buffered_write_ms": _milliseconds(
                                    write_started, write_finished
                                ),
                                "total_ms": _milliseconds(package_started, write_finished),
                                **_content_counts(package),
                            },
                        )

                if number % 100 == 0 or number == total:
                    print(
                        f"[{number}/{total}] packages={stats['packages']} "
                        f"inventory_failures={stats['inventory_failures']}"
                    )
    finally:
        if profile_stream is not None:
            profile_stream.close()

    temporary_output.replace(output)
    if temporary_failures.exists():
        temporary_failures.replace(failure_path)
    else:
        failure_path.unlink(missing_ok=True)
    if temporary_profile is not None and profile_path is not None:
        temporary_profile.replace(profile_path)
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
    argument_parser.add_argument(
        "--compact-output",
        action="store_true",
        help="Omit nulls and bypass recursive JSON key sorting without changing models.",
    )
    argument_parser.add_argument(
        "--profile-output",
        type=Path,
        help="Write one JSONL timing/count record per successfully parsed package.",
    )
    argument_parser.add_argument(
        "--max-packages",
        type=int,
        help="Parse only the first N manifest rows for benchmarking.",
    )
    arguments = argument_parser.parse_args()
    stats = parse_corpus(
        arguments.corpus_root,
        arguments.output,
        compute_hashes=not arguments.skip_hashes,
        compact_output=arguments.compact_output,
        profile_output_path=arguments.profile_output,
        max_packages=arguments.max_packages,
    )
    print("\n=== canonical parsing ===")
    for key in sorted(stats):
        print(f"{key:36} {stats[key]}")


if __name__ == "__main__":
    main()
