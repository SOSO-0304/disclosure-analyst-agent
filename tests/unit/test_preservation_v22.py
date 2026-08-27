"""Real audit regressions: source preservation, not merely parser status."""

from pathlib import Path

import pytest
from lxml import etree

from disclosure_agent.domain.models import SourceFile, SourceRole, TableCell, TableData
from disclosure_agent.parsing.dart_parser import DartParser
from disclosure_agent.parsing.exchange_parser import ExchangeParser
from disclosure_agent.parsing.markup_repair import repair_dart_xml
from disclosure_agent.parsing.table_parser import parse_table
from disclosure_agent.parsing.xml_loader import load_dart_xml


def parse(tmp_path: Path, body: str, *, html: bool = False):
    path = tmp_path / "sample.xml"
    raw = (
        ("<html><body>" + body + "</body></html>")
        if html
        else (
            '<?xml version="1.0" encoding="utf-8"?>'
            + "<DOCUMENT><BODY>"
            + body
            + "</BODY></DOCUMENT>"
        )
    ).encode()
    path.write_bytes(raw)
    source = SourceFile(
        source_file_id="s",
        archive_path_raw="sample.xml",
        archive_path_normalized="sample.xml",
        file_name="sample.xml",
        declared_extension="xml",
        source_role=SourceRole.PRIMARY_REPORT,
    )
    parser = ExchangeParser() if html else DartParser()
    doc = parser.parse(path, source, filing_id="f", title="test")
    assert path.read_bytes() == raw
    return doc


def emitted(doc):
    parts = []
    for block in doc.blocks:
        if block.table:
            parts.extend(c.text_raw for c in block.table.cells)
        else:
            parts.append(block.text_raw or "")
    return " ".join(parts)


def test_named_entities_and_ampersands_survive(tmp_path):
    doc = parse(tmp_path, "<P>Intel &reg; R&D S펜 & Touchscreen &mystery;</P>")
    assert "Intel ® R&D S펜 & Touchscreen &mystery;" in emitted(doc)
    assert doc.parse_summary.status.value == "success"


def test_recovery_cannot_drop_predefined_or_numeric_refs(tmp_path):
    path = tmp_path / "broken.xml"
    path.write_text(
        "<DOCUMENT><BODY><P>A &amp; B &#38; C <B>oops</P><P>R&amp;D</P></BODY></DOCUMENT>"
    )
    loaded = load_dart_xml(path, "s")
    assert loaded.structural_recovery
    value = "".join(loaded.root.itertext())
    assert "A & B & C" in value
    assert "R&D" in value


def test_literal_labels_apostrophes_and_inline_split_brackets(tmp_path):
    doc = parse(
        tmp_path,
        "<P><PUBG: 배틀그라운드> <BGMI> <신설 '23. 3.16.> "
        "<SPAN><배틀그라운드</SPAN><SPAN>> 성장</SPAN></P>",
    )
    assert "<PUBG: 배틀그라운드> <BGMI> <신설 '23. 3.16.>" in emitted(doc)
    assert "<배틀그라운드> 성장" in emitted(doc)
    assert doc.parse_summary.status.value == "success"


def test_valid_unknown_extension_and_declared_namespace_stay_tags():
    raw = b'<DOCUMENT xmlns:x="test"><x:item><P>A &amp; B</P></x:item><Custom>A</Custom></DOCUMENT>'
    assert repair_dart_xml(raw, "s").content == raw


def test_known_literal_paired_real_element_not_escaped():
    raw = b"<DOCUMENT><BGMI><P>real extension</P></BGMI></DOCUMENT>"
    assert repair_dart_xml(raw, "s").content == raw


def test_double_attribute_quote_repaired_without_swallowing_table(tmp_path):
    body = '<TABLE><TR><TH ENG="Other receivables and others""><P>미수금 등</P></TH>'
    body += "<TD>100</TD></TR></TABLE><P>after S&P</P>"
    doc = parse(tmp_path, body)
    assert "미수금 등" in emitted(doc)
    assert "after S&P" in emitted(doc)
    assert doc.parse_summary.status.value == "success"


def test_cover_correction_root_text_and_tail_preserved_in_order(tmp_path):
    body = (
        "before<COVER><P>표지</P></COVER><LIBRARY><CORRECTION><TABLE><TR><TD>정정 100→200</TD>"
        "</TR></TABLE></CORRECTION></LIBRARY><SECTION-1><TITLE>사업</TITLE><P>본문</P>"
        "</SECTION-1>after"
    )
    doc = parse(tmp_path, body)
    value = emitted(doc)
    assert all(
        term in value for term in ["before", "표지", "정정 100→200", "사업", "본문", "after"]
    )
    assert [
        value.index(t) for t in ["before", "표지", "정정 100→200", "사업", "본문", "after"]
    ] == sorted(value.index(t) for t in ["before", "표지", "정정 100→200", "사업", "본문", "after"])


