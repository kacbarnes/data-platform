from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys
import uuid

from . import __version__
from .config import Settings
from .contracts import ContractLoader
from .errors import LakeError
from .operations import LakeService
from .platform import Platform, initialize


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="lake", description="Contract-driven research lakehouse operations"
    )
    result.add_argument("--version", action="version", version=f"lake {__version__}")
    result.add_argument("--project-root", type=Path, help="platform checkout/configuration directory")
    result.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    commands = result.add_subparsers(dest="command", required=True)

    commands.add_parser("datasets", help="list and validate dataset contracts")

    ingest = commands.add_parser("ingest", help="ingest a contracted source into Iceberg")
    ingest.add_argument("dataset_id")
    ingest.add_argument("--source", type=Path, help="override the contracted local source")
    ingest.add_argument("--force", action="store_true", help="rerun a previously successful checksum")
    ingest.add_argument(
        "--adopt-existing",
        action="store_true",
        help="validate and register an existing target without rewriting it",
    )
    ingest.add_argument(
        "--full-refresh",
        action="store_true",
        help="authorize a contract with replace strategy to rebuild its contents",
    )

    build = commands.add_parser("build", help="publish a contracted SQL transformation")
    build.add_argument("dataset_id")
    build.add_argument("--full-refresh", action="store_true")

    test = commands.add_parser("test", help="run contracted quality checks")
    test.add_argument("dataset_id")

    status = commands.add_parser("status", help="show recent operational runs")
    status.add_argument("dataset_id", nargs="?")
    status.add_argument("--limit", type=int, default=20)

    lineage = commands.add_parser("lineage", help="show recorded snapshot lineage")
    lineage.add_argument("dataset_id")

    query = commands.add_parser("query", help="open the attached DuckDB query client")
    query.add_argument("arguments", nargs=argparse.REMAINDER)
    platform = commands.add_parser("platform", help="configure and operate local container services")
    actions = platform.add_subparsers(dest="platform_action", required=True)
    init = actions.add_parser("init", help="create private configuration and storage directories")
    init.add_argument("--data-root", type=Path, help="storage parent; defaults to DATA_ROOT or ~/Data")
    init.add_argument("--backend", choices=("docker", "podman"))
    init.add_argument("--project-name")
    for option in ("polaris-port", "polaris-health-port", "rustfs-port", "rustfs-console-port"):
        init.add_argument("--" + option, type=int)
    up = actions.add_parser("up", help="start services and bootstrap a fresh instance")
    up.add_argument("--existing", action="store_true", help="start legacy storage without bootstrap")
    up.add_argument("--timeout", type=int, default=180, help="health wait in seconds")
    actions.add_parser("down", help="stop services while preserving data")
    actions.add_parser("status", help="show container status")
    actions.add_parser("doctor", help="check configuration and runtime without changing state")
    logs = actions.add_parser("logs", help="show bounded, redacted container logs")
    logs.add_argument("--tail", type=int, default=100)
    return result


def _json_default(value):
    if isinstance(value, (datetime, date, uuid.UUID)):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _print_result(value, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, default=_json_default))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            print(f"{key}: {item}")
        return
    print(value)


def _print_rows(headers: tuple[str, ...], rows: list[tuple], as_json: bool) -> None:
    if as_json:
        _print_result([dict(zip(headers, row)) for row in rows], True)
        return
    if not rows:
        print("No records.")
        return
    rendered = [["" if value is None else str(value) for value in row] for row in rows]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rendered))
        for index in range(len(headers))
    ]
    print("  ".join(headers[index].ljust(widths[index]) for index in range(len(headers))))
    print("  ".join("-" * width for width in widths))
    for row in rendered:
        print("  ".join(row[index].ljust(widths[index]) for index in range(len(headers))))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "platform":
            if args.platform_action == "init":
                ports = {key: value for key, value in {
                    "POLARIS_PORT": args.polaris_port,
                    "POLARIS_HEALTH_PORT": args.polaris_health_port,
                    "RUSTFS_PORT": args.rustfs_port,
                    "RUSTFS_CONSOLE_PORT": args.rustfs_console_port,
                }.items() if value is not None}
                result = initialize(args.project_root, data_root=args.data_root,
                                    backend=args.backend, project_name=args.project_name, ports=ports)
            else:
                platform = Platform(args.project_root)
                if args.platform_action == "up":
                    if args.timeout < 1:
                        raise LakeError("timeout must be positive")
                    result = platform.up(existing=args.existing, timeout=args.timeout)
                elif args.platform_action == "logs":
                    if not 1 <= args.tail <= 10000:
                        raise LakeError("log tail must be between 1 and 10000")
                    result = platform.logs(args.tail)
                else:
                    result = getattr(platform, args.platform_action)()
            _print_result(result, args.json)
            return 2 if args.platform_action == "doctor" and not result["ok"] else 0
        settings = Settings.load(args.project_root)
        service = LakeService(settings, ContractLoader(settings.project_root / "contracts"))
        if args.command == "datasets":
            rows = [
                (
                    contract.dataset_id,
                    contract.kind,
                    contract.target.namespace,
                    contract.target.table,
                    contract.update.strategy,
                )
                for contract in service.datasets()
            ]
            _print_rows(("dataset", "kind", "namespace", "table", "update"), rows, args.json)
            return 0
        if args.command == "ingest":
            result = service.ingest(
                args.dataset_id,
                source_override=args.source,
                force=args.force,
                adopt_existing=args.adopt_existing,
                full_refresh=args.full_refresh,
            )
            _print_result(result, args.json)
            return 0
        if args.command == "build":
            _print_result(
                service.build(args.dataset_id, full_refresh=args.full_refresh), args.json
            )
            return 0
        if args.command == "test":
            _print_result(service.test(args.dataset_id), args.json)
            return 0
        if args.command == "status":
            rows = service.status(args.dataset_id, args.limit)
            _print_rows(
                ("run_id", "command", "dataset", "status", "started", "completed", "error"),
                rows,
                args.json,
            )
            return 0
        if args.command == "lineage":
            rows = service.lineage(args.dataset_id)
            _print_rows(
                ("input", "input_snapshot", "output", "output_snapshot", "created"),
                rows,
                args.json,
            )
            return 0
        if args.command == "query":
            return service.query(args.arguments)
        raise LakeError(f"unknown command: {args.command}")
    except LakeError as exc:
        if args.json:
            _print_result({"error": str(exc)}, True)
        else:
            print(f"lake: {exc}", file=sys.stderr)
        return 2
    except OSError:
        message = "cannot access platform files; check paths and permissions"
        if args.json:
            _print_result({"error": message}, True)
        else:
            print(f"lake: {message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
