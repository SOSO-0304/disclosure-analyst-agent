from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from disclosure_agent.domain.models import (
    CanonicalDocument,
    CompanyIdentity,
    DocumentGroup,
    FilingMetadata,
    FilingPackage,
    ParseStatus,
    ParseSummary,
    SourceFile,
    SourceRole,
)
from disclosure_agent.storage.effective_canonical import (
    EffectiveCanonicalExpectations,
    EffectiveCanonicalReader,
)
from disclosure_agent.storage.jsonl import COMPACT_CANONICAL_PROFILE, write_canonical


def _package(
    filing_id: str,
    receipt: str,
    *,
    status: ParseStatus = ParseStatus.SUCCESS,
    tables: int = 0,
    version: str = "2.2.0",
    source_suffix: str = "",
) -> FilingPackage:
    source_id = f"{filing_id}:source:1{source_suffix}"
    return FilingPackage(
        filing_id=filing_id,
        company=CompanyIdentity(
            corp_code="00000001",
            stock_code="000001",
            corp_name="테스트",
            listed_name="테스트",
            industry="테스트",
            sector="테스트",
        ),
        filing=FilingMetadata(
            doc_id=filing_id,
            document_group=DocumentGroup.MAJOR,
            document_subtype="테스트",
            report_name_raw="테스트",
            receipt_number=receipt,
            receipt_date=date(2024, 1, 1),
            filer_name="테스트",
        ),
        source_files=[
            SourceFile(
                source_file_id=source_id,
                archive_path_raw=f"raw/{receipt}.xml",
                archive_path_normalized=f"raw/{receipt}.xml",
                file_name=f"{receipt}.xml",
                source_role=SourceRole.PRIMARY_REPORT,
                declared_extension="xml",
                is_primary=True,
            )
        ],
        documents=[
            CanonicalDocument(
                document_id=f"{filing_id}:primary_report",
                filing_id=filing_id,
                document_role=SourceRole.PRIMARY_REPORT,
                primary_source_file_id=source_id,
                source_file_ids=[source_id],
                parse_summary=ParseSummary(
                    status=status,
                    parser_name="DartParser",
                    parser_version=version,
                    emitted_table_count=tables,
                ),
            )
        ],
    )


def test_reader_replaces_in_base_order_and_builds_deterministic_manifest(
    tmp_path: Path,
) -> None:
    base = tmp_path / "base.jsonl"
    overlay = tmp_path / "overlay.jsonl"
    base_a = _package("a", "20240101000001", status=ParseStatus.PARTIAL, tables=2)
    base_b = _package("b", "20240101000002", tables=3)
    replacement = _package("a", "20240101000001", tables=7, version="2.2.1")
    write_canonical(base, [base_a, base_b])
    write_canonical(overlay, [replacement])

    expected = EffectiveCanonicalExpectations(
        base_packages=2,
        overlay_packages=1,
        effective_packages=2,
        effective_documents=2,
        effective_success=2,
        effective_partial=0,
        effective_failed=0,
        effective_tables=10,
        replaced_packages=1,
    )
    first = EffectiveCanonicalReader(base, overlay, expectations=expected)
    packages = list(first)

    assert [package.filing_id for package in packages] == ["a", "b"]
    assert packages[0].documents[0].parse_summary.parser_version == "2.2.1"
    assert first.manifest.overlay_ids_in_base == 1
    assert first.manifest.replacement_identity_checks == 1

    second = EffectiveCanonicalReader(base, overlay, expectations=expected)
    list(second)
    assert second.manifest.to_json_bytes() == first.manifest.to_json_bytes()
    assert second.manifest.sha256 == first.manifest.sha256


def test_reader_accepts_compressed_base_and_overlay_with_same_logical_hashes(
    tmp_path: Path,
) -> None:
    base_plain = tmp_path / "base.jsonl"
    overlay_plain = tmp_path / "overlay.jsonl"
    base_gzip = tmp_path / "base.jsonl.gz"
    overlay_gzip = tmp_path / "overlay.jsonl.gz"
    base_packages = [
        _package("a", "20240101000001", status=ParseStatus.PARTIAL, tables=2),
        _package("b", "20240101000002", tables=3),
    ]
    overlay_packages = [_package("a", "20240101000001", tables=7, version="2.2.1")]

    write_canonical(base_plain, base_packages, profile=COMPACT_CANONICAL_PROFILE)
    write_canonical(overlay_plain, overlay_packages, profile=COMPACT_CANONICAL_PROFILE)
    write_canonical(
        base_gzip,
        base_packages,
        profile=COMPACT_CANONICAL_PROFILE,
        compression="gzip",
    )
    write_canonical(
        overlay_gzip,
        overlay_packages,
        profile=COMPACT_CANONICAL_PROFILE,
        compression="gzip",
    )

    plain = EffectiveCanonicalReader(base_plain, overlay_plain)
    compressed = EffectiveCanonicalReader(base_gzip, overlay_gzip)

    assert list(compressed) == list(plain)
    assert compressed.manifest.base_sha256 == plain.manifest.base_sha256
    assert compressed.manifest.overlay_sha256 == plain.manifest.overlay_sha256


def test_reader_rejects_overlay_only_filing(tmp_path: Path) -> None:
    base = tmp_path / "base.jsonl"
    overlay = tmp_path / "overlay.jsonl"
    write_canonical(base, [_package("a", "20240101000001")])
    write_canonical(overlay, [_package("b", "20240101000002")])

    with pytest.raises(ValueError, match="absent from base"):
        list(EffectiveCanonicalReader(base, overlay))


def test_reader_rejects_duplicate_base_id(tmp_path: Path) -> None:
    base = tmp_path / "base.jsonl"
    overlay = tmp_path / "overlay.jsonl"
    package = _package("a", "20240101000001")
    write_canonical(base, [package, package])
    write_canonical(overlay, [package])

    with pytest.raises(ValueError, match="Duplicate base filing_id"):
        list(EffectiveCanonicalReader(base, overlay))


def test_reader_rejects_replacement_source_identity_change(tmp_path: Path) -> None:
    base = tmp_path / "base.jsonl"
    overlay = tmp_path / "overlay.jsonl"
    write_canonical(base, [_package("a", "20240101000001")])
    write_canonical(
        overlay,
        [_package("a", "20240101000001", version="2.2.1", source_suffix=":changed")],
    )

    with pytest.raises(ValueError, match="source_file_id set mismatch"):
        list(EffectiveCanonicalReader(base, overlay))


def test_reader_rejects_wrong_accepted_count(tmp_path: Path) -> None:
    base = tmp_path / "base.jsonl"
    overlay = tmp_path / "overlay.jsonl"
    package = _package("a", "20240101000001")
    write_canonical(base, [package])
    write_canonical(overlay, [package])

    reader = EffectiveCanonicalReader(
        base,
        overlay,
        expectations=EffectiveCanonicalExpectations(effective_tables=999),
    )
    with pytest.raises(ValueError, match="effective_tables"):
        list(reader)
