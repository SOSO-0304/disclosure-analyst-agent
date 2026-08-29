"""Build the Supply Contract lifecycle subset used for contract state resolution.

The lifecycle slice is intentionally broader than the formation extractor slice:

* every ``단일판매공급계약체결`` filing;
* every ``단일판매공급계약해지`` filing;
* the one supplied ``투자판단관련주요경영사항`` filing whose report name
  contains ``단일판매 공급계약 잔여금액 승계``.

Selection is validated against ``data/manifest.jsonl`` so the expected inventory
comes from the supplied corpus rather than hard-coded package IDs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from collections import Counter, defaultdict
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
from disclosure_agent.subsets.supply_contract import SubsetBuildError

FORMATION_SUBTYPE = "단일판매공급계약체결"
TERMINATION_SUBTYPE = "단일판매공급계약해지"
SUCCESSION_SUBTYPE = "투자판단관련주요경영사항"
SUCCESSION_REPORT_TOKEN = "단일판매 공급계약 잔여금액 승계"

DEFAULT_CANONICAL = Path("data/processed/canonical-v22-smoke.jsonl")
DEFAULT_INVENTORY = Path("data/manifest.jsonl")
DEFAULT_OUTPUT = Path("data/processed/subsets/supply-contract-lifecycle-v22.jsonl")
DEFAULT_MANIFEST = Path(
    "data/processed/subsets/supply-contract-lifecycle-v22.manifest.json"
)


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _temporary_sibling(path: Path) -> Path:
    return path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")


def _normalise_jsonl_line(line: bytes) -> bytes:
    return line if line.endswith(b"\n") else line + b"\n"


def _kind_from_values(subtype: str | None, report_name: str) -> str | None:
    if subtype == FORMATION_SUBTYPE:
        return "formation"
    if subtype == TERMINATION_SUBTYPE:
        return "termination"
    if subtype == SUCCESSION_SUBTYPE and SUCCESSION_REPORT_TOKEN in report_name:
        return "succession"
    return None


def _payload_kind(payload: dict[str, Any]) -> str | None:
    filing = payload.get("filing")
    if not isinstance(filing, dict):
        return None
    if filing.get("document_group") != DocumentGroup.EXCHANGE.value:
        return None
    subtype = filing.get("document_subtype")
    report_name = filing.get("report_name_raw")
    if not isinstance(report_name, str):
        return None
    return _kind_from_values(subtype, report_name)


def _load_inventory(path: Path) -> tuple[dict[str, str], str]:
    if not path.is_file():
        raise SubsetBuildError(f"Inventory manifest not found: {path}")

    expected: dict[str, str] = {}
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

            if entry.doc_group is not DocumentGroup.EXCHANGE:
                continue
            kind = _kind_from_values(entry.doc_subtype, entry.report_nm)
            if kind is None:
                continue
            if entry.doc_id in expected:
                raise SubsetBuildError(f"Duplicate lifecycle doc_id: {entry.doc_id}")
            if entry.rcept_no in receipt_numbers:
                raise SubsetBuildError(
                    f"Duplicate lifecycle receipt number: {entry.rcept_no}"
                )
            expected[entry.doc_id] = kind
            receipt_numbers.add(entry.rcept_no)

    if not expected:
        raise SubsetBuildError("Inventory contains no Supply Contract lifecycle filings")
    return expected, digest.hexdigest()


def _verify_selected(
    selected: dict[str, str],
    expected: dict[str, str],
) -> None:
    missing = set(expected) - set(selected)
    unexpected = set(selected) - set(expected)
    mismatched = sorted(
        filing_id
        for filing_id in set(selected) & set(expected)
        if selected[filing_id] != expected[filing_id]
    )
    if missing or unexpected or mismatched:
        parts: list[str] = []
        if missing:
            parts.append(f"missing={len(missing)} examples={sorted(missing)[:5]}")
        if unexpected:
            parts.append(
                f"unexpected={len(unexpected)} examples={sorted(unexpected)[:5]}"
            )
        if mismatched:
            parts.append(f"kind_mismatch={len(mismatched)} examples={mismatched[:5]}")
        raise SubsetBuildError(
            "Lifecycle canonical selection does not match inventory: " + "; ".join(parts)
        )


def _finalize_parser_versions(versions: dict[str, set[str]]) -> dict[str, str]:
    conflicts = {name: values for name, values in versions.items() if len(values) > 1}
    if conflicts:
        rendered = ", ".join(
            f"{name}={sorted(values)}" for name, values in sorted(conflicts.items())
        )
        raise SubsetBuildError(f"Multiple parser versions in lifecycle subset: {rendered}")
    return {
        name: next(iter(values))
        for name, values in sorted(versions.items())
        if values
    }


def build_supply_contract_lifecycle_subset(
    canonical_path: str | Path = DEFAULT_CANONICAL,
    *,
    inventory_path: str | Path = DEFAULT_INVENTORY,
    output_path: str | Path = DEFAULT_OUTPUT,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    force: bool = False,
    progress_every: int = 250,
) -> dict[str, Any]:
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

    expected, inventory_sha256 = _load_inventory(inventory)
    expected_counts = Counter(expected.values())

    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temp_output = _temporary_sibling(output)
    temp_manifest = _temporary_sibling(manifest)

    canonical_digest = hashlib.sha256()
    selected: dict[str, str] = {}
    selected_counts: Counter[str] = Counter()
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

                if not isinstance(payload, dict):
                    continue
                kind = _payload_kind(payload)
                if kind is None:
                    if progress_every and scanned_packages % progress_every == 0:
                        print(
                            f"scanned={scanned_packages} selected={len(selected)}",
                            file=sys.stderr,
                        )
                    continue

                try:
                    package = FilingPackage.model_validate(payload)
                except Exception as exc:
                    raise SubsetBuildError(
                        f"Invalid selected lifecycle package at line {line_number}"
                    ) from exc

                if package.filing_id in selected:
                    raise SubsetBuildError(
                        f"Duplicate lifecycle filing_id in canonical: {package.filing_id}"
                    )
                selected[package.filing_id] = kind
                selected_counts[kind] += 1
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

                if progress_every and scanned_packages % progress_every == 0:
                    print(
                        f"scanned={scanned_packages} selected={len(selected)}",
                        file=sys.stderr,
                    )

            target.flush()
            os.fsync(target.fileno())

        _verify_selected(selected, expected)
        if schema_versions != {SCHEMA_VERSION}:
            raise SubsetBuildError(
                "Lifecycle packages do not all use the current canonical schema: "
                f"{sorted(schema_versions)}"
            )
        parser_versions = _finalize_parser_versions(parser_version_sets)
        subset_sha256 = _sha256_file(temp_output)

        metadata: dict[str, Any] = {
            "subset_name": "supply-contract-lifecycle-v22",
            "created_at": datetime.now(UTC).isoformat(),
            "selection": {
                "document_group": DocumentGroup.EXCHANGE.value,
                "formation_subtype": FORMATION_SUBTYPE,
                "termination_subtype": TERMINATION_SUBTYPE,
                "succession_subtype": SUCCESSION_SUBTYPE,
                "succession_report_token": SUCCESSION_REPORT_TOKEN,
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
                "expected_package_count": len(expected),
                "expected_by_kind": dict(sorted(expected_counts.items())),
            },
            "selected_package_count": len(selected),
            "selected_by_kind": dict(sorted(selected_counts.items())),
            "selected_document_count": document_count,
            "selected_source_file_count": source_file_count,
            "filing_ids": sorted(selected),
            "source_file_hash_coverage": {
                "present": source_hash_present,
                "total": source_file_count,
            },
            "subset": {
                "path": str(output),
                "size_bytes": temp_output.stat().st_size,
                "sha256": subset_sha256,
            },
        }

        with temp_manifest.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(metadata, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temp_output, output)
        os.replace(temp_manifest, manifest)
        return metadata
    except Exception:
        temp_output.unlink(missing_ok=True)
        temp_manifest.unlink(missing_ok=True)
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the Supply Contract lifecycle subset from canonical JSONL."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_CANONICAL)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--progress-every", type=int, default=250)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    try:
        metadata = build_supply_contract_lifecycle_subset(
            args.input,
            inventory_path=args.inventory,
            output_path=args.output,
            manifest_path=args.manifest,
            force=args.force,
            progress_every=args.progress_every,
        )
    except SubsetBuildError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

    print("=== supply contract lifecycle subset ===")
    print(f"packages                 {metadata['selected_package_count']}")
    for kind, count in metadata["selected_by_kind"].items():
        print(f"{kind:<24} {count}")
    print(f"canonical sha256         {metadata['source_canonical']['sha256']}")
    print(f"subset sha256            {metadata['subset']['sha256']}")
    print(f"OUTPUT                   {args.output}")
    print(f"MANIFEST                 {args.manifest}")


if __name__ == "__main__":
    main()
