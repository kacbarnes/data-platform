# Data platform roadmap

## Purpose

This roadmap takes the research data platform from a machine-specific local
deployment to a relocatable, reproducible package with a stable command and
configuration interface. The platform remains deliberately small: Python and
DuckDB provide the client and execution layer, while PostgreSQL, Polaris, and
RustFS remain containerized services.

Portability means one repository, one configuration model, and one CLI
workflow with platform-specific adapters. It does not mean hiding meaningful
differences in storage, permissions, networking, or container runtimes.

## Product principles

- Preserve the data invariants in `AGENTS.md` throughout every milestone.
- Keep contracts portable and semantic; keep machine state and credentials
  outside Git.
- Never copy or edit an Iceberg warehouse as an ordinary directory.
- Prefer capability detection and actionable diagnostics over OS-name checks.
- Keep Docker and Podman behavior explicit rather than aliasing one to another.
- Support Conda/Mamba without requiring it.
- Make every upgrade and migration independently testable and reversible.
- Stabilize interfaces before considering a native launcher or language port.

## Supported-platform vocabulary

- **First-class**: exercised by automated tests and documented end to end.
- **Supported**: expected to work and documented, but may depend on a hosted
  CI runner or manual release qualification.
- **Experimental**: usable for evaluation without compatibility guarantees.

The initial portability target is first-class Linux and WSL 2. macOS and a
native Windows launcher may become supported once container-VM behavior is
covered by repeatable tests.

## Milestones

| Release | Theme | Outcome | Plan |
|---|---|---|---|
| `v0.2` | Relocatable core | No committed runtime path assumes a user, home directory, environment manager, or checkout location. | [Implementation plan](docs/roadmap/v0.2-relocatable-core.md) |
| `v0.3` | Portable contracts | Contracts resolve named artifact roots and relative paths from machine-local configuration. | [Implementation plan](docs/roadmap/v0.3-portable-contracts.md) |
| `v0.4` | Unified lifecycle | `lake platform` owns initialization, startup, shutdown, status, and diagnostics. | [Implementation plan](docs/roadmap/v0.4-unified-lifecycle.md) |
| `v0.5` | Container portability | Docker Compose and Podman Compose are detected, configured, and tested as explicit backends. | [Implementation plan](docs/roadmap/v0.5-container-portability.md) |
| `v0.6` | Installation portability | Standard Python environments, pipx, uv, and Conda/Mamba are supported; an external DuckDB CLI is optional. | [Implementation plan](docs/roadmap/v0.6-installation-portability.md) |
| `v0.7` | Export and migration | A credential-free package can reconstruct datasets and verify provenance on another installation. | [Implementation plan](docs/roadmap/v0.7-export-and-migration.md) |
| `v0.8` | Compatibility automation | Linux and WSL behavior is continuously tested, with documented qualification for other hosts. | [Implementation plan](docs/roadmap/v0.8-compatibility-automation.md) |
| `v1.0` | Stable platform contract | CLI, configuration, contracts, migrations, upgrades, and release artifacts have explicit compatibility guarantees. | [Implementation plan](docs/roadmap/v1.0-stable-platform.md) |

## Dependency sequence

```text
v0.2 relocatable paths
  -> v0.3 portable contracts
  -> v0.4 lifecycle commands
  -> v0.5 Docker/Podman adapters
  -> v0.6 installation choices
  -> v0.7 export/import
  -> v0.8 compatibility evidence
  -> v1.0 stable interfaces
```

Later milestones may begin design work early, but a release must not depend on
an unstabilized interface from a future milestone.

## Versioning boundaries

Three versions remain independent:

1. The **platform package version** governs CLI, configuration, deployment, and
   operational metadata behavior.
2. A **dataset contract version** governs the meaning and structure of a
   canonical dataset, such as DSSAT contract `0.4.0`.
3. A **control-schema migration version** governs the local operational
   database and advances only through additive numbered migrations.

A platform release must never imply a dataset-contract upgrade.

## Release completion rule

A milestone is complete only when its acceptance criteria pass, affected
documentation is current, migrations are tested from the previous release,
and no credentials or machine-specific state appear in release artifacts.

## Native implementation decision

After `v1.0`, evaluate whether a small Rust launcher would materially improve
single-binary distribution, startup, or installation. Preserve the stable CLI
and configuration contracts if one is introduced. A full Rust rewrite is not
on the roadmap without measured operational need; a C port is not planned.

