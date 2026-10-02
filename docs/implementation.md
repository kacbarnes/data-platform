# Implementation record

## Current platform

- Apache Polaris 1.7.0 with PostgreSQL persistence
- RustFS S3-compatible object storage using a host bind mount
- Direct local RustFS credentials for DuckDB, with Polaris configured
  `stsUnavailable: true`; credentials remain in the untracked private `.env`
- RustFS runs with the host UID/GID (default `1000:1000`) so its bind-mounted
  warehouse remains writable without broadening filesystem permissions
- DuckDB 1.5.5 Python binding and CLI
- `research.bronze.usaspending_assistance` as the first tracked Iceberg table
- `lakehouse` as the attached interactive SQL client
- Legacy `ingestion_runs` audit table in `control.duckdb`

## Implemented CLI increment

The one-dataset script is replaced by an installable `lake` package. The first
supported command set is:

- `lake datasets`: discover and validate contracts
- `lake ingest`: load a contracted source into bronze
- `lake build`: publish a SQL transformation to silver or gold
- `lake test`: execute contracted quality checks
- `lake status`: inspect operational runs without connecting to Polaris
- `lake lineage`: display snapshot-level input/output lineage
- `lake query`: open the existing interactive DuckDB client

The CLI will preserve the existing `ingestion_runs` table and introduce generic
run, version, lineage, and quality tables through migration `002`.

Implementation modules separate configuration, contracts, catalog access,
control metadata, quality checks, orchestration, and presentation. The old
`scripts/ingest_iceberg.py` remains as a compatibility wrapper and refuses
catalog targets that disagree with the contract.

## v0.2 progress: configuration discovery

The relocatable-core milestone is in progress, not complete. Implemented:

- Centralized project discovery in `discover_project_root()`, called by
  `Settings.load()`: explicit root, environment override, working-directory
  ancestor search, then package checkout fallback.
- Injectable environment mappings shared by discovery and settings loading;
  an empty mapping isolates callers from process environment variables.
- Consistent environment, `.env`, and default precedence for `CONTROL_DB`.
- Shared `resolve_config_path()` helper: relative `DATA_ROOT` and configured
  `CONTROL_DB` values resolve against the discovered project root; absolute
  paths and home expansion are preserved. The default control database remains
  beneath the resolved `DATA_ROOT`.
- Temporary-file tests for discovery precedence, parent search, missing-root
  failure, environment isolation, configuration overrides, and defaults, plus
  working-directory independence, paths containing spaces, and home expansion.
- `python -m lake_cli` delegates to the existing CLI and propagates its exit
  code. A subprocess test checks help from outside the repository using the
  test environment's Python interpreter, without loading platform credentials.

- `scripts/lake` now uses `DATA_PLATFORM_PYTHON` or `python` from `PATH` to run
  the module entry point, without hard-coded executable or project paths.
  Linux/WSL subprocess tests verify help and invalid-argument exit codes from
  outside the repository. These Bash-specific tests are skipped on Windows.
- `scripts/install-cli` now selects `DATA_PLATFORM_PYTHON` or `python` from
  `PATH`, installs its own checkout into that environment, and no longer creates
  a user-bin symlink. Existing symlinks are left untouched. The equivalent pip
  command is documented for native Windows. Isolated installer tests use a fake
  interpreter to check argument boundaries, paths containing spaces, successful
  and failed exit codes, and absence of user-bin creation, without running pip.

- `lake query` now starts DuckDB directly through `query_client.py`; the
  optional `scripts/lakehouse` wrapper delegates to it. Executable discovery
  uses `DATA_PLATFORM_DUCKDB` or `PATH`. The client shares catalog setup SQL
  with ingestion and honors configured endpoints instead of a fixed health URL.
- Query-client tests cover escaped credentials, identifier validation, shared
  setup, discovery, argument forwarding, private POSIX file permissions, cleanup
  after success/failure/interrupt, and service delegation without a checkout
  script. Local DuckDB smoke tests verify output suppression and startup failure
  without connecting to the catalog or loading extensions.

Verification for this increment: all 41 tests pass on Linux, and
`git diff --check` is clean. No live catalog
verification is implied by these tests.
The implementer also confirmed successful installation with the updated script.

Remaining limitations: the selected project still requires `.env`, package
fallback assumes a source-checkout layout. This increment does not
complete launcher portability, diagnostics, or configuration initialization.

Native Windows query execution still needs host validation. Temporary file
protection on Windows uses the temporary directory's ACLs; force-killing the
parent or a crash can leave a startup file behind.

The next safe increment is portable configuration initialization with an
explicit storage root, followed by container lifecycle work. Initialization
remains a separate migration; neither service startup nor storage migration is
completed by the launcher and query-client changes.
See [the v0.2 plan](roadmap/v0.2-relocatable-core.md) for the remaining scope.

## Deliberate first-version limits

- File ingestion initially accepts one local Parquet, CSV, or JSON artifact per
  run. Multi-file discovery and remote acquisition are later increments.
- SQL is the first transformation language. Python asset execution can be added
  behind the same contract and run-tracking interfaces.
- Scheduling is external. The CLI remains deterministic and scheduler-agnostic
  so it can later be driven by systemd, Dagster, Airflow, or CI.
- Iceberg maintenance and destructive lifecycle operations are intentionally
  excluded from routine commands.
- Replace performs an explicit full refresh within a DuckDB transaction. Large
  incremental partition replacement is a later increment.
- Contract dependency ordering is explicit through `inputs`; recursive DAG
  execution is a later increment. A build operates on one requested asset.

## Next safe increments

1. Add a real silver USAspending contract after column-level profiling and
   domain review.
2. Add multi-file deliveries with a source-file manifest and watermark policy.
3. Add `lake plan` for dry-run dependency and mutation previews.
4. Add scheduler integration only after two or more repeatable pipelines exist.