@pytest.mark.parametrize("html", [False, True])
def test_nested_tables_are_linked_once_not_flattened(tmp_path, html):
    body = "<TABLE><TR><TD>before<TABLE><TR><TD>R&amp;D</TD></TR></TABLE>after</TD></TR></TABLE>"
    if html:
        body = body.lower().replace("r&amp;d", "R&amp;D")
    doc = parse(tmp_path, body, html=html)
    tables = [b.table for b in doc.blocks if b.table]
    assert len(tables) == 2
    assert emitted(doc).count("R&D") == 1
    assert tables[0].row_count == 1
    assert tables[0].cells[0].text_raw == "beforeafter"
    assert tables[0].cells[0].nested_table_ids == [tables[1].table_id]
    assert tables[1].parent_table_id == tables[0].table_id
    assert tables[1].parent_cell_locator is not None


def test_grid_rejects_overlapping_rectangles_not_only_equal_origins():
    with pytest.raises(ValueError, match="overlap"):
        TableData(
            table_id="t",
            row_count=2,
            column_count=2,
            cells=[
                TableCell(row_index=0, column_index=0, row_span=2, column_span=2),
                TableCell(row_index=1, column_index=1),
            ],
        )


def test_rowspan_and_empty_cell_placement_stays_correct():
    table = parse_table(
        etree.fromstring(
            b'<TABLE><TR><TD ROWSPAN="2">A</TD><TD/></TR><TR><TD>20</TD></TR></TABLE>'
        ),
        "t",
        "s",
    )
    assert [(c.row_index, c.column_index, c.text_raw) for c in table.cells] == [
        (0, 0, "A"),
        (0, 1, ""),
        (1, 1, "20"),
    ]


def test_html_ampersands_preserved(tmp_path):
    doc = parse(tmp_path, "<p>R&D M&A S&P</p>", html=True)
    assert "R&D M&A S&P" in emitted(doc)


def test_cdata_not_decoded_even_during_structural_recovery(tmp_path):
    path = tmp_path / "broken.xml"
    path.write_text(
        "<DOCUMENT><BODY><P><![CDATA[R&amp;D <literal>]]><B>bad</P>"
        "<P>A &amp; B</P></BODY></DOCUMENT>"
    )
    loaded = load_dart_xml(path, "s")
    value = "".join(loaded.root.itertext())
    assert "R&amp;D <literal>" in value
    assert "A & B" in value


def test_entities_in_attributes_and_non_ascii_encoding_survive_recovery(tmp_path):
    path = tmp_path / "cp949.xml"
    path.write_bytes(
        (
            '<?xml version="1.0" encoding="cp949"?>'
            '<DOCUMENT><P ENG="A &amp; B">한글 <B>mismatch</P>'
            "<P>S&amp;P &#128; &#xAC00;</P></DOCUMENT>"
        ).encode("cp949")
    )
    loaded = load_dart_xml(path, "s")
    assert loaded.root[0].get("ENG") == "A & B"
    assert "S&P \x80 가" in "".join(loaded.root.itertext())


def test_external_entities_are_never_expanded(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("DO_NOT_EXPAND")
    path = tmp_path / "entity.xml"
    path.write_text(
        f'<!DOCTYPE DOCUMENT [<!ENTITY external SYSTEM "{secret.as_uri()}">]>'
        "<DOCUMENT><P>&external;<B>mismatch</P><P>R&amp;D</P></DOCUMENT>"
    )
    loaded = load_dart_xml(path, "s")
    value = "".join(loaded.root.itertext())
    assert "DO_NOT_EXPAND" not in value
    assert "&external;" in value
    assert "R&D" in value


def test_three_level_nested_tables_have_direct_parent_links(tmp_path):
    doc = parse(
        tmp_path,
        "<TABLE><TR><TD>A<TABLE><TR><TD>B<TABLE><TR><TD>C</TD></TR>"
        "</TABLE>D</TD></TR></TABLE>E</TD></TR></TABLE>",
    )
    outer, middle, inner = [block.table for block in doc.blocks if block.table]
    assert [t.cells[0].text_raw for t in (outer, middle, inner)] == ["AE", "BD", "C"]
    assert outer.cells[0].nested_table_ids == [middle.table_id]
    assert middle.cells[0].nested_table_ids == [inner.table_id]
    assert inner.parent_table_id == middle.table_id


def test_colspan_moves_past_entire_active_rowspan():
    table = parse_table(
        etree.fromstring(
            b'<TABLE><TR><TD>A</TD><TD ROWSPAN="2">B</TD></TR>'
            b'<TR><TD COLSPAN="2">C</TD></TR></TABLE>'
        ),
        "t",
        "s",
    )
    assert table.cells[-1].column_index == 2
    assert table.column_count == 4


def test_independent_auditor_self_tests():
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[2] / "scripts" / "canonical_audit.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--self-test"], capture_output=True, text=True, timeout=30
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_heading_after_child_section_uses_own_section_level(tmp_path):
    doc = parse(
        tmp_path,
        "<SECTION-1><SECTION-2><TITLE>child</TITLE></SECTION-2><TITLE>parent</TITLE></SECTION-1>",
    )
    headings = {b.text_raw: b.heading_level for b in doc.blocks if b.heading_level}
    assert headings == {"child": 2, "parent": 1}
