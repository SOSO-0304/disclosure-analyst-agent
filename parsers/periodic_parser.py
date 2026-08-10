# parsers/periodic_parser.py

from pathlib import Path

from common import (
    parse_xml_to_markdown,
    save_markdown,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

INPUT_DIR = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "periodic"
)

OUTPUT_DIR = (
    PROJECT_ROOT
    / "data"
    / "parsed"
    / "periodic"
)


def parse_folder_name(
    folder_name: str,
) -> dict:

    parts = folder_name.split("_")

    return {
        "rcept_no": (
            parts[0]
            if len(parts) >= 1
            else folder_name
        ),
        "report_type": (
            parts[1]
            if len(parts) >= 2
            else "unknown"
        ),
        "base_year": (
            parts[2]
            if len(parts) >= 3
            else ""
        ),
        "base_month": (
            parts[3]
            if len(parts) >= 4
            else ""
        ),
    }


def parse_periodic():

    success_count = 0
    error_count = 0

    errors = []

    company_dirs = sorted(
        path
        for path in INPUT_DIR.iterdir()
        if path.is_dir()
    )

    for company_dir in company_dirs:

        company = company_dir.name

        print(f"\n[기업] {company}")

        disclosure_dirs = sorted(
            path
            for path in company_dir.iterdir()
            if path.is_dir()
        )

        for disclosure_dir in disclosure_dirs:

            folder_name = (
                disclosure_dir.name
            )

            metadata = parse_folder_name(
                folder_name
            )

            document_id = (
                metadata["rcept_no"]
            )

            xml_files = sorted(
                disclosure_dir.glob("*.xml")
            )

            if not xml_files:
                continue

            try:

                markdown_parts = []

                for xml_path in xml_files:

                    markdown = (
                        parse_xml_to_markdown(
                            xml_path=xml_path,
                            company=company,
                            doc_group=(
                                "periodic/"
                                + metadata["report_type"]
                            ),
                            document_id=document_id,
                        )
                    )

                    markdown_parts.append(
                        markdown
                    )

                metadata_markdown = (
                    f"# {company} 정기공시\n\n"
                    f"- 접수번호: `{document_id}`\n"
                    f"- 보고서 유형: `{metadata['report_type']}`\n"
                    f"- 기준 연도: `{metadata['base_year']}`\n"
                    f"- 기준 월: `{metadata['base_month']}`\n"
                    f"- 원본 폴더: `{folder_name}`\n"
                    "\n---\n\n"
                )

                final_markdown = (
                    metadata_markdown
                    + "\n\n---\n\n".join(
                        markdown_parts
                    )
                )

                output_path = (
                    OUTPUT_DIR
                    / company
                    / f"{folder_name}.md"
                )

                save_markdown(
                    final_markdown,
                    output_path,
                )

                success_count += 1

            except Exception as e:

                error_count += 1

                errors.append({
                    "company": company,
                    "document_id": folder_name,
                    "error": str(e),
                })

                print(
                    f"[ERROR] {company} / "
                    f"{folder_name}: {e}"
                )

    print("\n============================")
    print("Periodic Parsing 완료")
    print("============================")
    print(f"성공: {success_count}")
    print(f"실패: {error_count}")

    save_errors(errors)


def save_errors(errors):

    if not errors:
        return

    error_path = (
        OUTPUT_DIR
        / "_parsing_errors.txt"
    )

    error_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        error_path,
        "w",
        encoding="utf-8",
    ) as f:

        for error in errors:

            f.write(
                f"{error['company']} | "
                f"{error['document_id']} | "
                f"{error['error']}\n"
            )


if __name__ == "__main__":
    parse_periodic()