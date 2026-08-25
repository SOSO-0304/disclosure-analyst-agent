"""Batch conversion from manifest/raw corpus to canonical JSONL."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from disclosure_agent.parsing.document_parser import DocumentParser
from disclosure_agent.storage.jsonl import append_canonical


def _resolve_files(corpus_root: Path, row: dict) -> list[Path]:
    raw_path = corpus_root / row["file_path"]
    if raw_path.is_file():
        return [raw_path]
    if raw_path.is_dir():
        return sorted(raw_path.glob("*.xml"))
    # Some manifests point to a logical path while multi-file reports are siblings.
    parent = raw_path.parent
    receipt = str(row["rcept_no"])
    matches = sorted(parent.glob(f"{receipt}*.xml")) if parent.exists() else []
    if matches:
        return matches
    # Final corpus-wide fallback by receipt number; useful with Unicode-normalized company folders.
    return sorted((corpus_root / "raw" / row["doc_group"]).rglob(f"{receipt}*.xml"))


def parse_corpus(corpus_root: str | Path, output_path: str | Path) -> Counter:
    root = Path(corpus_root)
    manifest_path = root / "manifest.jsonl"
    output = Path(output_path)
    if output.exists():
        output.unlink()
    parser = DocumentParser()
    stats: Counter = Counter()
    with manifest_path.open(encoding="utf-8") as fp:
        for line in fp:
            row = json.loads(line)
            try:
                files = _resolve_files(root, row)
                if not files:
                    raise FileNotFoundError(row["file_path"])
                document = parser.parse(files, manifest=row)
                append_canonical(output, document)
                stats["parsed"] += 1
                stats[f"parsed:{row['doc_group']}"] += 1
            except Exception as exc:
                stats["failed"] += 1
                stats[f"failed:{row.get('doc_group', 'unknown')}"] += 1
                print(f"FAILED {row.get('doc_id')}: {type(exc).__name__}: {exc}")
    return stats


def main() -> None:
    import argparse
    argp = argparse.ArgumentParser()
    argp.add_argument("corpus_root", type=Path)
    argp.add_argument("--output", type=Path, default=Path("data/processed/canonical.jsonl"))
    args = argp.parse_args()
    stats = parse_corpus(args.corpus_root, args.output)
    print("\n=== canonical parsing ===")
    for key in sorted(stats):
        print(f"{key:24} {stats[key]}")


if __name__ == "__main__":
    main()
