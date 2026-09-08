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

## Install the platform CLI

```bash
cd /home/kent/Projects/data-platform
mamba env update -n data-platform -f environment.yml --prune
./scripts/install-cli
```

The environment contains matched DuckDB 1.5.5 CLI and Python packages. The
editable package install makes `lake` available through `~/.local/bin` while
keeping implementation changes under Git.

This localhost deployment marks RustFS STS as unavailable in Polaris and gives
DuckDB the private static RustFS credentials from `.env`. This is appropriate
for the single-user local environment and avoids depending on temporary
credential vending support in prerelease RustFS builds.

## Contract-driven workflow

```bash
lake datasets
lake ingest usaspending.assistance
lake test usaspending.assistance
lake status usaspending.assistance
lake lineage usaspending.assistance
```

`lake ingest` reads the source location, catalog target, update policy, and
quality expectations from `contracts/usaspending_assistance.yml`. A source
checksum already recorded as successful is skipped automatically. Catalog
metadata, snapshot history, run status, quality results, and Git provenance are
updated without hand editing.

Silver and gold products use transform contracts:

```bash
lake build <dataset-id>
```

See `docs/cli.md` for contract schemas, update strategies, recovery behavior,
and the complete command reference.

Inspect the audit trail:

```bash
lake status --limit 20
```

## Query CLI

The `lakehouse` launcher opens DuckDB with the Polaris catalog already attached
and selects the `bronze` namespace by default. Its temporary credential-bearing
startup file is mode `0600` and is removed when DuckDB exits.

```bash
lakehouse
```

Inside the prompt:

```sql
SHOW ALL TABLES;
SELECT count(*) FROM usaspending_assistance;
SELECT * FROM usaspending_assistance LIMIT 10;
SELECT *
FROM iceberg_snapshots('lakehouse.bronze.usaspending_assistance')
ORDER BY timestamp_ms DESC;
```

For a single non-interactive query:

```bash
lakehouse -c "SELECT count(*) FROM usaspending_assistance;"
```

The project copy lives at `scripts/lakehouse`; the installed command is a
symlink in `~/.local/bin`, so improvements to the version-controlled launcher
take effect immediately.

## Portability

The host storage root is the `DATA_ROOT` value in the untracked `.env`. When
storage moves to a NAS, stop the stack, copy the PostgreSQL and warehouse trees
while stopped, update `DATA_ROOT`, and start it again. For a
multi-user university deployment, replace localhost RustFS and PostgreSQL with
managed/shared services, external identity, TLS, backups, and monitored Iceberg
maintenance; pipeline contracts and table names can remain unchanged.
