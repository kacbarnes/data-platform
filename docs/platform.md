# Local platform setup and lifecycle

The Python CLI initializes configuration and operates the same Compose definition
with Docker or Podman. Install the package and dependencies in a user-owned Python
environment first. Service commands require a checkout containing `compose.yaml`
and `scripts/bootstrap_catalog.py`; these deployment assets are not embedded in
the Python wheel. Use `--project-root` before `platform`, or set
`DATA_PLATFORM_HOME`, to select that checkout from any working directory.

Create the environment with `mamba env create -f environment.yml`, then activate
`data-platform`. The file installs Python and general dependencies from
conda-forge, and both DuckDB 1.5.5 components from official PyPI wheels through
its pip subsection. The pinned conda-forge CLI release has no Windows build;
the PyPI CLI has Windows x64 and ARM64 wheels. Do not replace the pin with an
older CLI just to satisfy the conda solver. On Windows 11 ARM, x64 Miniforge is
a practical option for an environment matching the x86_64 work laptop.

## Fresh Linux/Docker instance

From the checkout, with your Python environment active and Docker available:

```bash
python -m pip install --no-deps --no-build-isolation --editable .
lake platform init --data-root "$HOME/Data" --backend docker
lake platform doctor
lake platform up
lake platform status
lake query
```

`init` creates `.env` and directories only; `up` starts the services and performs
first-time bootstrap. No command installs or enables the container runtime.
Docker must already be accessible to your user. The first startup may take
several minutes while public images are pulled.

## Fresh Windows/Podman instance

Use a Python environment with the dependencies in `environment.yml`, plus Podman
and a Compose provider. Podman on Windows uses a Linux VM; the external drive
and checkout must be accessible to that VM and provider. Unlock and attach the
drive before initialization. A Windows drive path is different from its WSL
mount path; use the path appropriate to the environment running the CLI.

From a native PowerShell session in the checkout:

```powershell
python -m pip install --no-deps --no-build-isolation --editable .
podman machine start
podman compose version
python -m lake_cli platform init --data-root "E:/ResearchData" --backend podman
python -m lake_cli platform doctor
python -m lake_cli platform up
python -m lake_cli platform status
python -m lake_cli query
```

An existing Podman machine is a prerequisite for the `machine start` example;
follow your organization's runtime setup if it has not been created. Podman's
Compose command delegates to an installed provider. The definition requires
profiles, health dependencies, completed one-shot dependencies, long-form bind
mounts, and `config --quiet`. Docker Compose is the validated provider here.
Podman command generation is tested, but a live Podman/Windows deployment still
needs validation on the laptop. Do not infer Windows mount compatibility from
Linux tests. External-drive sharing, NTFS behavior, and host/VM permissions are
part of that validation.

## Configuration and storage

`DATA_PLATFORM_HOME` selects software/configuration; `DATA_ROOT` selects the
storage parent. Relative storage paths resolve against the checkout; `~` expands
to the user's home, and symlinks are resolved. Initialization defaults to the
explicit `--data-root`, then environment `DATA_ROOT`, then `~/Data`.

Directories are created beneath that parent:

```text
DataLake/landing/
DataLake/warehouse/            # Iceberg/object-store owned
Databases/postgres/polaris/    # Polaris metastore only in bind mode
Databases/duckdb/              # operational control database
.data-platform/               # lifecycle journal and checkpoints
```

PostgreSQL storage is selected with `init --postgres-storage bind|volume` or
`POSTGRES_STORAGE`. Fresh native Windows installations default to `volume`;
Linux defaults to `bind`. Existing `.env` files without this setting retain
`bind`. Volume mode stores the catalog in the runtime's Linux-backed named
volume `<project-name>_postgres-data`, not under `DATA_ROOT`. `down` preserves
this volume; never use `compose down --volumes` or prune it. Changing runtime,
project name, or deleting the runtime VM can disconnect or destroy that catalog.
Moving to another NTFS drive does not reliably provide PostgreSQL's required
POSIX ownership and permissions. Native Windows bind mode is rejected by `up`
and flagged by `doctor`. WSL/Linux bind mounts still require a suitable filesystem.

### Recovering the earlier Windows permission failure

Stop the old services with the original configuration first:

```powershell
python -m lake_cli platform down
```

In the checkout's existing `.env`, add `POSTGRES_STORAGE='volume'`. Preserve
all credentials, paths, project name and lifecycle records; do not rerun fresh
initialization or delete files. Then run `platform doctor` and `platform up`.
The CLI derives the mount type/source from this setting. Recovery is permitted
only for an older checkpoint with no completed bootstrap steps and an empty
old PostgreSQL directory, and only if the target volume does not already exist.
If these guards reject recovery, an explicit reviewed migration is required.
Do not bypass them by deleting the checkpoint. Direct Compose users must also
set `POSTGRES_STORAGE_TYPE='volume'` and `POSTGRES_STORAGE_SOURCE='postgres-data'`;
new initialization writes all three settings consistently.

### Catalog backups in volume mode

A copy of `DATA_ROOT` alone is **not a complete lakehouse backup**. Preserve the
PostgreSQL catalog too. With services running, these PowerShell commands create
a custom-format database dump without exposing credentials or piping binary
output through PowerShell:

```powershell
# Substitute your actual project name from platform status.
$pgContainer = 'research-lake-75824576d1-postgres-1'
podman exec $pgContainer sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/polaris-backup.dump'
podman cp "${pgContainer}:/tmp/polaris-backup.dump" ./polaris-backup.dump
```

