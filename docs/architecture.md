# Architecture

## Ownership boundaries

| Concern | Authority | Location |
| --- | --- | --- |
| Source artifacts | User/research source | `${DATA_ROOT}/DataLake/landing`, `raw`, or `Archive` |
| Table metadata and snapshots | Iceberg through Polaris | `${DATA_ROOT}/DataLake/warehouse` |
| Catalog authorization and table pointers | Polaris | PostgreSQL metastore |
| Durable table objects | RustFS | Iceberg warehouse bind mount |
| Contracts and transformations | Git | This repository |
| Runs, lineage, and quality results | `lake` CLI | `${DATA_ROOT}/Databases/duckdb/control.duckdb` |
| Interactive analysis | DuckDB CLI | `lakehouse` command |

Nothing except the object-store service may write directly to `warehouse`.
Projects address tables by catalog, namespace, and table name rather than by
physical paths.

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

Research projects do not own copies of shared data. They contain contracts,
SQL/Python transformations, tests, notebooks, and models. They query shared
tables through Polaris and publish reusable outputs as new catalog tables. Each
published output records the exact input snapshots and producing Git commit.

