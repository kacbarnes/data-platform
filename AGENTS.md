# Repository guidance

## Mission

Build a small, comprehensible research data platform that applies production
lakehouse practices locally: immutable provenance, catalog-managed Iceberg
tables, reproducible transformations, explicit contracts, automatic lineage,
and auditable operations. Prefer a reliable narrow feature over a broad feature
that cannot be verified.

## Non-negotiable data invariants

- Never edit, move, or delete files below the Iceberg warehouse directly.
  Polaris and Iceberg exclusively own that tree.
- Treat source artifacts as immutable. Corrections arrive as new source versions
  and new table snapshots rather than in-place file edits.
- Every catalog mutation must run through the platform client and create an
  operational run record, including failed mutations.
- Destructive catalog operations (`DROP`, purge, history expiration, orphan-file
  removal) require an explicit user request and a resolved target. They must not
  be hidden inside routine commands.
- A derived index, cache, feature matrix, or vector store is rebuildable. Its
  authoritative inputs and producing code must be identifiable by snapshot and
  Git commit.
- Never commit credentials or print secrets. Local credentials belong in
  `.env`; examples contain placeholders only.

## Contracts and metadata

- Dataset contracts in `contracts/` are the human-authored source of truth for
  ownership, semantics, target names, update policy, and quality expectations.
- Iceberg/Polaris own technical table metadata: schemas, files, partitions,
  statistics, and snapshots. Do not duplicate or hand-edit it in YAML.
- The CLI owns operational metadata: run status, checksums, input/output
  snapshots, quality results, and code versions.
- Semantic metadata cannot be inferred safely. Require a contract change when
  meaning, ownership, privacy classification, or update semantics change.
- Contract and database-schema changes must be backward compatible when
  practical. Add numbered migrations; never rewrite an applied migration.

## Implementation conventions

- Target Python 3.13 and the pinned DuckDB release in `environment.yml`.
- Use the `src/` package layout. Keep shell scripts as thin environment or
  compatibility launchers; business logic belongs in Python modules.
- Validate all SQL identifiers before interpolation. Bind data values as query
  parameters whenever DuckDB permits it.
- Centralize catalog attachment, contract loading, tracking, quality checks,
  and snapshot discovery instead of copying that logic between commands.
- Commands should be idempotent where possible. A previously successful source
  checksum must be skipped unless the caller explicitly requests a rerun.
- Default to safe failure: never silently overwrite an existing table or adopt
  an untracked table without an explicit recovery option and validation.
- Prefer structured JSON fields in the control database for extensible metrics,
  but keep commonly queried identifiers and timestamps as typed columns.

## Testing and verification

- Unit tests must use temporary local DuckDB databases and files. They must not
  connect to or mutate the live Polaris catalog.
- Live integration checks are read-only by default. If a write test is expressly
  authorized, use a clearly temporary namespace and remove it through the
  catalog API after verification.
- Before handoff, run `pytest`, CLI help/smoke tests, and `docker compose config
  --quiet` when Compose files changed.
- Test failure recording and validation paths, not only successful execution.

## Documentation and change hygiene

- Update `docs/architecture.md` when component boundaries or data ownership
  change.
- Update `docs/cli.md` whenever commands, contract fields, or operational
  behavior change.
- Keep `docs/implementation.md` current with completed work, known limitations,
  and the next safe increment.
- Preserve unrelated user changes and keep commits focused and reviewable.

