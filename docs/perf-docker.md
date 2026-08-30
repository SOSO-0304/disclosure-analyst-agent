# Isolated perf PostgreSQL

The Docker resources already running for `model/data-pipeline` are not shared with
`perf/canonical-pipeline`. Never run the repository's default `compose.yaml` while
working through this guide.

## Resource boundary

| Resource | Existing model stack | Isolated perf stack |
|---|---|---|
| Container | `disclosure-postgres` | `disclosure-perf-postgres` |
| Host port | `5432` | `55432` |
| Database | `disclosure` | `disclosure_perf` |
| Volume | existing model volume | `disclosure_perf_pgdata` |
| Compose project | existing project | `disclosure-perf` |
| Environment file | `.env` | `.env.perf` |

## Start on Windows PowerShell

Create a local environment file. It is ignored by Git.

```powershell
Copy-Item .env.perf.example .env.perf
```

Start only the isolated PostgreSQL service. Keep every option in this command so the
default stack cannot be selected accidentally.

```powershell
docker compose `
  -p disclosure-perf `
  -f compose.perf.yaml `
  --env-file .env.perf `
  up -d
```

Verify the isolated service and its published port.

```powershell
docker compose `
  -p disclosure-perf `
  -f compose.perf.yaml `
  --env-file .env.perf `
  ps

docker port disclosure-perf-postgres
```

The expected host mapping is `5432/tcp -> 0.0.0.0:55432` (and possibly the IPv6
equivalent). The existing `disclosure-postgres` container must remain running and
unchanged.

## Run migrations from the host

Load the dedicated URL into only the current PowerShell process.

```powershell
$env:DATABASE_URL = `
  "postgresql+psycopg://disclosure_perf:disclosure_perf_dev@localhost:55432/disclosure_perf"

alembic upgrade head
```

Explicit `--database-url` arguments should use the same `localhost:55432` URL. Never
use the default `localhost:5432/disclosure` URL for perf migrations or loads.

## Stop safely

Stop only the isolated stack. This preserves its named volume.

```powershell
docker compose `
  -p disclosure-perf `
  -f compose.perf.yaml `
  --env-file .env.perf `
  down
```

Do not add `-v`, and do not delete or rename either PostgreSQL volume.
