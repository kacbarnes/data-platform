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

Use `environment.yml`, which bundles `podman-compose==1.6.0`, plus an installed
Podman runtime. Docker and Docker Desktop are not prerequisites for this path.
Pip-only installations can use `python -m pip install '.[podman]'`.
Podman on Windows uses a Linux VM; the external drive
and checkout must be accessible to that VM and provider. Unlock and attach the
drive before initialization. A Windows drive path is different from its WSL
mount path; use the path appropriate to the environment running the CLI.

From a native PowerShell session in the checkout:

```powershell
python -m pip install --no-deps --no-build-isolation --editable .
podman machine start
podman-compose --version
python -m lake_cli platform init --data-root "E:/ResearchData" --backend podman --podman-forwarding ssh
python -m lake_cli platform doctor
python -m lake_cli platform up
python -m lake_cli platform status
python -m lake_cli query
```

An existing Podman machine is a prerequisite for the `machine start` example;
follow your organization's runtime setup if it has not been created. Podman's
Compose command delegates to an installed provider. The definition requires
profiles, health dependencies, completed one-shot dependencies, long-form bind
mounts, and `config --quiet`. Fresh Podman initialization records
`PODMAN_COMPOSE_PROVIDER='podman-compose'`; the lifecycle runner resolves it in
the active environment and explicitly passes it to Podman. Override this in
`.env` or the shell with an executable name/full path when needed. Working
directory plus absolute Compose/env paths replace `--project-directory`, which
podman-compose does not support.

**Existing-provider changes:** stop services using the original provider before
changing this setting. Docker Compose and podman-compose use different container
names/labels; switching while PostgreSQL is running can start a second container
against the same database volume. Preserve the project name, credentials, volume
and checkpoints. Legacy configurations without an explicit provider retain
Docker-first discovery if available, otherwise use podman-compose. After stopping
the original stack, set `PODMAN_COMPOSE_PROVIDER='podman-compose'` for the Docker-free path.

The Windows ARM64 laptop successfully started services and queried the empty
catalog using Docker's provider plus a manual SSH tunnel. Managed forwarding is
implemented and locally tested, but automated Windows detachment, a full
podman-compose deployment, actual table-object reads/writes, and the work
laptop/external-drive configuration still need host qualification. Do not infer
these results from Linux tests.

## Configuration and storage

### Managed Podman SSH forwarding (user-scoped Windows)

If services are reachable within the Podman VM but not from Windows localhost,
enable the SSH path that was verified manually on the Windows ARM64 laptop.
No administrator installation, firewall change, port-proxy service, or separate
terminal is required. OpenSSH `ssh.exe` must already be on PATH and the Podman
machine must be running. Native forwarding remains the default; Linux/Docker
behavior is unchanged.

For a fresh checkout/configuration:

```powershell
python -m lake_cli platform init --data-root "E:/ResearchData" --backend podman --podman-forwarding ssh
python -m lake_cli platform up --timeout 240
```

For an existing configuration, run `platform down`, close the temporary manual
SSH tunnel with Ctrl+C, then add these settings to the existing `.env` without
changing credentials or storage:

```dotenv
PODMAN_FORWARDING='ssh'
PODMAN_MACHINE='podman-machine-default'
```

Thereafter, normal `platform up` starts/reuses a detached, user-owned tunnel and
waits for host-side health; `platform down` stops containers and asks the tunnel
supervisor to stop its own SSH child. All four configured host ports are bound
to `127.0.0.1` and forwarded to the same ports on the VM's loopback interface.
The Podman machine inspection provides the SSH port, username and identity path.
The active Podman connection must match that VM. Machine/runtime installation
and starting the VM are still prerequisites, not automatic platform operations.

The helper references the existing private key; it never copies key contents or
receives lakehouse credentials. Batch authentication and strict host-key checking
are enabled. On a new laptop, the first native OpenSSH connection may require a
one-time verified host-key acceptance; do not disable checking to bypass changed
keys. Use this only for the local machine you created:

```powershell
$vm = @(podman machine inspect podman-machine-default | ConvertFrom-Json)[0]
ssh.exe -i "$($vm.SSHConfig.IdentityPath)" -p $vm.SSHConfig.Port "$($vm.SSHConfig.RemoteUsername)@127.0.0.1" exit
```

