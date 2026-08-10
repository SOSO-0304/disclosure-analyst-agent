# parsers/exchange_common.py

from pathlib import Path
import html
import re

from bs4 import BeautifulSoup, NavigableString, Tag


def clean_text(text: str | None) -> str:
    if not text:
        return ""

    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)

    return text.strip()


def escape_markdown(text: str) -> str:
    return text.replace("|", r"\|")


def load_exchange_html(path: Path) -> BeautifulSoup:
    """
    exchange 파일은 확장자는 .xml이지만
    내부 구조는 HTML/XForms 계열이므로 HTML parser로 읽는다.
    """

    raw = path.read_bytes()

    soup = BeautifulSoup(
        raw,
        "html.parser",
    )

    # 렌더링용 요소 제거
    for tag in soup.find_all(
        ["style", "script", "noscript"]
    ):
        tag.decompose()

    return soup


def extract_cell_text(cell: Tag) -> str:
    """
    하나의 td/th 내부 텍스트만 추출한다.

    중요한 점:
    - 다른 tr 내용은 절대 포함하지 않음
    - span 내용 포함
    - <br>은 Markdown 셀 내부 줄바꿈으로 보존
    """

    parts = []

    for node in cell.descendants:

        if isinstance(node, NavigableString):
            text = clean_text(str(node))

            if text:
                parts.append(text)

        elif isinstance(node, Tag):

            if node.name == "br":
                parts.append("<br>")

            elif node.name == "input":
                value = clean_text(
                    node.get("value")
                )

                if value:
                    parts.append(value)

    # <br> 주변 공백 정리
    result = []

    for part in parts:

        if part == "<br>":

            if result and result[-1] != "<br>":
                result.append(part)

        else:
            result.append(part)

    text = ""

    for i, part in enumerate(result):

        if part == "<br>":
            text += "<br>"
            continue

        if (
            text
            and not text.endswith("<br>")
        ):
            text += " "

        text += part

    return escape_markdown(
        text.strip()
    )


def get_direct_rows(
    table: Tag,
) -> list[Tag]:
    """
    현재 table에 실제로 속한 tr만 반환.
    nested table의 tr은 제외한다.
    """

    rows = []

    for tr in table.find_all("tr"):

        if tr.find_parent("table") is table:
            rows.append(tr)

    return rows


def get_direct_cells(
    tr: Tag,
) -> list[Tag]:
    """
    현재 tr 바로 아래의 td/th만 반환한다.
    """

    return tr.find_all(
        ["td", "th"],
        recursive=False,
    )


def parse_span(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


def build_table_grid(
    table: Tag,
) -> list[list[str]]:
    """
    HTML rowspan/colspan을 실제 2차원 grid로 펼친다.

    rowspan:
        아래 행에도 값을 반복

    colspan:
        실제 column 수만큼 펼치고
        첫 위치에만 값을 남긴다.
    """

    html_rows = get_direct_rows(
        table
    )

    if not html_rows:
        return []

    grid = []

    # col index -> [text, 남은 row 수]
    active_rowspans = {}

    for tr in html_rows:

        row = {}
        cells = get_direct_cells(tr)

        # 먼저 이전 행에서 내려온 rowspan 배치
        for col in sorted(
            list(active_rowspans.keys())
        ):

            text, remaining = (
                active_rowspans[col]
            )

            row[col] = text

            remaining -= 1

            if remaining <= 0:
                del active_rowspans[col]

            else:
                active_rowspans[col] = [
                    text,
                    remaining,
                ]

        col = 0

        for cell in cells:

            # 이미 rowspan으로 차 있는 column 건너뜀
            while col in row:
                col += 1

            text = extract_cell_text(
                cell
            )

            rowspan = parse_span(
                cell.get("rowspan")
            )

            colspan = parse_span(
                cell.get("colspan")
            )

            # colspan 영역
            for offset in range(colspan):

                current_col = col + offset

                row[current_col] = (
                    text
                    if offset == 0
                    else ""
                )

                # rowspan 저장
                if rowspan > 1:

                    active_rowspans[
                        current_col
                    ] = [
                        (
                            text
                            if offset == 0
                            else ""
                        ),
                        rowspan - 1,
                    ]

            col += colspan

        grid.append(row)

    # 전체 column 수
    max_col = max(
        max(row.keys())
        for row in grid
        if row
    )

    width = max_col + 1

    result = []

    for row in grid:

        result.append([
            row.get(col, "")
            for col in range(width)
        ])

    return result


def table_to_markdown(
    grid: list[list[str]],
) -> str:
    """
    grid → Markdown pipe table
    """

    if not grid:
        return ""

    width = max(
        len(row)
        for row in grid
    )

    normalized = [
        row + [""] * (
            width - len(row)
        )
        for row in grid
    ]

    header = normalized[0]

    lines = [
        "| "
        + " | ".join(header)
        + " |",

        "| "
        + " | ".join(
            ["---"] * width
        )
        + " |",
    ]

    for row in normalized[1:]:

        lines.append(
            "| "
            + " | ".join(row)
            + " |"
        )

    return "\n".join(lines)


def is_hidden(tag: Tag) -> bool:
    style = (
        tag.get("style", "")
        .lower()
        .replace(" ", "")
    )

    return (
        "display:none"
        in style
    )


def render_exchange(
    soup: BeautifulSoup,
) -> list[str]:
    """
    실제 문서 순서대로
    제목 + table을 Markdown으로 변환.
    """

    lines = []

    body = soup.body or soup

    for node in body.descendants:

        if not isinstance(node, Tag):
            continue

        # 숨김 요소 제외
        if is_hidden(node):
            continue

        # ----------------------------
        # 제목 span
        # ----------------------------

        if node.name == "span":

            # table 내부 span은 table에서 처리
            if node.find_parent("table"):
                continue

            text = clean_text(
                node.get_text(
                    " ",
                    strip=True,
                )
            )

            if not text:
                continue

            style = (
                node.get(
                    "style",
                    "",
                )
                .lower()
                .replace(" ", "")
            )

            # 실제 문서 제목 역할만
            if "font-weight:bold" in style:

                lines.append(
                    f"## {text}"
                )

                lines.append("")

        # ----------------------------
        # TABLE
        # ----------------------------

        elif node.name == "table":

            # nested table은 상위 table 처리 시 제외
            if node.find_parent("table"):
                continue

            grid = build_table_grid(
                node
            )

            markdown = (
                table_to_markdown(
                    grid
                )
            )

            if markdown:
                lines.append(markdown)
                lines.append("")

    return lines


def parse_exchange_xml(
    xml_path: Path,
    company: str,
    document_id: str,
) -> str:

    soup = load_exchange_html(
        xml_path
    )

    lines = [
        f"# {company}",
        "",
        f"- 문서 ID: `{document_id}`",
        "- 문서 유형: `exchange`",
        f"- 원본 파일: `{xml_path.name}`",
        "",
        "---",
        "",
    ]

    lines.extend(
        render_exchange(
            soup
        )
    )

    markdown = "\n".join(
        lines
    )

    markdown = re.sub(
        r"\n{3,}",
        "\n\n",
        markdown,
    )

    return (
        markdown.strip()
        + "\n"
    )


def save_markdown(
    markdown: str,
    output_path: Path,
) -> None:

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        markdown,
        encoding="utf-8",
    )