# `lake` CLI

The workflow is contract-first:

```text
lake datasets
lake ingest <dataset-id>
lake build <dataset-id>
lake test <dataset-id>
lake status [<dataset-id>]
lake lineage <dataset-id>
lake query
```

Commands obtain local platform configuration from the repository `.env`. They
must not require callers to manually register a table, update a catalog pointer,
or write operational metadata.

## Dataset contracts

Source contract:

```yaml
dataset_id: agency.awards
kind: source
owner: research-group
description: Award records received from an external agency.
source:
  format: parquet
  path: /data/raw/agency/awards.parquet
target:
  catalog: research
  namespace: bronze
  table: agency_awards
update:
  strategy: append
quality:
  minimum_rows: 1
  required_columns: [award_id, amount]
  not_null: [award_id]
  unique: [award_id]
metadata:
  external_run_id: upstream-pipeline-run-identifier
  source_archive_sha256: immutable-upstream-archive-digest
```

Transform contract:

```yaml
dataset_id: agency.awards_clean
kind: transform
owner: research-group
description: Validated and typed award records.
inputs: [agency.awards]
transform:
  sql: transforms/agency_awards_clean.sql
target:
  catalog: research
  namespace: silver
  table: agency_awards_clean
update:
  strategy: replace
quality:
  minimum_rows: 1
  not_null: [award_id]
  unique: [award_id]
```

Supported source formats are Parquet, CSV, JSON, and newline-delimited JSON.
The first release accepts one local source artifact per ingest run.

The optional `metadata` mapping carries searchable upstream provenance and
semantic flags. Successful ingests copy it unchanged into
`pipeline_runs.metrics_json.contract_metadata`; the contract SHA-256 still
identifies the complete reviewed YAML.

## Update strategies

- `create`: create once and fail safely if the table already exists.
- `append`: add a new source version. A previously successful source checksum
  is skipped automatically unless `--force` is supplied.
- `merge`: update and insert by the contract's `primary_key`.
- `replace`: rebuild table contents and requires the explicit
  `--full-refresh` flag.

No routine command drops or purges a catalog table.

## Commands

### Discover datasets

```bash
lake datasets
lake --json datasets
```

All contracts are parsed and validated before output. Duplicate IDs, invalid
identifiers, missing transform SQL, and invalid update policies fail here.

### Ingest bronze

```bash
lake ingest usaspending.assistance
lake ingest agency.awards --source /new/delivery/awards.parquet
```

The CLI hashes and profiles the source, executes quality checks before the
catalog write, publishes through Polaris, captures the new Iceberg snapshot,
and records the run. Recovery of an already-created table is explicit:

```bash
lake ingest agency.awards --adopt-existing
```

Adoption requires source and target row counts to match.

### Build silver or gold

```bash
lake build agency.awards_clean --full-refresh
```

Input snapshot IDs are captured before transformation. Quality checks execute
against the transformation query before publication. Successful builds record
the exact input-to-output snapshot edges.

### Test, status, and lineage

```bash
lake test agency.awards_clean
lake status
lake status agency.awards_clean --limit 10
lake lineage agency.awards_clean
```

`status` and `lineage` read the local control database. Add global `--json` for
machine-readable output.

### Query

```bash
lake query
```

This delegates to the `lakehouse` DuckDB client with Polaris already attached.

## Automatic tracking

Every mutating command records a run before attempting the catalog operation.
The control database captures:

- command and dataset ID;
- contract checksum and pipeline Git commit;
- start, completion, status, and error;
- source checksum, size, schema, and row count;
- output snapshot and row count;
- input/output snapshot lineage;
- every contracted quality result.

Failed runs remain in history. They are evidence, not clutter.
