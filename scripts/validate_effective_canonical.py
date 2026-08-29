#!/usr/bin/env python3
"""Validate the immutable base + DART v2.2.1 overlay effective canonical view."""

from __future__ import annotations

import argparse
from pathlib import Path

from disclosure_agent.storage.effective_canonical import (
    EffectiveCanonicalExpectations,
    EffectiveCanonicalReader,
)

DEFAULT_BASE = Path("data/processed/canonical-v22-smoke.jsonl")
DEFAULT_OVERLAY = Path("data/processed/canonical-dart-221-overlay.jsonl")
DEFAULT_MANIFEST = Path("data/processed/effective-canonical.manifest.json")

EXPECTED = EffectiveCanonicalExpectations(
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--no-strict-corpus-counts",
        action="store_true",
        help="Validate merge invariants without the accepted 4,204-package corpus counts.",
    )
    args = parser.parse_args()

    reader = EffectiveCanonicalReader(
        args.base,
        args.overlay,
        expectations=(
            EffectiveCanonicalExpectations() if args.no_strict_corpus_counts else EXPECTED
        ),
    )

    for _ in reader:
        pass

    manifest = reader.manifest
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.manifest.with_suffix(f"{args.manifest.suffix}.tmp")
    temporary.write_bytes(manifest.to_json_bytes())
    temporary.replace(args.manifest)

    print("=== effective canonical validation ===")
    print(f"base packages                   {manifest.base_packages}")
    print(f"overlay packages                {manifest.overlay_packages}")
    print(f"overlay IDs in base             {manifest.overlay_ids_in_base}")
    print(f"effective packages              {manifest.effective_packages}")
    print(f"effective documents             {manifest.effective_documents}")
    print(f"effective success               {manifest.effective_success}")
    print(f"effective partial               {manifest.effective_partial}")
    print(f"effective failed                {manifest.effective_failed}")
    print(f"effective tables                {manifest.effective_tables}")
    print(f"replaced packages               {manifest.replaced_packages}")
    print(f"replacement identity checks     {manifest.replacement_identity_checks}")
    print(f"duplicate filing IDs            {manifest.duplicate_filing_ids}")
    print(f"base sha256                     {manifest.base_sha256}")
    print(f"overlay sha256                  {manifest.overlay_sha256}")
    print(f"manifest sha256                 {manifest.sha256}")
    print(f"MANIFEST                        {args.manifest}")


if __name__ == "__main__":
    main()
