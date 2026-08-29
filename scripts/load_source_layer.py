#!/usr/bin/env python3
"""Load the accepted effective canonical view into the generic PostgreSQL source layer."""

from __future__ import annotations

import argparse
from pathlib import Path

import orjson

from disclosure_agent.services.source_layer_ingestion import ingest_effective_source_layer
from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.effective_canonical import (
    EffectiveCanonicalExpectations,
    EffectiveCanonicalManifest,
)

DEFAULT_BASE = Path("data/processed/canonical-v22-smoke.jsonl")
DEFAULT_OVERLAY = Path("data/processed/canonical-dart-221-overlay.jsonl")
DEFAULT_MANIFEST = Path("data/processed/effective-canonical.manifest.json")

ACCEPTED_EXPECTATIONS = EffectiveCanonicalExpectations(
    base_packages=4204,
    overlay_packages=77,
    effective_packages=4204,
    effective_documents=4619,
    effective_success=4602,
    effective_partial=17,
    effective_failed=0,
    effective_tables=1580832,
    replaced_packages=77,
)


def _read_manifest(path: Path) -> EffectiveCanonicalManifest:
    if not path.is_file():
        raise SystemExit(
            f"Validated manifest not found: {path}. "
            "Run scripts/validate_effective_canonical.py first."
        )
    try:
        return EffectiveCanonicalManifest(**orjson.loads(path.read_bytes()))
    except Exception as exc:
        raise SystemExit(f"Invalid effective canonical manifest: {path}") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--database-url")
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument(
        "--no-strict-corpus-counts",
        action="store_true",
        help="Disable accepted 4,204-package counts; merge and staging checks still apply.",
    )
    args = parser.parse_args()
    if args.progress_every < 1:
        parser.error("--progress-every must be at least 1")

    validated_manifest = _read_manifest(args.manifest)
    expectations = (
        EffectiveCanonicalExpectations()
        if args.no_strict_corpus_counts
        else ACCEPTED_EXPECTATIONS
    )
    expected_companies = None if args.no_strict_corpus_counts else 70

    def progress(number: int, counts: dict[str, int]) -> None:
        if number % args.progress_every == 0:
            print(
                f"[source load {number}] documents={counts.get('documents', 0)} "
                f"blocks={counts.get('blocks', 0)} tables={counts.get('tables', 0)}",
                flush=True,
            )

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        result = ingest_effective_source_layer(
            base_path=args.base,
            overlay_path=args.overlay,
            validated_manifest=validated_manifest,
            session=session,
            expectations=expectations,
            expected_company_count=expected_companies,
            progress=progress,
        )

    print("\n=== source layer load ===")
    print(f"load run                       {result.load_run_id}")
    for key in ("companies", "filings", "documents", "sections", "blocks", "tables"):
        print(f"{key:<30} {result.counts[key]}")
    print(f"manifest sha256                {result.manifest.sha256}")


if __name__ == "__main__":
    main()
