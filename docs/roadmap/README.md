# Roadmap implementation plans

These documents turn the package roadmap into reviewable implementation
increments. Each plan describes outcomes and sequencing, not an immutable task
list. Discoveries may change internal design, but changes to scope or public
interfaces must also update `ROADMAP.md` and the affected plan.

## Plan index

- [`v0.2`: Relocatable core](v0.2-relocatable-core.md)
- [`v0.3`: Portable contracts](v0.3-portable-contracts.md)
- [`v0.4`: Unified lifecycle](v0.4-unified-lifecycle.md)
- [`v0.5`: Container portability](v0.5-container-portability.md)
- [`v0.6`: Installation portability](v0.6-installation-portability.md)
- [`v0.7`: Export and migration](v0.7-export-and-migration.md)
- [`v0.8`: Compatibility automation](v0.8-compatibility-automation.md)
- [`v1.0`: Stable platform contract](v1.0-stable-platform.md)

## Common implementation rules

- Make changes in focused commits that remain understandable independently.
- Add unit tests before live integration checks.
- Keep integration writes confined to clearly temporary catalog objects.
- Update `docs/architecture.md` for component-boundary changes and
  `docs/cli.md` for commands, configuration, or behavior.
- Record completed work and remaining limitations in
  `docs/implementation.md`.
- Treat `.env` as private machine state and examples as placeholders only.
- Never make warehouse files part of an export, fixture, or test cleanup.

