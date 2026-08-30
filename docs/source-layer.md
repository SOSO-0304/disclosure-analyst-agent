# Accepted Canonical -> Isolated PostgreSQL Source Layer

Canonical parser is frozen at schema `2.2.0`. The accepted final snapshot already
contains the DART parser `2.2.1` repairs. Reopen parser work only when downstream
evidence demonstrates a concrete preservation loss.

## 1. Validate the accepted snapshot

The reader transparently decompresses and streams the snapshot exactly once. It hashes
the logical JSONL bytes, validates every package, rejects duplicate filing IDs, and
checks the accepted whole-corpus invariants.

```powershell
python scripts/validate_effective_canonical.py `
  --input data\processed\canonical-v221-final.jsonl.gz `
  --manifest data\processed\canonical-v221-final.manifest.json
```

Accepted corpus invariants:

```text
snapshot packages         4,204
overlay packages              0
effective packages        4,204
effective documents       4,619
effective success         4,513
effective partial           106
effective failed               0
effective tables       1,580,832
replaced packages              0
```

The deterministic output is `canonical-v221-final.manifest.json`. It contains the
snapshot's logical SHA-256 and is required by the database loader. Re-running with an
identical input produces the same manifest bytes and hash. The reader still supports
the historical `--base` plus `--overlay` workflow, but it is not the production input.

## 2. Apply the additive source-layer migration

The perf Compose stack uses a separate container, port, database, user, network and
volume. Never run the default Compose project while working on this branch.

```powershell
$PerfDatabaseUrl = (
  Get-Content .env.perf |
    Where-Object { $_ -like "PERF_DATABASE_URL=*" } |
    Select-Object -First 1
) -replace "^PERF_DATABASE_URL=", ""

if (-not $PerfDatabaseUrl) {
  throw "PERF_DATABASE_URL is missing from .env.perf"
}
$env:DATABASE_URL = $PerfDatabaseUrl

python -c "from disclosure_agent.config import get_settings; print(get_settings().database_url)"
alembic upgrade head
```

The printed URL must contain port `55432` and database `disclosure_perf` before
Alembic runs. The migration creates only the generic source layer and its isolated
UNLOGGED staging schema.

New public tables:

```text
load_runs
source_companies
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

```powershell
python scripts/load_source_layer.py `
  --input data\processed\canonical-v221-final.jsonl.gz `
  --manifest data\processed\canonical-v221-final.manifest.json `
  --database-url $PerfDatabaseUrl
```

The loader follows Controller -> Service -> Repository. It stages all rows in
`source_staging`, validates counts and parent/reference integrity, verifies the effective
manifest again while streaming, and only then promotes rows to public tables using
canonical-ID upserts. A PostgreSQL advisory transaction lock serializes full source
loads. If validation or promotion fails, the transaction rolls back without changing
the accepted public source snapshot.

The load run ID is derived from the validated manifest hash, so repeating the same input
updates the same source snapshot rather than creating duplicate canonical rows. After
the load, run `scripts/verify_source_layer_db.py` against the same perf database URL.
