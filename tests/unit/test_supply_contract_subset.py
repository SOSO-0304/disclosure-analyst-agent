from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import orjson
import pytest

from disclosure_agent.domain.models import (
    CanonicalDocument,
    CompanyIdentity,
    ContentFormat,
    CorpusManifestEntry,
    DocumentGroup,
    FilingMetadata,
    FilingPackage,
    ManifestFileFormat,
    ParserProfile,
    ParseStatus,
    ParseSummary,
    SourceFile,
    SourceRole,
)
from disclosure_agent.subsets.supply_contract import (
    SUPPLY_CONTRACT_SUBTYPE,
    SubsetBuildError,
    build_supply_contract_subset,
)


def _package(
    filing_id: str,
    receipt_number: str,
    subtype: str,
    *,
    source_hash: str | None = None,
) -> FilingPackage:
    source_id = f"source:{filing_id}"
    return FilingPackage(
        filing_id=filing_id,
        company=CompanyIdentity(
            corp_code="00123456",
            stock_code="123456",
            corp_name="테스트회사",
            listed_name="테스트회사",
            industry="테스트업",
            sector="테스트섹터",
        ),
        filing=FilingMetadata(
            doc_id=filing_id,
            document_group=DocumentGroup.EXCHANGE,
            document_subtype=subtype,
            report_name_raw=subtype,
            receipt_number=receipt_number,
            receipt_date=date(2026, 1, 2),
            filer_name="테스트회사",
        ),
        source_files=[
            SourceFile(
                source_file_id=source_id,
                archive_path_raw=f"raw/exchange/{filing_id}.xml",
                archive_path_normalized=f"raw/exchange/{filing_id}.xml",
                file_name=f"{filing_id}.xml",
                source_role=SourceRole.PRIMARY_REPORT,
                declared_extension="xml",
                detected_content_format=ContentFormat.HTML,
                parser_profile=ParserProfile.XFORMS_HTML,
                sha256=source_hash,
                size_bytes=100,
                is_primary=True,
            )
        ],
        documents=[
            CanonicalDocument(
                document_id=f"document:{filing_id}",
                filing_id=filing_id,
                document_role=SourceRole.PRIMARY_REPORT,
                primary_source_file_id=source_id,
                source_file_ids=[source_id],
                parse_summary=ParseSummary(
                    status=ParseStatus.SUCCESS,
                    parser_name="ExchangeParser",
                    parser_version="2.2.0",
                ),
            )
        ],
    )


def _inventory_entry(
    filing_id: str,
    receipt_number: str,
    subtype: str,
) -> CorpusManifestEntry:
    return CorpusManifestEntry(
        doc_id=filing_id,
        corp_code="00123456",
        corp_name="테스트회사",
        listed_name="테스트회사",
        stock_code="123456",
        industry="테스트업",
        sector="테스트섹터",
        doc_group=DocumentGroup.EXCHANGE,
        doc_subtype=subtype,
        report_nm=subtype,
        is_correction=False,
        rcept_no=receipt_number,
        rcept_dt="20260102",
        flr_nm="테스트회사",
        file_path=f"raw/exchange/{filing_id}.xml",
        file_format=ManifestFileFormat.XML,
        n_files=1,
    )


def _write_jsonl(path: Path, records: list[object]) -> None:
    with path.open("wb") as stream:
        for record in records:
            if hasattr(record, "model_dump"):
                payload = record.model_dump(mode="json")
            else:
                payload = record
            stream.write(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS) + b"\n")


def test_build_subset_uses_exact_subtype_and_writes_manifest(tmp_path: Path):
    present_hash = "a" * 64
    supply_a = _package("exchange_a", "20260102800001", SUPPLY_CONTRACT_SUBTYPE)
    termination = _package("exchange_b", "20260102800002", "단일판매공급계약해지")
    supply_c = _package(
        "exchange_c",
        "20260102800003",
        SUPPLY_CONTRACT_SUBTYPE,
        source_hash=present_hash,
    )

    canonical = tmp_path / "canonical.jsonl"
    inventory = tmp_path / "manifest.jsonl"
    output = tmp_path / "subsets" / "supply-contract.jsonl"
    manifest = tmp_path / "subsets" / "supply-contract.manifest.json"

    _write_jsonl(canonical, [supply_a, termination, supply_c])
    _write_jsonl(
        inventory,
        [
            _inventory_entry("exchange_a", "20260102800001", SUPPLY_CONTRACT_SUBTYPE),
            _inventory_entry("exchange_b", "20260102800002", "단일판매공급계약해지"),
            _inventory_entry("exchange_c", "20260102800003", SUPPLY_CONTRACT_SUBTYPE),
        ],
    )

    metadata = build_supply_contract_subset(
        canonical,
        inventory_path=inventory,
        output_path=output,
        manifest_path=manifest,
        progress_every=0,
    )

    selected = [orjson.loads(line) for line in output.read_bytes().splitlines()]
    assert [item["filing_id"] for item in selected] == ["exchange_a", "exchange_c"]
    assert metadata["selected_package_count"] == 2
    assert metadata["selected_document_count"] == 2
    assert metadata["selection"] == {
        "document_group": "exchange",
        "document_subtype": SUPPLY_CONTRACT_SUBTYPE,
    }
    assert metadata["filing_ids"] == ["exchange_a", "exchange_c"]
    assert metadata["parser_versions"] == {"ExchangeParser": "2.2.0"}
    assert metadata["source_file_hash_coverage"] == {"present": 1, "total": 2}
    assert metadata["source_canonical"]["sha256"] == hashlib.sha256(
        canonical.read_bytes()
    ).hexdigest()
    assert metadata["subset"]["sha256"] == hashlib.sha256(
        output.read_bytes()
    ).hexdigest()
    assert manifest.is_file()


def test_build_subset_rejects_inventory_mismatch(tmp_path: Path):
    canonical = tmp_path / "canonical.jsonl"
    inventory = tmp_path / "manifest.jsonl"
    output = tmp_path / "subset.jsonl"
    manifest = tmp_path / "subset.manifest.json"

    _write_jsonl(
        canonical,
        [_package("exchange_a", "20260102800001", SUPPLY_CONTRACT_SUBTYPE)],
    )
    _write_jsonl(
        inventory,
        [
            _inventory_entry("exchange_a", "20260102800001", SUPPLY_CONTRACT_SUBTYPE),
            _inventory_entry("exchange_c", "20260102800003", SUPPLY_CONTRACT_SUBTYPE),
        ],
    )

    with pytest.raises(SubsetBuildError, match="does not match inventory"):
        build_supply_contract_subset(
            canonical,
            inventory_path=inventory,
            output_path=output,
            manifest_path=manifest,
            progress_every=0,
        )

    assert not output.exists()
    assert not manifest.exists()
