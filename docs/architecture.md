# Architecture

## Ownership boundaries

| Concern | Authority | Location |
| --- | --- | --- |
| Source deliveries and provenance | User/research source | `${DATA_ROOT}/DataLake/landing` |
| Table metadata and snapshots | Iceberg through Polaris | `${DATA_ROOT}/DataLake/warehouse` |
| Catalog authorization and table pointers | Polaris | PostgreSQL metastore |
| Durable table objects | RustFS | Iceberg warehouse bind mount |
| Dataset contracts | User/research project | Private, Git-ignored `contracts/` |
| Platform and transformation code | Git | This repository |
| Runs, lineage, and quality results | `lake` CLI | `${DATA_ROOT}/Databases/duckdb/control.duckdb` |
| Platform bootstrap and lifecycle operations | `lake platform` | `${DATA_ROOT}/.data-platform` journals and checkpoints |
| Interactive analysis | DuckDB CLI | `lake query` (optional `lakehouse` wrapper) |

Nothing except the object-store service may write directly to `warehouse`.
Projects address tables by catalog, namespace, and table name rather than by
physical paths.

The Python query client owns executable discovery, a temporary credential-bearing
startup file, subprocess execution, and cleanup. It reuses `Settings` and the
catalog setup statements used by Python ingestion. The Linux/WSL `lakehouse`
script is a thin compatibility wrapper; native Windows uses `lake query` or
`python -m lake_cli ... query` directly. Container startup is separate.

The Python platform module owns private configuration initialization and invokes
Docker/Podman Compose for service lifecycle. Configuration selects the checkout,
storage parent, backend, project name, and ports. Lifecycle checkpoints prevent
routine startup from repeating completed bootstrap steps; operational JSON
journals record both successful and failed attempts. They do not duplicate
Iceberg table metadata or dataset-run records. Shutdown preserves bind-mounted
storage and named volumes, and storage relocation requires a separate migration
procedure. PostgreSQL uses a host bind mount on Linux by default, and a
runtime-managed Linux-backed named volume on native Windows. The latter places
catalog authority outside `DATA_ROOT`: complete backup/transfer requires a
database dump alongside warehouse objects, configuration and operational records.

Optional managed Podman forwarding is a client-side transport, not a new data
authority. The platform module starts a detached supervisor which exclusively
owns its SSH child and accepts tokenized shutdown requests. All listeners bind
to host loopback. Existing Podman SSH keys are referenced, never copied; no
lakehouse credentials are passed to this helper. Transient heartbeats/process
records live in a user-local cache, while startup/shutdown outcomes remain in
the durable lifecycle journal. Native runtime networking remains the default.

## Data lifecycle

```text
source artifact
  -> tracked ingest
  -> bronze table snapshot
  -> validated transformation
  -> silver table snapshot
  -> feature/product transformation
  -> gold table snapshot
  -> analysis, ML, publication, or derived retrieval index
```

Bronze, silver, and gold are logical Iceberg namespaces. They are not folders
into which users copy files.

## Physical storage and logical namespaces

New instances create only `landing/` and `warehouse/` within `DataLake/`.
`Databases/` and `.data-platform/` hold separate control/metastore and operational
state; they are not processing stages. Windows PostgreSQL volume storage remains
outside this physical tree, as described above.

Landing is an immutable, file-oriented intake area. Keep original ZIP/TAR
deliveries, workbooks and CSVs there, with project/delivery/version organization
when useful. Landing files are not automatically registered as Iceberg tables.
Ingestion is an explicit, tracked operation defined by a dataset contract.

`research.bronze`, `research.silver`, and `research.gold` are catalog namespaces
that organize registered tables by processing stage. A contract's target
namespace, not its source directory name, chooses that logical location.
Landing and bronze may retain overlapping content because preserving the exact
delivery and providing a queryable table are different responsibilities.

The shared warehouse still contains an internal object/directory layout chosen
by RustFS and Iceberg. Namespace-like prefixes may legitimately appear there;
the rule is not to flatten those objects, but to avoid a second user-maintained
tier layout. Do not move table objects to change their classification.

Legacy `raw`, `Archive`, or physical `bronze/silver/gold` directories are neither
deleted nor relocated by initialization. Existing contract source paths continue
to work. Their contents are not automatically adopted, reclassified, or copied
into the catalog. Review any consolidation separately, preserving immutable
sources and using catalog-aware migration for registered tables.

## Metadata layers

1. **Technical metadata** is automatic: Iceberg schema, partitions, manifests,
   statistics, snapshots, and current table pointers.
2. **Operational metadata** is automatic: run IDs, checksums, input/output
   snapshots, row counts, validation results, timestamps, and Git commits.
3. **Semantic metadata** is reviewed by a human: descriptions, ownership,
   privacy, keys, accepted update behavior, and domain meaning.

This separation avoids maintaining the same metadata manually in several
places while retaining the information that software cannot infer responsibly.

## Project interface

Research projects do not own copies of shared data. They maintain private contracts,
SQL/Python transformations, tests, notebooks, and models. They query shared
tables through Polaris and publish reusable outputs as new catalog tables. Each
published output records the exact input snapshots and producing Git commit.

Actual dataset contracts are not distributed in this repository. Their local YAML
bytes are still hashed for operational provenance. Keep private revisions/backups
under the data owner's controls; the platform Git commit identifies code rather
than establishing that a private contract was committed or published. Synthetic
contract examples remain in documentation and isolated tests.
