# Implementation record

## Current platform

- Apache Polaris 1.7.0 with PostgreSQL persistence
- RustFS S3-compatible object storage using a host bind mount
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