Runtime state and per-launch SSH logs live under
`%LOCALAPPDATA%/data-platform/<storage-identity>/forwarding` on Windows, or
`${XDG_CACHE_HOME:-~/.cache}/data-platform/<storage-identity>/forwarding` on Linux.
They are ephemeral, not transfer/backup artifacts. This keeps heartbeat writes
off the external data drive. `platform status` shows forwarding state and its
log path; lifecycle outcomes remain journaled under `DATA_ROOT/.data-platform`.
The log captures both Python-supervisor output and SSH output, with startup
markers, launch exceptions and exit diagnostics. `status` also reports a helper
error and SSH exit code when available. An SSH startup failure is not necessarily
a host-key problem. If a pre-fix run left an empty log and stale state, preserve
the state and review the recorded processes rather than deleting it to retry.
Closing the launching terminal does not stop the background tunnel. This is
not an OS-login service: after reboot or a broken SSH connection, run `up` again.

On Windows, the supervisor preserves `PROGRAMDATA` for OpenSSH. It checks the
launcher's job policy and requests explicit breakaway when permitted; silent
breakaway follows the job's existing policy. A kill-on-close job that prohibits
breakaway fails with an actionable error. Use an independent terminal or native
forwarding in that case. Nested jobs can impose additional restrictions; launch
failures remain visible in the forwarding log.

Local JSON state reads and atomic replacements retry Windows access/sharing/lock
conflicts for up to five delays (0.31 seconds total). Persistent failures,
malformed JSON, missing files, and unrelated errors still fail explicitly.

Occupied ports are rejected rather than adopting a manual tunnel or unrelated
service. Stop the managed tunnel before changing its machine, ports, storage
root, or cache location. Stale supervisor state fails safely: no PID read from
disk is killed, avoiding PID-reuse accidents. A tokenized stop request is handled
only by the owning supervisor. Force-killing that supervisor can leave an orphan
SSH process; verify the recorded process and command before manual recovery,
rather than deleting state or killing every `ssh.exe` process.

Real detached-process reuse, failure, shutdown and restart are tested on Linux
using isolated local listener children; Windows creation flags and generated SSH
commands are covered by unit tests. The manually created tunnel was verified on
Windows/Podman, but the automated Windows background process still needs laptop
validation. No live desktop lakehouse is touched by those tests.

`DATA_PLATFORM_HOME` selects software/configuration; `DATA_ROOT` selects the
storage parent. `CONTRACTS_DIR` selects private dataset contracts and defaults
to `<DATA_ROOT>/contracts`, alongside `DataLake`. Set it in private configuration
or use `init --contracts-dir /path/to/contracts`; it is not part of bootstrap
identity and changing it does not migrate the catalog. Relative storage paths resolve against the checkout; `~` expands
to the user's home, and symlinks are resolved. Initialization defaults to the
explicit `--data-root`, then environment `DATA_ROOT`, then `~/Data`.

Initialization creates the configured contract directory and these storage directories
beneath that parent (the contract directory can be overridden independently):

```text
contracts/                   # private dataset contracts (default location)
DataLake/landing/
DataLake/warehouse/            # Iceberg/object-store owned
Databases/postgres/polaris/    # Polaris metastore only in bind mode
Databases/duckdb/              # operational control database
.data-platform/               # lifecycle journal and checkpoints
```

The generated `DataLake` contains only landing and warehouse directories.
Bronze/silver/gold are logical catalog namespaces created by bootstrap, not
parallel disk folders. The warehouse's internal object layout is managed, not
a directory structure for users to reorganize. Existing legacy folders and
files remain untouched; initialization does not migrate or register them.
See [storage boundaries](architecture.md#physical-storage-and-logical-namespaces).

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
[podman-compose package](https://pypi.org/project/podman-compose/1.6.0/),
[Podman machine](https://docs.podman.io/en/stable/markdown/podman-machine.1.html),
[Compose interpolation](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/),
and [Polaris 1.7 bootstrap behavior](https://polaris.incubator.apache.org/downloads/1.7.0/).
