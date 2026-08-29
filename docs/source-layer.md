# Effective Canonical -> PostgreSQL Source + Generic Fact Layer

Canonical parser is frozen at schema `2.2.0` with the accepted DART parser `2.2.1`
overlay. Reopen parser work only when downstream evidence demonstrates a concrete
preservation loss.

## 1. Validate the effective view

The official merge key is `filing_id`. The reader streams the large base exactly once,
keeps the overlay in memory, rejects duplicate/unknown overlay IDs, and verifies that
replacement packages preserve the original document/source identity sets.

macOS/Linux:

```bash
python scripts/validate_effective_canonical.py \
  --base data/processed/canonical-v22-smoke.jsonl \
  --overlay data/processed/canonical-dart-221-overlay.jsonl
```

PowerShell:

```powershell
python scripts\validate_effective_canonical.py `
  --base data\processed\canonical-v22-smoke.jsonl `
  --overlay data\processed\canonical-dart-221-overlay.jsonl
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

Large generated JSONL files under `data/processed` are intentionally ignored by Git.
Keep them in external/cloud storage and copy them into the repository workspace when
needed. Small JSON manifests remain trackable.

## 2. Apply the additive migrations

The migrations create only the generic source/fact layers and the isolated UNLOGGED
staging tables. Existing Supply Contract domain tables remain in place. In particular,
the existing `companies` table stays the verified 34-company Supply Contract slice. The
full 70-company corpus master is stored separately in `source_companies`.

macOS/Linux:

```bash
DATABASE_URL='postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure' \
  python -m alembic upgrade head
```

PowerShell:

```powershell
$env:DATABASE_URL="postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure"
python -m alembic upgrade head
```

Public tables added by the first two migrations:

```text
load_runs
source_companies
source_filings
source_documents
source_sections
source_blocks
source_tables
generic_facts
```

`source_tables` stores table metadata, locator, searchable normalized text and the
canonical grid as JSONB. Canonical table cells are deliberately not exploded into a
blind SQL cell table.

`generic_facts` is a selective atomic retrieval layer. A cell is promoted when it has a
DART concept code, has a parsed numeric value, or is the right-most value in a compact
label/value row. Every fact retains filing/document/block/table identity, row/column,
row label, column header, section/table path, raw/normalized value, unit/currency,
concept/context identifiers, and source locator. It does not assign business semantics
such as `revenue` or `facility_investment`; typed event layers remain responsible for
those interpretations.

## 3. Load source rows and generic facts in one canonical pass

macOS/Linux:

```bash
python scripts/load_source_layer.py \
  --base data/processed/canonical-v22-smoke.jsonl \
  --overlay data/processed/canonical-dart-221-overlay.jsonl \
  --manifest data/processed/effective-canonical.manifest.json \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure
```

PowerShell:

```powershell
python scripts\load_source_layer.py `
  --base data\processed\canonical-v22-smoke.jsonl `
  --overlay data\processed\canonical-dart-221-overlay.jsonl `
  --manifest data\processed\effective-canonical.manifest.json `
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure
```

The loader follows Controller -> Service -> Repository. It stages all source rows and
selective generic facts in `source_staging`, validates counts and parent/reference
integrity, verifies the effective manifest again while streaming, and only then promotes
rows to public tables. A PostgreSQL advisory transaction lock serializes full source
loads. If source validation, fact validation, or promotion fails, the transaction rolls
back without changing the accepted public snapshot or typed Supply Contract tables.

The load run ID is derived from the validated manifest hash, so repeating the same input
updates the same source snapshot rather than creating duplicate canonical rows. The
exact `generic_facts` count is intentionally data-derived rather than hard-coded; the
staged count must equal the extractor-emitted count for that run.

## 4. Verify and profile before adding typed events

Verify the promoted source snapshot and the existing Supply Contract slice separately:

```bash
python scripts/verify_source_layer_db.py \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure

python scripts/verify_supply_contract_db.py \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure
```

`verify_source_layer_db.py` requires the runtime `generic_facts` count to match the
recorded load-run count and rejects an empty fact layer. The fact count is not otherwise
hard-coded because it is an extractor output rather than a corpus invariant.

Before designing new typed extractors, inspect the actual generic-fact distribution:

```bash
python scripts/profile_generic_facts.py \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure \
  --top 30 \
  --contains 매출 \
  --contains 시설투자 \
  --contains 자금조달 \
  --contains 계약
```

The profiler reports total/numeric/concept-coded facts, fact-kind distribution,
document-group distribution, top concept codes and labels, plus evidence-locatable
samples for requested substrings. Use this output to decide which high-value domains
need typed event extractors and which questions can be answered directly from generic
facts.

After migration and again after a full source load, `scripts/verify_supply_contract_db.py`
must retain its original exact counts, including `companies=34`.
