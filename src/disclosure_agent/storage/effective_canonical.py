"""Validated streaming view over an immutable canonical base plus overlay."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import orjson

from disclosure_agent.domain.models import FilingPackage, ParseStatus


@dataclass(frozen=True, slots=True)
class EffectiveCanonicalExpectations:
    """Optional corpus-level invariants checked after one complete streaming pass."""

    base_packages: int | None = None
    overlay_packages: int | None = None
    effective_packages: int | None = None
    effective_documents: int | None = None
    effective_success: int | None = None
    effective_partial: int | None = None
    effective_failed: int | None = None
    effective_tables: int | None = None
    replaced_packages: int | None = None


@dataclass(frozen=True, slots=True)
class EffectiveCanonicalManifest:
    """Deterministic description of one effective canonical input view."""

    manifest_version: str
    merge_key: str
    base_file: str
    overlay_file: str
    base_sha256: str
    overlay_sha256: str
    base_packages: int
    overlay_packages: int
    overlay_ids_in_base: int
    effective_packages: int
    effective_documents: int
    effective_success: int
    effective_partial: int
    effective_failed: int
    effective_tables: int
    replaced_packages: int
    replacement_identity_checks: int
    duplicate_filing_ids: int

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-compatible representation."""

        return asdict(self)

    def to_json_bytes(self) -> bytes:
        """Serialize deterministically so equal inputs produce equal manifests."""

        return (
            orjson.dumps(
                self.to_dict(),
                option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
            )
            + b"\n"
        )

    @property
    def sha256(self) -> str:
        """Hash the deterministic manifest bytes."""

        return hashlib.sha256(self.to_json_bytes()).hexdigest()


class EffectiveCanonicalReader:
    """Stream a base JSONL once while replacing validated overlay packages.

    The overlay is small enough to hold in memory. The large base file is opened
    exactly once: each raw line is hashed, validated as ``FilingPackage``, checked
    for duplicate IDs, and either yielded or replaced. Replacement package identity
    is stricter than the merge key so normal companion documents cannot disappear
    silently during package-level overlay application.
    """

    def __init__(
        self,
        base_path: str | Path,
        overlay_path: str | Path,
        *,
        expectations: EffectiveCanonicalExpectations | None = None,
    ) -> None:
        self.base_path = Path(base_path)
        self.overlay_path = Path(overlay_path)
        self.expectations = expectations or EffectiveCanonicalExpectations()
        if not self.base_path.is_file():
            raise FileNotFoundError(self.base_path)
        if not self.overlay_path.is_file():
            raise FileNotFoundError(self.overlay_path)

        self._overlay, self._overlay_sha256 = self._load_overlay()
        self._consumed = False
        self._manifest: EffectiveCanonicalManifest | None = None

    @property
    def manifest(self) -> EffectiveCanonicalManifest:
        """Return the manifest after the reader has been fully consumed."""

        if self._manifest is None:
            raise RuntimeError("EffectiveCanonicalReader must be fully consumed first")
        return self._manifest

    def __iter__(self) -> Iterator[FilingPackage]:
        if self._consumed:
            raise RuntimeError("EffectiveCanonicalReader is single-pass")
        self._consumed = True
        return self._iter_effective()

    def _load_overlay(self) -> tuple[dict[str, FilingPackage], str]:
        overlay: dict[str, FilingPackage] = {}
        digest = hashlib.sha256()
        with self.overlay_path.open("rb") as stream:
            for line_number, line in enumerate(stream, 1):
                digest.update(line)
                if not line.strip():
                    continue
                package = _validate_line(line, self.overlay_path, line_number)
                if package.filing_id in overlay:
                    raise ValueError(f"Duplicate overlay filing_id: {package.filing_id}")
                overlay[package.filing_id] = package
        if not overlay:
            raise ValueError("Overlay JSONL is empty")
        return overlay, digest.hexdigest()

    def _iter_effective(self) -> Iterator[FilingPackage]:
        base_digest = hashlib.sha256()
        seen_base: set[str] = set()
        seen_overlay: set[str] = set()
        counts: Counter[str] = Counter()

        with self.base_path.open("rb") as stream:
            for line_number, line in enumerate(stream, 1):
                base_digest.update(line)
                if not line.strip():
                    continue
                base = _validate_line(line, self.base_path, line_number)
                counts["base_packages"] += 1
                if base.filing_id in seen_base:
                    raise ValueError(f"Duplicate base filing_id: {base.filing_id}")
                seen_base.add(base.filing_id)

                replacement = self._overlay.get(base.filing_id)
                if replacement is None:
                    effective = base
                else:
                    _validate_replacement_identity(base, replacement)
                    seen_overlay.add(base.filing_id)
                    counts["replaced_packages"] += 1
                    counts["replacement_identity_checks"] += 1
                    effective = replacement

                counts["effective_packages"] += 1
                for document in effective.documents:
                    counts["effective_documents"] += 1
                    status = document.parse_summary.status
                    counts[f"status:{status.value}"] += 1
                    counts["effective_tables"] += document.parse_summary.emitted_table_count
                yield effective

        unknown = sorted(set(self._overlay) - seen_overlay)
        if unknown:
            raise ValueError(f"Overlay filing IDs are absent from base snapshot: {unknown[:10]}")

        manifest = EffectiveCanonicalManifest(
            manifest_version="1.0.0",
            merge_key="filing_id",
            base_file=self.base_path.name,
            overlay_file=self.overlay_path.name,
            base_sha256=base_digest.hexdigest(),
            overlay_sha256=self._overlay_sha256,
            base_packages=counts["base_packages"],
            overlay_packages=len(self._overlay),
            overlay_ids_in_base=len(seen_overlay),
            effective_packages=counts["effective_packages"],
            effective_documents=counts["effective_documents"],
            effective_success=counts[f"status:{ParseStatus.SUCCESS.value}"],
            effective_partial=counts[f"status:{ParseStatus.PARTIAL.value}"],
            effective_failed=(
                counts[f"status:{ParseStatus.FAILED.value}"]
                + counts[f"status:{ParseStatus.UNSUPPORTED.value}"]
                + counts[f"status:{ParseStatus.PENDING.value}"]
            ),
            effective_tables=counts["effective_tables"],
            replaced_packages=counts["replaced_packages"],
            replacement_identity_checks=counts["replacement_identity_checks"],
            duplicate_filing_ids=0,
        )
        _assert_expectations(manifest, self.expectations)
        self._manifest = manifest


