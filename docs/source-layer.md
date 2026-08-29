# Effective Canonical -> PostgreSQL Source Layer

Canonical parser is frozen at schema `2.2.0` with the accepted DART parser `2.2.1`
overlay. Reopen parser work only when downstream evidence demonstrates a concrete
preservation loss.

## 1. Validate the effective view

The official merge key is `filing_id`. The reader streams the large base exactly once,
keeps the overlay in memory, rejects duplicate/unknown overlay IDs, and verifies that
replacement packages preserve the original document/source identity sets.

```bash
python scripts/validate_effective_canonical.py \
  --base data/processed/canonical-v22-smoke.jsonl \
  --overlay data/processed/canonical-dart-221-overlay.jsonl
```

Accepted corpus invariants:

```text
base packages             4,204
overlay packages             77
effective packages        4,204
effective documents       4,619
effective success         4,602
effective partial            17
effective failed               0
effective tables       1,580,832
replaced packages             77
```

The deterministic output is
`data/processed/effective-canonical.manifest.json`. It contains the base and overlay
SHA-256 values and is required by the database loader. Re-running with identical inputs
produces the same manifest bytes and hash.

## 2. Apply the additive source-layer migration

The migration creates only the generic source layer and its isolated UNLOGGED staging
schema. Existing Supply Contract domain tables remain in place.

When running Alembic from the host Mac, point `DATABASE_URL` at `localhost`:

```bash
DATABASE_URL='postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure' \
  alembic upgrade head
```

New public tables:

```text
load_runs
source_filings
source_documents
source_sections
source_blocks
source_tables
```

`source_tables` stores table metadata, locator, searchable normalized text and the
canonical grid as JSONB. Canonical table cells are deliberately not exploded into a
multi-million-row SQL cell table. Later `generic_facts` extraction will normalize only
retrieval-relevant atomic values.

## 3. Load through staging and promote

```bash
python scripts/load_source_layer.py \
  --base data/processed/canonical-v22-smoke.jsonl \
  --overlay data/processed/canonical-dart-221-overlay.jsonl \
  --manifest data/processed/effective-canonical.manifest.json \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure
```

The loader follows Controller -> Service -> Repository. It stages all rows in
`source_staging`, validates counts and parent/reference integrity, verifies the effective
manifest again while streaming, and only then promotes rows to public tables using
canonical-ID upserts. A PostgreSQL advisory transaction lock serializes full source
loads. If validation or promotion fails, the transaction rolls back without changing
the accepted public source snapshot or typed Supply Contract tables.

The load run ID is derived from the validated manifest hash, so repeating the same input
updates the same source snapshot rather than creating duplicate canonical rows.
