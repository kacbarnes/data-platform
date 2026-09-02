# Research data platform

A local, persistent Iceberg lakehouse used to learn and apply enterprise data
management practices without hiding durable data inside Docker's virtual disk.

## Architecture

- **Polaris 1.7.0**: Iceberg REST catalog and authorization boundary.
- **PostgreSQL 17**: persistent Polaris metastore.
- **RustFS**: local S3-compatible object storage, bind-mounted at
  `/home/kent/Data/DataLake/warehouse`.
- **DuckDB**: ingestion, validation, querying, and the operational control DB at
  `/home/kent/Data/Databases/duckdb/control.duckdb`.

Only source files belong in `landing`, `raw`, or `Archive`. Never manually edit
anything in `warehouse`; Iceberg and its catalog own that tree.

## First-time setup

Docker must be running and `docker info` must succeed for your user.

```bash
cd /home/kent/Projects/data-platform
./scripts/init-platform
mamba env create -f environment.yml
```

`init-platform` creates an untracked `.env` with random local credentials,
bootstraps the PostgreSQL-backed Polaris realm, creates the object-store bucket,
and creates the `research` catalog. It should be run only once for a new storage
root. Normal subsequent operation is:

```bash
docker compose up -d
docker compose ps
```

Endpoints are bound to localhost:

- Polaris REST catalog: `http://localhost:8181/api/catalog`
- Polaris health: `http://localhost:8182/q/health`
- RustFS API: `http://localhost:9000`
- RustFS console: `http://localhost:9001`

## First tracked migration

```bash
cd /home/kent/Projects/data-platform
set -a
source .env
set +a
mamba run -n data-platform python scripts/ingest_iceberg.py \
  /home/kent/Data/Archive/legacy-project-data/capstone/usaspending/bulk/all_assistance.parquet \
  --dataset-id usaspending.assistance \
  --namespace bronze \
  --table usaspending_assistance
```

The archived Parquet remains the immutable source artifact. The command creates
`research.bronze.usaspending_assistance` as an Iceberg table and records source
checksum, byte and row counts, schema, Git commit, status, and Iceberg snapshot
ID in `control.duckdb`. It refuses to overwrite an existing target.

Inspect the audit trail:

```bash
mamba run -n data-platform python - <<'PY'
import duckdb
print(duckdb.sql("""
  FROM '/home/kent/Data/Databases/duckdb/control.duckdb'.ingestion_runs
  ORDER BY started_at DESC
"""))
PY
```

## Portability

The only host-specific values are the three absolute bind mounts in
`compose.yaml`. When storage moves to a NAS, stop the stack, copy the PostgreSQL
and warehouse trees while stopped, update those paths, and start it again. For a
multi-user university deployment, replace localhost RustFS and PostgreSQL with
managed/shared services, external identity, TLS, backups, and monitored Iceberg
maintenance; pipeline contracts and table names can remain unchanged.

