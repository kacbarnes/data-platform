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
   containing `pyproject.toml` with `[project].name = "research-lake-cli".
   Dataset contract storage is not a project-discovery marker.
4. The package's checkout root (two directory levels above the package directory),
   if its `pyproject.toml` identifies the same platform package.

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
instead of the Bash wrapper. The optional `scripts/lakehouse` wrapper likewise
invokes the Python CLI's `query` command. `scripts/init-platform` now delegates
to configuration-only `lake platform init`; run `lake platform up` separately.

### Installing into a user-owned environment

On Linux/WSL, run `bash scripts/install-cli` from the checkout root. The script
selects `DATA_PLATFORM_PYTHON` or `python` from `PATH` and installs its own
checkout in editable mode. It does not use `DATA_PLATFORM_HOME` to select the
software to install. On native Windows, or without the Bash helper, run this
equivalent command from the checkout root in the intended Python environment:

```text
python -m pip install --no-deps --no-build-isolation --editable .
```

Dependencies, the Hatchling build backend, and its `editables` helper must
already be installed;
`environment.yml` supplies them, including matched DuckDB 1.5.5 binding and CLI
via its pip subsection (the conda CLI pin is unavailable on Windows).
A user-owned environment does not require
administrator privileges. Package installation generates the `lake` entry point
inside that environment. The installer no longer creates or replaces
`~/.local/bin/lake`; any existing symlink is left untouched. Installation does
not initialize services, create a data root, or migrate stored data.

## Platform configuration and services

```bash
lake platform init --data-root /path/to/storage --backend docker
lake platform doctor
lake platform up
lake platform status
lake platform logs --tail 100
lake platform down
```

Choose `--backend podman` for Podman. `init` creates private configuration and
directories without starting containers; existing credentials are preserved.
`--postgres-storage bind|volume` selects database storage. Fresh Windows defaults
to a Linux-backed runtime volume; Linux defaults to a bind mount. Existing
configuration is not silently migrated. Windows bind mode fails diagnostics and
startup rather than accepting host writability as sufficient. Volume mode needs
a separate catalog backup; copying `DATA_ROOT` alone is incomplete.
`up` bootstraps fresh storage and records completed steps so normal startup
does not repeat bootstrap. `down` preserves data. Global `--json` provides a
JSON result/error envelope. For legacy state without lifecycle checkpoints,
`up --existing` starts services without bootstrap using the original credentials.
Container output is decoded as UTF-8, replacing malformed bytes, independent
of the host's default encoding. This keeps Windows log diagnostics readable;
configured secrets remain redacted from successful and failed command output.

For user-scoped Podman/Windows installations with broken native port forwarding,
`init --podman-forwarding ssh --podman-machine podman-machine-default` enables
managed localhost SSH forwarding. Existing instances can set `PODMAN_FORWARDING`
and `PODMAN_MACHINE` in `.env` after shutdown. `up` starts/reuses the background
helper; `down` stops its owned SSH child; `status` reports forwarding status/log
location. `doctor` checks executable and matching machine prerequisites without
starting a tunnel. Native forwarding remains the default. See platform setup
for first-use host-key trust, port collisions and stale-state recovery.
Forwarding logs capture Python-helper startup errors as well as SSH output;
`status` includes recorded helper errors and SSH exit codes when available.

`environment.yml` bundles `podman-compose==1.6.0`; pip-only installations can
use `.[podman]`. Fresh Podman configuration explicitly selects that provider.
`PODMAN_COMPOSE_PROVIDER` overrides its executable name/path. Stop an existing
stack with its original provider before switching, since provider-specific
container names differ. Docker is not required for the Podman deployment.

Use `--project-root` before `platform` to select a checkout from outside it.
Storage and deployment configuration are independent of the Python interpreter.
See [platform setup](platform.md) for Windows commands, port selection,
permissions, retry behavior, diagnostics, and testing evidence.

## Dataset contracts

Actual source and transform contracts live outside the tool checkout. The
`CONTRACTS_DIR` setting defaults to `<resolved DATA_ROOT>/contracts`, alongside
`DataLake`, and follows environment-over-`.env` precedence. An empty value is an
error. Explicit relative paths resolve against the selected project root;
absolute paths, spaces, and `~` are supported. Configuration loading never
creates directories. `lake platform init --contracts-dir /private/contracts`
creates the selected directory and records it in private configuration. Existing
configuration without this setting uses the default.

An existing empty directory lists no datasets. A missing directory fails with
an actionable error rather than silently searching the checkout. New clones
contain no contract directory or actual contracts. The examples below use
fictional datasets and are safe to adapt locally. Run records retain the
contract checksum and platform code commit without publishing the YAML.

For an existing installation, move local YAML files from the old checkout's
`contracts/` into the resolved external directory, preserving relative subfolders
and checking for filename collisions before moving anything. Keep private
versions and backups under the data owner's controls. Re-running initialization
creates a missing configured directory but never migrates files or changes
credentials. An explicitly different directory on repeated initialization is
rejected; update `CONTRACTS_DIR` deliberately when relocating contracts.

Moving a contract does not change how its `source.path` resolves, and relative
`transform.sql` paths still resolve against the selected code checkout. The
managed Iceberg warehouse cannot be selected as contract storage.

Source contract:

```yaml
dataset_id: agency.awards
kind: source
owner: research-group
description: Award records received from an external agency.
source:
  format: parquet
  path: /data/DataLake/landing/agency/awards.parquet
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

The source path identifies an intake file; `target.namespace: bronze` identifies
its catalog destination, not a disk directory. New deliveries belong under
`DATA_ROOT/DataLake/landing`. Existing contracts referencing legacy source paths
remain supported and are not rewritten by initialization. Ingestion does not
require or create a user-managed `DataLake/bronze` directory.

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
lake query -- -c "SELECT count(*) FROM usaspending_assistance;"
python -m lake_cli --project-root /path/to/platform query
```

This starts the DuckDB CLI directly from Python with Polaris attached and the
`bronze` namespace selected. It works without Bash on native Windows and Linux;
the package and a compatible DuckDB CLI must be installed in the selected
environment. Linux execution is tested; native Windows execution still needs
host validation. The Python DuckDB binding alone does not provide the CLI.

Set `DATA_PLATFORM_DUCKDB` to an executable path or command name to override
discovery; otherwise `duckdb` is found on `PATH`. Paths containing spaces are
passed as individual subprocess arguments. An invalid explicit override fails
instead of falling back silently. Put DuckDB options after `query --`.

Windows sessions default to ASCII-bordered `table` output to avoid garbled
Unicode borders. Other hosts retain `duckbox` output. Select another mode in
the prompt with `.mode`, or pass a DuckDB output option such as `-csv`.

The query client uses the same settings and catalog setup statements as Python
ingestion, including configured Polaris URL, S3 endpoint and region. It no
longer checks a fixed localhost health URL or sources `.env` as shell code.
Connection and initialization errors come from DuckDB. Startup errors stop the
requested query, while an interactive SQL error does not end the session.

Credentials are written to a temporary startup file, closed before the child
process opens it for Windows compatibility. The file uses mode `0600` on POSIX;
Windows protection follows temporary-directory ACLs. It is deleted after normal
exit, launch failure, or Ctrl+C; forcibly killing the parent or a system crash
can leave it behind. Setup output and command echo are disabled. Credentials
are not put in process arguments. Child exit codes are preserved, and Ctrl+C
returns 130. Query SQL is an interactive analysis interface; use tracked ingest
and build commands for publication rather than manual catalog mutations.

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
