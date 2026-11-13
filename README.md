# Research data platform

A local research data platform for publishing, querying, and tracing datasets
with Apache Iceberg. The `lake` CLI combines contract-driven ingestion, SQL
transformations, quality checks, and operational provenance with persistent
catalog and object storage.

The current deployment targets single-user local workflows with Docker or
Podman. The project is under active development; shared multi-user hosting and
automated storage migration are not yet implemented.

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

The object store, Iceberg, and catalog own the warehouse layout. Publish changes
through the platform client rather than editing table objects directly.
Initialization preserves existing source directories and does not automatically
register their contents. See [storage boundaries](docs/architecture.md#physical-storage-and-logical-namespaces).

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
storage configuration, alternate ports, existing-instance startup, and retry
behavior. Linux/Docker has passed fresh-instance lifecycle and catalog checks.
Native Windows/Podman has passed managed OpenSSH forwarding, service health,
read-only catalog access, S3 listing, tunnel reuse, and shutdown/restart checks.
Windows table-object reads/writes and external-drive storage remain unqualified.
Detailed verification evidence and remaining limitations are recorded in the
[implementation notes](docs/implementation.md).

Default endpoints are bound to localhost:

- Polaris REST catalog: `http://localhost:8181/api/catalog`
- Polaris health: `http://localhost:8182/q/health`
- RustFS API: `http://localhost:9000`
- RustFS console: `http://localhost:9001`

## Install the platform CLI

```bash
mamba env update -n data-platform -f environment.yml --prune
python -m pip install --no-deps --no-build-isolation --editable .
```

The environment contains matched DuckDB 1.5.5 CLI and Python packages. The
editable package install makes `lake` available in the active environment while
keeping implementation changes under Git.

This localhost deployment marks RustFS STS as unavailable in Polaris and gives
DuckDB the private static RustFS credentials from `.env`. This is appropriate
for the single-user local environment and avoids depending on temporary
credential vending support in prerelease RustFS builds.

## Contract-driven workflow

Dataset contracts live outside the tool checkout, by default under
`${DATA_ROOT}/contracts` alongside `DataLake`. Set `CONTRACTS_DIR` in your private
`.env` or environment to select another directory, or use
`lake platform init --contracts-dir /path/to/contracts`. Initialization
creates the selected directory. Create local YAML contracts using the synthetic
examples in [the CLI guide](docs/cli.md#dataset-contracts) before running the
workflow below. Dataset-specific contract definitions are managed separately
from the platform source.

```bash
lake datasets
lake ingest agency.awards
lake test agency.awards
lake status agency.awards
lake lineage agency.awards
```

`lake ingest` reads the source location, catalog target, update policy, and
quality expectations from `agency_awards.yml` in the configured contract
directory. A source checksum already recorded as successful is skipped
automatically. Catalog metadata, snapshot history, run status, quality results,
and Git provenance are
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
executable, otherwise it uses `duckdb` from `PATH`. Windows sessions default to
ASCII table borders; other hosts use DuckDB's `duckbox` rendering.

The client initializes credentials through a temporary startup file and removes
it after normal exit or interruption. POSIX permissions are `0600`; Windows
uses temporary-directory ACLs. Forced termination or a crash can leave the file
behind. See the [query client reference](docs/cli.md#query) for details.

```bash
lake query
```

Inside the prompt:

```sql
SHOW ALL TABLES;
SELECT count(*) FROM agency_awards;
SELECT * FROM agency_awards LIMIT 10;
SELECT *
FROM iceberg_snapshots('lakehouse.bronze.agency_awards')
ORDER BY timestamp_ms DESC;
```

For a single non-interactive query:

```bash
lake query -- -c "SELECT count(*) FROM agency_awards;"
```

On Linux/WSL, `scripts/lakehouse` remains an optional Bash wrapper around
`python -m lake_cli query`. On native Windows, use `lake query` or the Python
module entry point directly. The installer does not create user-bin symlinks.

## Storage and shared deployments

`DATA_ROOT` selects the storage parent, and `CONTRACTS_DIR` can independently
select the contract directory. PostgreSQL defaults to bind storage on Linux and
a Linux-backed runtime volume on native Windows. In volume mode, catalog state
lives outside `DATA_ROOT`, so a copy of that directory alone is not a complete
backup. See [catalog backups](docs/platform.md#catalog-backups-in-volume-mode).

Storage relocation requires a deliberate migration of catalog state, table
objects, configuration, and operational records. Changing `DATA_ROOT` is not a
migration: lifecycle checkpoints guard the installation's storage identity.
Automated migration and restore are not currently provided.

Shared hosting requires additional deployment design for identity and access,
networking and TLS, concurrent clients, coordinated backups, and Iceberg
maintenance. The current local setup is a foundation for that work; a tested
multi-user deployment is not yet part of the project.

## License

Copyright 2026 Kent Barnes.

Licensed under the [Apache License, Version 2.0](LICENSE).
See [NOTICE](NOTICE) for attribution information. Third-party dependencies and
managed datasets retain their respective licenses and access restrictions.

## Acknowledgements

This platform builds on DuckDB, Apache Polaris, Apache Iceberg, PostgreSQL,
RustFS, and the Python ecosystem. See [third-party software acknowledgements](THIRD_PARTY_NOTICES.md)
for contributor credits, upstream licenses, component roles, and research
citation guidance.