def _validate_line(line: bytes, path: Path, line_number: int) -> FilingPackage:
    try:
        return FilingPackage.model_validate(orjson.loads(line))
    except Exception as exc:
        raise ValueError(f"Invalid canonical JSONL record at {path}:{line_number}") from exc


def _document_signature(package: FilingPackage) -> set[tuple[str, str, str, tuple[str, ...]]]:
    return {
        (
            document.document_id,
            document.document_role.value,
            document.primary_source_file_id,
            tuple(sorted(document.source_file_ids)),
        )
        for document in package.documents
    }


def _validate_replacement_identity(base: FilingPackage, overlay: FilingPackage) -> None:
    if overlay.filing.receipt_number != base.filing.receipt_number:
        raise ValueError(
            f"Overlay receipt mismatch for {base.filing_id}: "
            f"base={base.filing.receipt_number}, overlay={overlay.filing.receipt_number}"
        )
    if overlay.company.corp_code != base.company.corp_code:
        raise ValueError(
            f"Overlay company mismatch for {base.filing_id}: "
            f"base={base.company.corp_code}, overlay={overlay.company.corp_code}"
        )

    base_sources = {source.source_file_id for source in base.source_files}
    overlay_sources = {source.source_file_id for source in overlay.source_files}
    if base_sources != overlay_sources:
        raise ValueError(f"Overlay source_file_id set mismatch for {base.filing_id}")

    if _document_signature(base) != _document_signature(overlay):
        raise ValueError(f"Overlay document identity/role/source set mismatch for {base.filing_id}")


def _assert_expectations(
    manifest: EffectiveCanonicalManifest,
    expected: EffectiveCanonicalExpectations,
) -> None:
    pairs = {
        "base_packages": expected.base_packages,
        "overlay_packages": expected.overlay_packages,
        "effective_packages": expected.effective_packages,
        "effective_documents": expected.effective_documents,
        "effective_success": expected.effective_success,
        "effective_partial": expected.effective_partial,
        "effective_failed": expected.effective_failed,
        "effective_tables": expected.effective_tables,
        "replaced_packages": expected.replaced_packages,
    }
    failures = {
        key: (wanted, getattr(manifest, key))
        for key, wanted in pairs.items()
        if wanted is not None and getattr(manifest, key) != wanted
    }
    if failures:
        details = ", ".join(
            f"{key}: expected={wanted}, actual={actual}"
            for key, (wanted, actual) in failures.items()
        )
        raise ValueError(f"Effective canonical invariant mismatch: {details}")
