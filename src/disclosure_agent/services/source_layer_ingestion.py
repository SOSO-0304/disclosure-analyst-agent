"""Application service for staging and promoting the full effective canonical source layer."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from disclosure_agent.storage.effective_canonical import (
    EffectiveCanonicalExpectations,
    EffectiveCanonicalManifest,
    EffectiveCanonicalReader,
)
from disclosure_agent.storage.source_layer_persistence import project_source_package
from disclosure_agent.storage.source_layer_repository import SourceLayerRepository

ProgressCallback = Callable[[int, dict[str, int]], None]


@dataclass(frozen=True, slots=True)
class SourceLayerIngestionResult:
    """Stable result returned after staging validation and atomic promotion."""

    load_run_id: str
    counts: dict[str, int]
    manifest: EffectiveCanonicalManifest


def ingest_effective_source_layer(
    *,
    base_path: str | Path,
    overlay_path: str | Path | None,
    validated_manifest: EffectiveCanonicalManifest,
    session: Session,
    expectations: EffectiveCanonicalExpectations,
    expected_company_count: int | None = None,
    progress: ProgressCallback | None = None,
) -> SourceLayerIngestionResult:
    """Stage one prevalidated effective view, verify it again, then promote by canonical ID."""

    load_run_id = validated_manifest.sha256[:32]
    started_at = datetime.now(UTC)
    reader = EffectiveCanonicalReader(base_path, overlay_path, expectations=expectations)
    repository = SourceLayerRepository(session)
    repository.prepare_staging()

    counts: Counter[str] = Counter()
    seen_companies: set[str] = set()
    for number, package in enumerate(reader, 1):
        rows = project_source_package(package, load_run_id=load_run_id)
        if package.company.corp_code not in seen_companies:
            repository.stage_rows("source_companies", [rows.company])
            seen_companies.add(package.company.corp_code)
            counts["companies"] += 1

        repository.stage_rows("source_filings", [rows.filing])
        repository.stage_rows("source_documents", rows.documents)
        repository.stage_rows("source_sections", rows.sections)
        repository.stage_rows("source_blocks", rows.blocks)
        repository.stage_rows("source_tables", rows.tables)
        counts["filings"] += 1
        counts["documents"] += len(rows.documents)
        counts["sections"] += len(rows.sections)
        counts["blocks"] += len(rows.blocks)
        counts["tables"] += len(rows.tables)

        if progress is not None:
            progress(number, dict(counts))

    manifest = reader.manifest
    if manifest.to_json_bytes() != validated_manifest.to_json_bytes():
        raise ValueError(
            "Effective canonical inputs no longer match the prevalidated manifest; "
            "rerun validate_effective_canonical.py before loading."
        )
    if expected_company_count is not None and counts["companies"] != expected_company_count:
        raise ValueError(
            "Source company count mismatch: "
            f"expected={expected_company_count}, actual={counts['companies']}"
        )

    validated_counts = repository.validate_staging(
        expected_counts=counts,
        manifest=manifest,
    )
    repository.promote(
        load_run_id=load_run_id,
        manifest=manifest,
        counts=validated_counts,
        started_at=started_at,
    )
    return SourceLayerIngestionResult(
        load_run_id=load_run_id,
        counts=validated_counts,
        manifest=manifest,
    )
