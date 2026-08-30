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

The source/fact migrations create:

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

The accepted full load currently verifies:

```text
companies                     70
filings                     4,204
documents                   4,619
sections                   90,962
blocks                  2,700,533
tables                  1,580,832
generic facts           7,634,414
```

The exact `generic_facts` count is data-derived rather than a corpus invariant, but it
must match the count recorded in the deterministic load run.

## 4. Verify and profile the generic layer

```bash
python scripts/verify_source_layer_db.py \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure

python scripts/verify_supply_contract_db.py \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure
```

The accepted generic-fact profile is dominated by periodic reports. Only six facts in
the current canonical snapshot carry a concept code, so typed periodic financial
extraction must not assume XBRL concept coverage.

```bash
python scripts/profile_generic_facts.py \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure \
  --top 30 \
  --contains 매출 \
  --contains 시설투자 \
  --contains 자금조달 \
  --contains 계약
```

## 5. Materialise facility-investment typed events without re-reading canonical JSONL

The first full-corpus typed event domain is the 43 Exchange `신규시설투자등` filings.
The materialiser reads only `source_filings` and indexed `generic_facts`, so adding or
rerunning this domain does **not** scan the 27GB canonical JSONL again.

After applying the latest migration:

```bash
DATABASE_URL='postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure' \
  python -m alembic upgrade head
```

materialise and verify:

```bash
python scripts/load_facility_investment_events.py \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure

python scripts/verify_facility_investment_db.py \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure

python scripts/profile_facility_investment_events.py \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure \
  --limit 20
```

The new public tables are:

```text
source_events
source_event_evidence
facility_investment_events
```

`source_event_evidence` stores an attribute -> `generic_facts.fact_id` link instead of
copying evidence text. This keeps typed values auditable back to the exact canonical
filing/table/cell while avoiding another large evidence payload.

The facility extractor deliberately uses tolerant label normalization because Exchange
form numbering can shift across revisions. It extracts investment type/subject, KRW
amount, equity and ratio, purpose, period, decision date, disclosure-deferral fields,
and notes. Corrections are retained as distinct filings for now; correction-lineage
resolution is a subsequent domain step rather than an inferred merge.

After any migration or typed-event projection, the existing
`scripts/verify_supply_contract_db.py` exact counts, including `companies=34`, must remain
unchanged.
