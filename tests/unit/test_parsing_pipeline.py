"""Regression tests for source inventory and canonical parsing."""

from __future__ import annotations

import json
from pathlib import Path

from lxml import etree
from pypdf import PdfWriter

from disclosure_agent.domain.models import (
    ContentFormat,
    CorpusManifestEntry,
    ParseStatus,
    SourceRole,
)
from disclosure_agent.inventory.builder import InventoryBuilder
from disclosure_agent.parsing.batch import parse_corpus
from disclosure_agent.parsing.content_detector import detect_content_format
from disclosure_agent.parsing.document_parser import DocumentParser
from disclosure_agent.parsing.pdf_parser import PdfParser
from disclosure_agent.parsing.periodic_html_parser import parse_viewer_metadata
from disclosure_agent.parsing.table_parser import parse_table
from disclosure_agent.storage.jsonl import read_canonical


def manifest_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "doc_id": "periodic-00126380-20240312000736",
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "listed_name": "삼성전자",
        "stock_code": "005930",
        "industry": "제조업",
        "sector": "IT",
        "doc_group": "periodic",
        "doc_subtype": "annual",
        "report_nm": "사업보고서 (2023.12)",
        "is_correction": False,
        "rcept_no": "20240312000736",
        "rcept_dt": "20240312",
        "flr_nm": "삼성전자",
        "base_year": 2023,
        "base_month": 12,
        "file_path": ("reports/periodic/삼성전자/20240312000736_annual_2023_12"),
        "file_format": "xml",
        "n_files": 3,
    }
    payload.update(overrides)
    return payload


def minimal_dart_xml(text: str = "매출액 100") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE DOCUMENT SYSTEM "dart.dtd">\n'
        "<DOCUMENT><BODY><SECTION-1><TITLE>사업의 내용</TITLE>"
        f"<P>{text}</P></SECTION-1></BODY></DOCUMENT>"
    )


def make_periodic_sources(root: Path) -> Path:
    directory = root / "raw" / "periodic" / "삼성전자" / "20240312000736_annual_2023_12"
    directory.mkdir(parents=True)
    for suffix in ("", "_00760", "_00761"):
        (directory / f"20240312000736{suffix}.xml").write_text(
            minimal_dart_xml(suffix or "본문"),
            encoding="utf-8",
        )
    return directory


def test_content_detection_does_not_trust_xml_extension(tmp_path: Path) -> None:
    source = tmp_path / "exchange.xml"
    source.write_text("<!doctype html><html><body>공시</body></html>", encoding="utf-8")

    assert detect_content_format(source) is ContentFormat.HTML


def test_content_detection_skips_dart_doctype(tmp_path: Path) -> None:
    source = tmp_path / "periodic.xml"
    source.write_text(minimal_dart_xml(), encoding="utf-8")

    assert detect_content_format(source) is ContentFormat.DART_XML


def test_inventory_assigns_roles_to_three_periodic_sources(tmp_path: Path) -> None:
    make_periodic_sources(tmp_path)
    entry = CorpusManifestEntry.model_validate(manifest_payload())

    _, resolved = InventoryBuilder(tmp_path, compute_hashes=False).build(entry)

    assert [item.source.source_role for item in resolved] == [
        SourceRole.PRIMARY_REPORT,
        SourceRole.SEPARATE_AUDIT_REPORT,
        SourceRole.CONSOLIDATED_AUDIT_REPORT,
    ]
    assert all(item.source.detected_content_format is ContentFormat.DART_XML for item in resolved)


def test_empty_table_cell_keeps_its_column_coordinate() -> None:
    root = etree.fromstring(b"<TABLE><TR><TD>A</TD><TD></TD><TD>C</TD></TR></TABLE>")

    table = parse_table(root, "table-1", "source-1")

    assert [cell.column_index for cell in table.cells] == [0, 1, 2]
    assert table.cells[1].text_raw == ""
    assert table.column_count == 3


def test_table_preserves_dart_numeric_attributes() -> None:
    root = etree.fromstring(
        b'<TABLE><TR><TD ACODE="revenue" ACONTEXT="current" '
        b'ADECIMAL="-6" AUNITVALUE="KRW" ANEGATED="true">1,000</TD></TR></TABLE>'
    )

    table = parse_table(root, "table-1", "source-1")
    cell = table.cells[0]

    assert str(cell.numeric_value) == "-1000"
    assert cell.concept_code == "revenue"
    assert cell.context_ref == "current"
    assert cell.decimals_raw == "-6"
    assert cell.unit_raw == "KRW"


