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

## Configuration and project discovery

`Settings.load()` selects the project root in this order:

1. The explicit global `--project-root` option.
2. A non-empty `DATA_PLATFORM_HOME` environment variable.
3. The current working directory, then its parents, selecting the first directory
   containing both `pyproject.toml` and a `contracts/` directory.
4. The package's checkout root (two directory levels above the package directory),
   if it contains `pyproject.toml`.

If discovery fails, the CLI asks for `--project-root` or `DATA_PLATFORM_HOME`.
Explicit and environment-provided roots are expanded and resolved; configuration
loading then requires a readable `.env` at the selected root. `DATA_PLATFORM_HOME`
must be supplied before loading that file, not solely inside it.

For example, select a checkout explicitly when calling from another directory:

```bash
lake --project-root /path/to/data-platform datasets
```

Configuration values use environment variables first, the selected project's
`.env` second, and defaults where defined. An empty selected value is an error,
not a request to fall back. `CONTROL_DB` follows this same precedence, including
support for a value in `.env`.

`DATA_ROOT` defaults to `~/Data`; `CONTROL_DB` defaults to
`<resolved DATA_ROOT>/Databases/duckdb/control.duckdb`. Both paths expand `~`.
Relative `DATA_ROOT` and explicitly configured `CONTROL_DB` values resolve
against the discovered project root, regardless of the process working
directory. Absolute paths are not prefixed with the project root. For example,
`DATA_ROOT="data files"` selects `<project root>/data files`, while
`CONTROL_DB="state/control.duckdb"` selects `<project root>/state/control.duckdb`,
not a path beneath `DATA_ROOT`. Omitting `CONTROL_DB` retains the default beneath
the resolved `DATA_ROOT`. Resolution does not create directories. These settings
do not change dataset source-path semantics.

For Python callers and tests, `Settings.load(..., environ=mapping)` uses that
mapping for both root discovery and configuration overrides. Omitting it (or
passing `None`) uses the process environment; passing `{}` ignores the process
environment but still loads the selected project's `.env`.

## Running with the active Python environment

With the package installed in the selected Python environment, `python -m
lake_cli` exposes the same commands as the installed `lake` entry point:

```bash
python -m lake_cli --help
python -m lake_cli --project-root /path/to/data-platform datasets
```

This uses whichever interpreter the shell resolves as `python`; activate the
intended environment first. It works outside the repository when the package
is installed, and uses the same project-discovery and configuration rules.
`--help` exits before configuration loading and requires no `.env` or running
lakehouse.

On Linux/WSL, `scripts/lake` is an optional Bash compatibility wrapper. It uses
`DATA_PLATFORM_PYTHON` when non-empty, otherwise `python` from `PATH`, and invokes
`python -m lake_cli` with the original arguments and exit status. The interpreter
override is a command name or executable path, not a command with extra flags.
`DATA_PLATFORM_LAKE` is no longer used. Project selection follows the Python
configuration rules above; the wrapper does not force its own checkout root.

On native Windows, use the installed `lake` command or `python -m lake_cli`
instead of the Bash wrapper. The interactive `lakehouse` and initialization
scripts still require portability work.

### Installing into a user-owned environment

On Linux/WSL, run `bash scripts/install-cli` from the checkout root. The script
selects `DATA_PLATFORM_PYTHON` or `python` from `PATH` and installs its own
checkout in editable mode. It does not use `DATA_PLATFORM_HOME` to select the
software to install. On native Windows, or without the Bash helper, run this
equivalent command from the checkout root in the intended Python environment:

```text
python -m pip install --no-deps --no-build-isolation --editable .
```

Dependencies and the Hatchling build backend must already be installed;
`environment.yml` supplies them. A user-owned environment does not require
administrator privileges. Package installation generates the `lake` entry point
inside that environment. The installer no longer creates or replaces
`~/.local/bin/lake`; any existing symlink is left untouched. Installation does
not initialize services, create a data root, or migrate stored data.

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
