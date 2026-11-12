# Research data platform

A local, persistent Iceberg lakehouse used to learn and apply enterprise data
management practices. Table objects stay under your configured data root;
Windows catalog storage uses a Linux-backed runtime volume with separate backup
requirements.

See [`ROADMAP.md`](ROADMAP.md) for the portability and packaging milestones and
[`docs/roadmap/`](docs/roadmap/) for their implementation plans.

## Architecture

- **Polaris 1.7.0**: Iceberg REST catalog and authorization boundary.
- **PostgreSQL 17**: persistent Polaris metastore.
- **RustFS**: local S3-compatible object storage, bind-mounted at
  `${DATA_ROOT}/DataLake/warehouse`.
- **DuckDB**: ingestion, validation, querying, and the operational control DB at
  `${DATA_ROOT}/Databases/duckdb/control.duckdb`.

The physical data-lake layout is intentionally small:

```text
${DATA_ROOT}/DataLake/
  landing/       # Immutable source deliveries and provenance records
  warehouse/     # RustFS/Iceberg-managed objects and table metadata
```

Bronze, silver, and gold are **catalog namespaces**, not additional directories
to create or populate. Landing preserves the delivery as received; bronze is
its first catalog-managed table representation. Silver and gold are refined
tables and products, addressed through the catalog in the same object store.
Iceberg manages table/snapshot metadata, not just labels on individual files.

Never manually edit anything in `warehouse`; the object store, Iceberg, and
catalog own that tree. Existing `raw`, `Archive`, or physical tier directories
are legacy/source locations: initialization leaves them untouched and existing
contracts can still read them. They are not part of the layout generated for
new installations. See [storage boundaries](docs/architecture.md#physical-storage-and-logical-namespaces).

## First-time setup

Install the Python environment and package first. Docker or Podman must already
be available to your user. The environment bundles `podman-compose` for the
Docker-free Podman path; Docker deployments still require Docker Compose.

```bash
mamba env create -f environment.yml
mamba activate data-platform
python -m pip install --no-deps --no-build-isolation --editable .
lake platform init --data-root "$HOME/Data" --backend docker
lake platform doctor
lake platform up
```

`init` creates private `.env` configuration and directories under your selected
storage parent. `up` starts services and bootstraps a fresh instance, with
checkpoints for safe retries. Repeating `init` preserves credentials; subsequent
`up` runs skip completed bootstrap steps. Normal operation is:

```bash
lake platform up
lake platform status
lake platform down
```

See [platform setup](docs/platform.md) for native Windows/Podman commands,
external-drive configuration, alternate ports, legacy-instance startup, retry
records, and validation limitations. Linux/Docker was tested with a fresh
isolated stack. Windows/Podman services and catalog queries were verified with
a manual SSH tunnel; automated forwarding, the bundled provider and the work
external-drive deployment still need host validation.

Default endpoints are bound to localhost:

- Polaris REST catalog: `http://localhost:8181/api/catalog`
- Polaris health: `http://localhost:8182/q/health`
- RustFS API: `http://localhost:9000`
- RustFS console: `http://localhost:9001`

## Install the platform CLI

```bash
mamba env update -n data-platform -f environment.yml --prune
./scripts/install-cli
```

The environment contains matched DuckDB 1.5.5 CLI and Python packages. The
editable package install makes `lake` available in the active environment while
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

`lake query` opens DuckDB with the Polaris catalog attached and selects the
`bronze` namespace by default. Set `DATA_PLATFORM_DUCKDB` to select a DuckDB
executable, otherwise it uses `duckdb` from `PATH`. Its temporary credential
file is mode `0600` on POSIX and removed after exit or interruption; Windows uses
temporary-directory ACLs. Catalog attachment and empty-table listing were
verified on native Windows with the manual SSH tunnel; table-object operations
and automated forwarding still need host validation. Forced termination can
leave the startup file behind.

```bash
lake query
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
lake query -- -c "SELECT count(*) FROM usaspending_assistance;"
```

On Linux/WSL, `scripts/lakehouse` remains an optional Bash wrapper around
`python -m lake_cli query`. On native Windows, use `lake query` or the Python
module entry point directly. The installer does not create user-bin symlinks.

## Portability

The host storage root is the `DATA_ROOT` value in the untracked `.env`. When
storage moves to a NAS, stop the stack, copy the PostgreSQL and warehouse trees
while stopped, update `DATA_ROOT`, and start it again. For a
multi-user university deployment, replace localhost RustFS and PostgreSQL with
managed/shared services, external identity, TLS, backups, and monitored Iceberg
maintenance; pipeline contracts and table names can remain unchanged.

## License

Copyright 2026 Kent Barnes.

Licensed under the [Apache License, Version 2.0](LICENSE).
See [NOTICE](NOTICE) for attribution information. Third-party dependencies and
managed datasets retain their respective licenses and access restrictions.