def test_multi_file_periodic_becomes_three_semantic_documents(tmp_path: Path) -> None:
    make_periodic_sources(tmp_path)
    entry = CorpusManifestEntry.model_validate(manifest_payload())
    _, sources = InventoryBuilder(tmp_path, compute_hashes=False).build(entry)

    package = DocumentParser().parse(sources, manifest=entry)

    assert len(package.source_files) == 3
    assert len(package.documents) == 3
    assert [document.document_role for document in package.documents] == [
        SourceRole.PRIMARY_REPORT,
        SourceRole.SEPARATE_AUDIT_REPORT,
        SourceRole.CONSOLIDATED_AUDIT_REPORT,
    ]
    assert all(document.blocks for document in package.documents)


def test_xml_recovery_is_visible_as_partial_status(tmp_path: Path) -> None:
    directory = make_periodic_sources(tmp_path)
    (directory / "20240312000736.xml").write_text(
        "<DOCUMENT><BODY><SECTION-1><TITLE>깨진 문서</TITLE><P>본문",
        encoding="utf-8",
    )
    entry = CorpusManifestEntry.model_validate(manifest_payload())
    _, sources = InventoryBuilder(tmp_path, compute_hashes=False).build(entry)

    package = DocumentParser().parse(sources, manifest=entry)
    primary = package.documents[0]

    assert primary.parse_summary.status is ParseStatus.PARTIAL
    assert primary.parse_summary.recovered is True
    assert any(issue.issue_code == "markup_recovery" for issue in primary.parse_issues)


def test_viewer_parser_extracts_toc_metadata_only(tmp_path: Path) -> None:
    viewer = tmp_path / "20240514001522_viewer.html"
    viewer.write_text(
        """
        <html><body><script>
        var node1 = {};
        node1['text'] = "I. 회사의 개요";
        node1['id'] = "1";
        node1['rcpNo'] = "20240514001522";
        node1['dcmNo'] = "9945802";
        node1['eleId'] = "1";
        node1['offset'] = "0";
        node1['length'] = "120";
        node1['tocNo'] = "1";
        </script></body></html>
        """,
        encoding="utf-8",
    )

    metadata = parse_viewer_metadata(viewer)

    assert metadata["viewer_is_companion_only"] is True
    assert metadata["viewer_toc"][0]["text"] == "I. 회사의 개요"
    assert metadata["viewer_toc"][0]["offset"] == 0


def test_blank_pdf_is_failed_instead_of_false_success(tmp_path: Path) -> None:
    target = (
        tmp_path
        / "raw"
        / "periodic"
        / "삼성전자"
        / "20240312000736_quarter_2023_03"
        / "20240312000736.pdf"
    )
    target.parent.mkdir(parents=True)
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with target.open("wb") as stream:
        writer.write(stream)

    source_entry = CorpusManifestEntry.model_validate(
        manifest_payload(
            file_path=("raw/periodic/삼성전자/20240312000736_quarter_2023_03/20240312000736.pdf"),
            file_format="pdf+html",
            n_files=1,
        )
    )
    source = InventoryBuilder(tmp_path, compute_hashes=False).build_source_index()
    assert source_entry.rcept_no in source
    resolved_pdf = next(
        item
        for item in InventoryBuilder(tmp_path, compute_hashes=False).build(source_entry)[1]
        if item.path.suffix == ".pdf"
    )

    document = PdfParser().parse(
        resolved_pdf.path,
        resolved_pdf.source,
        filing_id=source_entry.doc_id,
        title=source_entry.report_nm,
    )

    assert document.parse_summary.status is ParseStatus.FAILED
    assert any(issue.issue_code == "pdf_text_unavailable" for issue in document.parse_issues)


def test_jsonl_reader_validates_filing_packages(tmp_path: Path) -> None:
    make_periodic_sources(tmp_path)
    entry = CorpusManifestEntry.model_validate(manifest_payload())
    _, sources = InventoryBuilder(tmp_path, compute_hashes=False).build(entry)
    package = DocumentParser().parse(sources, manifest=entry)
    output = tmp_path / "canonical.jsonl"
    output.write_text(package.model_dump_json() + "\n", encoding="utf-8")

    loaded = list(read_canonical(output))

    assert loaded == [package]


def test_batch_atomically_writes_one_package_per_manifest_row(tmp_path: Path) -> None:
    make_periodic_sources(tmp_path)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps(manifest_payload(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "processed" / "canonical.jsonl"
    output.parent.mkdir()
    output.write_text("previous output\n", encoding="utf-8")

    stats = parse_corpus(tmp_path, output, compute_hashes=False)
    packages = list(read_canonical(output))

    assert stats["packages"] == 1
    assert stats["inventory_failures"] == 0
    assert len(packages) == 1
    assert len(packages[0].documents) == 3
    assert not output.with_suffix(".jsonl.tmp").exists()


def test_manifest_fixture_is_json_serializable() -> None:
    json.dumps(manifest_payload(), ensure_ascii=False)