Check each command's exit code. Store the dump securely with the original private
configuration and a consistent warehouse backup. Quiesce ingestion/publication
during coordinated backups; a database dump alone contains no table objects.
Restore requires a deliberate `pg_restore` procedure into the intended database;
do not run fresh bootstrap over a restored catalog. This increment does not
automate backup/restore or migration.

Credentials are generated with Python's cryptographic random generator and
never printed. `.env` is created exclusively, with mode `0600` on POSIX; Windows
uses host ACLs. Existing configuration is preserved. A different root, backend,
project name, or explicitly supplied port on repeat `init` is rejected rather
than silently rewriting configuration. Initialization refuses a fresh credential
set when the selected metastore or warehouse already contains state.

`PLATFORM_BACKEND` selects `docker` or `podman`. Fresh instances get a Compose
project name derived from the resolved storage path. Existing configurations
without a project name retain `research-data-platform`. Docker on Linux uses
the caller's UID/GID for RustFS; Podman defaults to container UID/GID `0:0`, which
maps to the invoking user under rootless Podman. Existing ownership overrides
are preserved. Rootful Podman, SELinux, and VM-shared drives may require explicit
ownership or mount configuration; `doctor` does not establish those permissions.

Endpoints bind to loopback. Defaults are Polaris `8181`, health `8182`, RustFS
`9000`, and its console `9001`. For an additional instance, choose free ports:

```bash
lake platform init --data-root /path/to/new/storage --backend docker \
  --polaris-port 18181 --polaris-health-port 18182 \
  --rustfs-port 19000 --rustfs-console-port 19001
```

Generated `POLARIS_URL` and `RUSTFS_CLIENT_ENDPOINT` match those ports. Later
manual port edits must also update the corresponding client endpoint values.
Configuration paths containing apostrophes, dollars, or newlines are currently
rejected by initialization to preserve consistent Python/Compose parsing.

## Startup, retry, shutdown, and diagnostics

```bash
lake platform up --timeout 240
lake platform status
lake platform logs --tail 100
lake --json platform doctor
lake platform down
```

First startup validates Compose, checks free ports, starts storage services,
bootstraps the Polaris realm, starts Polaris, creates catalog privileges and
logical bronze/silver/gold namespaces, and waits for service health. Successful
realm and catalog steps are checkpointed. Normal repeated startup skips them;
failed bootstrap resumes incomplete steps. Polaris 1.7 realm bootstrap and the
catalog script support retry after interruption. Credentials are not rotated.

Each `up`/`down` attempt gets a JSON operational journal under `.data-platform`,
including status, errors, timestamps, step progress, and deployment code hashes.
This is the authority for platform operations; dataset publication remains
tracked separately in the DuckDB control database. Journal errors and container
logs redact configured secrets. A lock prevents concurrent lifecycle operations.
After a forced process termination, verify that no operation is running before
manually removing a stale `operation.lock`.

`down` removes only the selected project's containers and network. It does not
request volume deletion or erase storage. Failed startup leaves state available
for diagnosis and retry. `doctor` checks configuration, available storage,
write-access indicators, port occupancy, Compose parsing, and runtime access;
it does not change configuration or bootstrap services. It cannot fully predict
container-side write permissions, drive sharing, or image-pull authorization.
Both Python and Compose receive the same resolved storage settings. Unrelated
shell Compose file/profile overrides are excluded by the lifecycle runner.
Legacy `.env` files without `DATA_ROOT` retain the Python default `~/Data`;
the CLI supplies its absolute value to Compose. Direct Compose invocation
requires `DATA_ROOT` in `.env` or the shell because the definition has no
machine-specific fallback.

For an existing instance initialized by the old scripts, preserve its original
`.env` and use `lake platform up --existing`. This starts services without
bootstrap and does not invent a checkpoint for an unverified existing catalog.
Subsequent starts of that legacy instance should continue using `--existing`.

Changing `DATA_ROOT` is not migration. Preserve the original configuration,
metastore, warehouse, and lifecycle records together. Checkpoints bind bootstrap
to its storage/configuration identity; mismatched credentials or project names
are rejected. This increment does not implement migration, credential rotation,
Apptainer deployment, or container-runtime installation.

## Verification performed

Linux/Docker was exercised with a separate temporary project, storage root, and
alternate ports: fresh startup, authentication, catalog and namespace checks,
DuckDB attachment, repeated startup, and shutdown preserving state all passed.
The test did not modify the desktop's existing lakehouse. Docker's local
credential helper initially timed out; the retry used an empty temporary Docker
configuration for public image pulls, without changing the user's configuration.
Test state was preserved at `/tmp/data-platform-smoke-3wsaz0TS/storage`.

The PostgreSQL volume correction was separately exercised on Linux/Docker:
Compose parsing passed for both storage modes; a real PostgreSQL 17 named-volume
instance accepted read-only queries before and after restart, and shutdown
preserved its volume. Test state remains at
`/tmp/lake-pg-volume-smoke-_gjhuhyz`; no existing lakehouse was modified.
Native Windows/Podman filesystem behavior still needs laptop verification.

Official runtime references: [Podman Compose](https://docs.podman.io/en/stable/markdown/podman-compose.1.html),
[Podman machine](https://docs.podman.io/en/stable/markdown/podman-machine.1.html),
[Compose interpolation](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/),
and [Polaris 1.7 bootstrap behavior](https://polaris.incubator.apache.org/downloads/1.7.0/).
