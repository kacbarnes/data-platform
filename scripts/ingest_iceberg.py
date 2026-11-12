#!/usr/bin/env python3
"""Compatibility wrapper for the former one-off ingestion interface."""

import argparse
from pathlib import Path
import sys

from lake_cli.cli import main as lake_main
from lake_cli.config import Settings
from lake_cli.contracts import ContractLoader


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--adopt-existing", action="store_true")
    args = parser.parse_args()

    settings = Settings.load()
    contract = ContractLoader(settings.contracts_dir).get(args.dataset_id)
    if args.namespace != contract.target.namespace or args.table != contract.target.table:
        parser.error(
            "--namespace/--table must match the dataset contract; edit the contract "
            "instead of overriding catalog identity"
        )
    print(
        "ingest_iceberg.py is deprecated; use `lake ingest` instead.",
        file=sys.stderr,
    )
    command = ["ingest", args.dataset_id, "--source", str(args.source)]
    if args.adopt_existing:
        command.append("--adopt-existing")
    return lake_main(command)


if __name__ == "__main__":
    raise SystemExit(main())
