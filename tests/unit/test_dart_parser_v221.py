from __future__ import annotations

from pathlib import Path

import pytest

from disclosure_agent.domain.models import SourceFile, SourceRole
from disclosure_agent.parsing.dart_parser import DartParser
from disclosure_agent.parsing.xml_loader import load_dart_xml


@pytest.mark.parametrize(
    ("eng_attribute", "expected_eng"),
    [
        ('ENG=""Snow Corporation"', '"Snow Corporation'),
        ('ENG="Accrued Expenses""', 'Accrued Expenses"'),
        ('ENG="" CJ telenix co.,Ltd ""', '" CJ telenix co.,Ltd "'),
        (
            'ENG="　"Proceeds from disposal of investments""',
            '　"Proceeds from disposal of investments"',
        ),
    ],
)
def test_malformed_eng_quotes_are_preserved_without_structural_recovery(
    tmp_path: Path,
    eng_attribute: str,
    expected_eng: str,
) -> None:
    path = tmp_path / "sample.xml"
    raw = (
        "<DOCUMENT><BODY><TABLE><TR>"
        f"<TH {eng_attribute}><P>항목</P></TH><TD>100</TD>"
        "</TR></TABLE><P>after S&amp;P</P></BODY></DOCUMENT>"
    ).encode()
    path.write_bytes(raw)

    loaded = load_dart_xml(path, "source")

    assert loaded.structural_recovery is False
    assert loaded.root.xpath("//*[local-name()='TH']")[0].get("ENG") == expected_eng
    assert "after S&P" in "".join(loaded.root.itertext())
    assert any(
        issue.issue_code == "malformed_eng_attribute_quote_preserved"
        for issue in loaded.issues
    )
    assert path.read_bytes() == raw


def test_dart_parser_221_keeps_table_and_tail_after_malformed_eng(tmp_path: Path) -> None:
    path = tmp_path / "sample.xml"
    raw = (
        '<DOCUMENT><BODY><TABLE><TR><TH ENG=""Snow Corporation"><P>회사</P></TH>'
        "<TD>100</TD></TR></TABLE><P>tail R&amp;D S&amp;P</P></BODY></DOCUMENT>"
    ).encode()
    path.write_bytes(raw)
    source = SourceFile(
        source_file_id="source",
        archive_path_raw="sample.xml",
        archive_path_normalized="sample.xml",
        file_name="sample.xml",
        declared_extension="xml",
        source_role=SourceRole.PRIMARY_REPORT,
    )

    document = DartParser().parse(path, source, filing_id="filing", title="test")
    tables = [block.table for block in document.blocks if block.table is not None]
    table_text = " ".join(cell.text_raw for table in tables for cell in table.cells)
    block_text = " ".join(
        block.text_raw or "" for block in document.blocks if block.table is None
    )
    emitted_text = f"{table_text} {block_text}"

    assert document.parse_summary.parser_version == "2.2.1"
    assert document.parse_summary.status.value == "success"
    assert len(tables) == 1
    assert "회사" in emitted_text
    assert "100" in emitted_text
    assert "tail R&D S&P" in emitted_text
    assert path.read_bytes() == raw
