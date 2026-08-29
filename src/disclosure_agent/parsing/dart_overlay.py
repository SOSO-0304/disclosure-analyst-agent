"""Selective reparsing of structurally recovered DART packages into an overlay."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from disclosure_agent.domain.models import FilingPackage, ParseStatus
from disclosure_agent.inventory.builder import InventoryBuilder, validate_manifest_rows
from disclosure_agent.parsing.document_parser import DocumentParser
from disclosure_agent.storage.jsonl import append_canonical, read_canonical

DART_OVERLAY_PARSER_VERSION = "2.2.1"


def is_dart_overlay_candidate(package: FilingPackage) -> bool:
    """Return whether v2.2 contains a partial DartParser document needing reparse."""

    for document in package.documents:
        if document.parse_summary.parser_name != "DartParser":
            continue
        if document.parse_summary.status is not ParseStatus.PARTIAL:
            continue
        if any(issue.issue_code == "markup_recovery" for issue in document.parse_issues):
            return True
    return False


def collect_dart_overlay_candidate_ids(base_path: str | Path) -> tuple[str, ...]:
    """Scan the immutable base snapshot once and return candidate filing IDs in base order."""

    return tuple(
        package.filing_id
        for package in read_canonical(base_path)
        if is_dart_overlay_candidate(package)
    )


def reparse_dart_overlay(
    *,
    corpus_root: str | Path,
    base_path: str | Path,
    output_path: str | Path,
    compute_hashes: bool = False,
    expected_packages: int | None = None,
) -> Counter[str]:
    """Reparse only v2.2 DART recovery candidates and atomically write an overlay JSONL."""

    root = Path(corpus_root)
    base = Path(base_path)
    output = Path(output_path)
    if not base.is_file():
        raise FileNotFoundError(base)
    manifest_path = root / "manifest.jsonl"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)

    candidate_ids = collect_dart_overlay_candidate_ids(base)
    if expected_packages is not None and len(candidate_ids) != expected_packages:
        raise ValueError(
            f"DART overlay candidate count mismatch: expected={expected_packages}, "
            f"actual={len(candidate_ids)}"
        )
    if not candidate_ids:
        raise ValueError("No DART overlay candidates were found in the base snapshot")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("Base snapshot contains duplicate DART overlay candidate filing IDs")

    with manifest_path.open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    entries = validate_manifest_rows(rows)
    by_id = {entry.doc_id: entry for entry in entries}
    missing = [filing_id for filing_id in candidate_ids if filing_id not in by_id]
    if missing:
        raise ValueError(f"Overlay candidates missing from manifest: {missing[:10]}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.unlink(missing_ok=True)
    temporary.touch()

    inventory = InventoryBuilder(root, compute_hashes=compute_hashes)
    parser = DocumentParser()
    stats: Counter[str] = Counter()
    stats["candidates"] = len(candidate_ids)

    try:
        for number, filing_id in enumerate(candidate_ids, 1):
            entry = by_id[filing_id]
            _, sources = inventory.build(entry)
            package = parser.parse(sources, manifest=entry)

            dart_documents = [
                document
                for document in package.documents
                if document.parse_summary.parser_name == "DartParser"
            ]
            if not dart_documents:
                raise ValueError(f"Candidate {filing_id} reparsed without a DartParser document")
            wrong_versions = [
                document.document_id
                for document in dart_documents
                if document.parse_summary.parser_version != DART_OVERLAY_PARSER_VERSION
            ]
            if wrong_versions:
                raise ValueError(
                    f"Candidate {filing_id} has unexpected DartParser versions: {wrong_versions}"
                )

            append_canonical(temporary, package)
            stats["packages"] += 1
            stats[f"packages:{entry.doc_group.value}"] += 1
            for document in package.documents:
                stats[f"documents:{document.parse_summary.status.value}"] += 1
                if document.parse_summary.parser_name == "DartParser":
                    stats["dart_documents"] += 1
                    if document.parse_summary.status is ParseStatus.PARTIAL:
                        stats["dart_documents:partial"] += 1
                    elif document.parse_summary.status is ParseStatus.SUCCESS:
                        stats["dart_documents:success"] += 1

            if number % 10 == 0 or number == len(candidate_ids):
                print(
                    f"[overlay {number}/{len(candidate_ids)}] "
                    f"success={stats['dart_documents:success']} "
                    f"partial={stats['dart_documents:partial']}"
                )
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    temporary.replace(output)
    return stats
