"""Build a reproducible Supply Contract subset from a canonical JSONL snapshot.

The builder performs a single streaming pass over the immutable canonical file:

1. hash every canonical byte while reading;
2. select Exchange filings by exact ``document_subtype``;
3. fully validate only selected packages against the canonical Pydantic model;
4. write selected records to a temporary JSONL file without re-serialising them;
5. verify the selected filing IDs exactly match the corpus manifest inventory;
6. hash the validated subset and atomically publish subset + manifest.

The source canonical file is never modified and selected package objects are not
retained in memory after their provenance counters have been collected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

from disclosure_agent.domain.models import (
    SCHEMA_VERSION,
    CorpusManifestEntry,
    DocumentGroup,
    FilingPackage,
)

SUPPLY_CONTRACT_SUBTYPE = "단일판매공급계약체결"
DEFAULT_CANONICAL = Path("data/processed/canonical-v22-smoke.jsonl")
DEFAULT_INVENTORY = Path("data/manifest.jsonl")
DEFAULT_OUTPUT = Path("data/processed/subsets/supply-contract-v22.jsonl")
DEFAULT_MANIFEST = Path("data/processed/subsets/supply-contract-v22.manifest.json")


class SubsetBuildError(RuntimeError):
    """Raised when a subset cannot be published safely."""


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_jsonl_line(line: bytes) -> bytes:
    return line if line.endswith(b"\n") else line + b"\n"


def _is_supply_contract_payload(payload: dict[str, Any]) -> bool:
    filing = payload.get("filing")
    if not isinstance(filing, dict):
        return False
    return (
        filing.get("document_group") == DocumentGroup.EXCHANGE.value
        and filing.get("document_subtype") == SUPPLY_CONTRACT_SUBTYPE
    )


def _load_inventory_ids(path: Path) -> tuple[set[str], str]:
    """Return exact expected filing IDs for Supply Contract filings + file hash."""

    if not path.is_file():
        raise SubsetBuildError(f"Inventory manifest not found: {path}")

    expected: set[str] = set()
    receipt_numbers: set[str] = set()
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for line_number, line in enumerate(stream, 1):
            digest.update(line)
            if not line.strip():
                continue
            try:
                entry = CorpusManifestEntry.model_validate(orjson.loads(line))
            except Exception as exc:
                raise SubsetBuildError(
                    f"Invalid inventory manifest record at line {line_number}"
                ) from exc

            if (
                entry.doc_group is DocumentGroup.EXCHANGE
                and entry.doc_subtype == SUPPLY_CONTRACT_SUBTYPE
            ):
                if entry.doc_id in expected:
                    raise SubsetBuildError(
                        f"Duplicate Supply Contract doc_id in inventory: {entry.doc_id}"
                    )
                if entry.rcept_no in receipt_numbers:
                    raise SubsetBuildError(
                        "Duplicate Supply Contract receipt number in inventory: "
                        f"{entry.rcept_no}"
                    )
                expected.add(entry.doc_id)
                receipt_numbers.add(entry.rcept_no)

    if not expected:
        raise SubsetBuildError("Inventory contains no Supply Contract filings")
    return expected, digest.hexdigest()


def _finalize_parser_versions(versions: dict[str, set[str]]) -> dict[str, str]:
    conflicts = {name: values for name, values in versions.items() if len(values) > 1}
    if conflicts:
        rendered = ", ".join(
            f"{name}={sorted(values)}" for name, values in sorted(conflicts.items())
        )
        raise SubsetBuildError(f"Multiple parser versions in selected subset: {rendered}")

    return {
        name: next(iter(values))
        for name, values in sorted(versions.items())
        if values
    }


def _verify_selected_ids(selected_ids: list[str], expected_ids: set[str]) -> None:
    if len(selected_ids) != len(set(selected_ids)):
        raise SubsetBuildError("Duplicate filing_id values in selected canonical packages")

    selected_id_set = set(selected_ids)
    missing = expected_ids - selected_id_set
    unexpected = selected_id_set - expected_ids
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing={len(missing)} examples={sorted(missing)[:5]}")
        if unexpected:
            details.append(f"unexpected={len(unexpected)} examples={sorted(unexpected)[:5]}")
        raise SubsetBuildError(
            "Canonical subset does not match inventory Supply Contract filing IDs: "
            + "; ".join(details)
        )


def _temporary_sibling(path: Path) -> Path:
    return path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")


def build_supply_contract_subset(
    canonical_path: str | Path = DEFAULT_CANONICAL,
    *,
    inventory_path: str | Path = DEFAULT_INVENTORY,
    output_path: str | Path = DEFAULT_OUTPUT,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    force: bool = False,
    progress_every: int = 250,
) -> dict[str, Any]:
    """Build and atomically publish the Supply Contract canonical subset.

    Selection is based only on canonical filing metadata:

    ``document_group == exchange`` and
    ``document_subtype == 단일판매공급계약체결``.

    The expected filing count is not hard-coded. Exact filing IDs are derived
    independently from ``data/manifest.jsonl`` and compared as a set.
    """

    canonical = Path(canonical_path)
    inventory = Path(inventory_path)
    output = Path(output_path)
    manifest = Path(manifest_path)

    if progress_every < 0:
        raise SubsetBuildError("progress_every must be zero or greater")
    if not canonical.is_file():
        raise SubsetBuildError(f"Canonical snapshot not found: {canonical}")
    if canonical.resolve() in {output.resolve(), manifest.resolve()}:
        raise SubsetBuildError("Output paths must not replace the source canonical snapshot")
    if output.resolve() == manifest.resolve():
        raise SubsetBuildError("Subset JSONL and manifest paths must be different")
    if not force:
        existing = [path for path in (output, manifest) if path.exists()]
        if existing:
            raise SubsetBuildError(
                "Refusing to replace existing output without --force: "
                + ", ".join(str(path) for path in existing)
            )

    expected_ids, inventory_sha256 = _load_inventory_ids(inventory)

    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temp_output = _temporary_sibling(output)
    temp_manifest = _temporary_sibling(manifest)

    canonical_digest = hashlib.sha256()
    selected_ids: list[str] = []
    schema_versions: set[str] = set()
    parser_version_sets: dict[str, set[str]] = defaultdict(set)
    scanned_packages = 0
    document_count = 0
    source_file_count = 0
    source_hash_present = 0

    try:
        with canonical.open("rb") as source, temp_output.open("wb") as target:
            for line_number, line in enumerate(source, 1):
                canonical_digest.update(line)
                if not line.strip():
                    continue
                scanned_packages += 1
                try:
                    payload = orjson.loads(line)
                except Exception as exc:
                    raise SubsetBuildError(
                        f"Invalid JSON in canonical snapshot at line {line_number}"
                    ) from exc

                if not isinstance(payload, dict) or not _is_supply_contract_payload(payload):
                    if progress_every > 0 and scanned_packages % progress_every == 0:
                        print(
                            f"scanned={scanned_packages} selected={len(selected_ids)}",
                            file=sys.stderr,
                        )
                    continue

                try:
                    package = FilingPackage.model_validate(payload)
                except Exception as exc:
                    raise SubsetBuildError(
                        f"Invalid selected canonical package at line {line_number}"
                    ) from exc

                # FilingPackage validates filing_id == filing.doc_id.
                selected_ids.append(package.filing_id)
                schema_versions.add(package.schema_version)
                document_count += len(package.documents)
                source_file_count += len(package.source_files)
                source_hash_present += sum(
                    source_file.sha256 is not None for source_file in package.source_files
                )
                for document in package.documents:
                    name = document.parse_summary.parser_name
                    version = document.parse_summary.parser_version
                    if name and version:
                        parser_version_sets[name].add(version)

                target.write(_normalise_jsonl_line(line))

                if progress_every > 0 and scanned_packages % progress_every == 0:
                    print(
                        f"scanned={scanned_packages} selected={len(selected_ids)}",
                        file=sys.stderr,
                    )

            target.flush()
            os.fsync(target.fileno())

        _verify_selected_ids(selected_ids, expected_ids)
        if schema_versions != {SCHEMA_VERSION}:
            raise SubsetBuildError(
                "Selected packages do not all use the current canonical schema: "
                f"{sorted(schema_versions)}"
            )
        parser_versions = _finalize_parser_versions(parser_version_sets)

        # Hash only after the temporary subset has passed count/schema/inventory checks.
        subset_sha256 = _sha256_file(temp_output)
        subset_size_bytes = temp_output.stat().st_size

        metadata: dict[str, Any] = {
            "subset_name": "supply-contract-v22",
            "created_at": datetime.now(UTC).isoformat(),
            "selection": {
                "document_group": DocumentGroup.EXCHANGE.value,
                "document_subtype": SUPPLY_CONTRACT_SUBTYPE,
            },
            "schema_version": SCHEMA_VERSION,
            "parser_versions": parser_versions,
            "source_canonical": {
                "path": str(canonical),
                "size_bytes": canonical.stat().st_size,
                "sha256": canonical_digest.hexdigest(),
                "scanned_package_count": scanned_packages,
            },
            "inventory_manifest": {
                "path": str(inventory),
                "sha256": inventory_sha256,
                "expected_package_count": len(expected_ids),
            },
            "selected_package_count": len(selected_ids),
            "selected_document_count": document_count,
            "selected_source_file_count": source_file_count,
            "filing_ids": sorted(selected_ids),
            "source_file_hash_coverage": {
                "present": source_hash_present,
                "total": source_file_count,
            },
            "subset": {
                "path": str(output),
                "size_bytes": subset_size_bytes,
                "sha256": subset_sha256,
            },
        }

        with temp_manifest.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(metadata, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        # Publish data first and manifest last. The manifest is the commit marker for
        # a complete subset build; neither source input is ever modified.
        os.replace(temp_output, output)
        os.replace(temp_manifest, manifest)
        return metadata
    except Exception:
        temp_output.unlink(missing_ok=True)
        temp_manifest.unlink(missing_ok=True)
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the reproducible Supply Contract subset from canonical JSONL."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_CANONICAL)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--progress-every",
        type=int,
        default=250,
        help="Write progress every N scanned packages; 0 disables progress output.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    try:
        metadata = build_supply_contract_subset(
            args.input,
            inventory_path=args.inventory,
            output_path=args.output,
            manifest_path=args.manifest,
            force=args.force,
            progress_every=args.progress_every,
        )
    except SubsetBuildError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

    print("=== supply contract subset ===")
    print(f"packages                 {metadata['selected_package_count']}")
    print(f"documents                {metadata['selected_document_count']}")
    print(
        "source hashes            "
        f"{metadata['source_file_hash_coverage']['present']}/"
        f"{metadata['source_file_hash_coverage']['total']}"
    )
    print(f"canonical sha256         {metadata['source_canonical']['sha256']}")
    print(f"subset sha256            {metadata['subset']['sha256']}")
    print(f"OUTPUT                   {args.output}")
    print(f"MANIFEST                 {args.manifest}")


if __name__ == "__main__":
    main()
