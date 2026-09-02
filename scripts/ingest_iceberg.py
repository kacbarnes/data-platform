#!/usr/bin/env python3
"""Ingest one Parquet dataset into Iceberg and record operational provenance."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

import duckdb


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CONTROL_DB = Path(os.environ.get("CONTROL_DB", "/home/kent/Data/Databases/duckdb/control.duckdb"))


def valid_identifier(value):
    if not IDENTIFIER.fullmatch(value):
        raise argparse.ArgumentTypeError(f"invalid SQL identifier: {value!r}")
    return value


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_commit():
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def configure_control_db(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_runs (
            run_id UUID PRIMARY KEY,
            dataset_id VARCHAR NOT NULL,
            source_uri VARCHAR NOT NULL,
            source_sha256 VARCHAR,
            source_size_bytes UBIGINT,
            source_row_count UBIGINT,
            source_schema_json JSON,
            target_catalog VARCHAR NOT NULL,
            target_namespace VARCHAR NOT NULL,
            target_table VARCHAR NOT NULL,
            iceberg_snapshot_id UBIGINT,
            pipeline_git_commit VARCHAR,
            started_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
            completed_at TIMESTAMPTZ,
            status VARCHAR NOT NULL,
            error_message VARCHAR
        )
    """)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--namespace", default="bronze", type=valid_identifier)
    parser.add_argument("--table", required=True, type=valid_identifier)
    parser.add_argument(
        "--adopt-existing",
        action="store_true",
        help="verify and register an existing target instead of overwriting it",
    )
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    if not source.is_file():
        parser.error(f"source does not exist: {source}")

    required = [
        "POLARIS_ROOT_CLIENT_ID", "POLARIS_ROOT_CLIENT_SECRET", "POLARIS_CATALOG"
    ]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        parser.error("missing environment variables: " + ", ".join(missing))

    run_id = uuid.uuid4()
    catalog_alias = "lakehouse"
    target = f"{catalog_alias}.{args.namespace}.{args.table}"
    CONTROL_DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(CONTROL_DB))
    configure_control_db(con)
    con.execute(
        """INSERT INTO ingestion_runs
           (run_id, dataset_id, source_uri, target_catalog, target_namespace,
            target_table, pipeline_git_commit, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'running')""",
        [run_id, args.dataset_id, source.as_uri(), os.environ["POLARIS_CATALOG"],
         args.namespace, args.table, git_commit()],
    )

    try:
        checksum = sha256(source)
        row_count = con.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(source)]
        ).fetchone()[0]
        schema_rows = con.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(source)]
        ).fetchall()
        schema_json = json.dumps([
            {"name": row[0], "type": row[1], "nullable": row[2]} for row in schema_rows
        ])

        con.execute("INSTALL iceberg")
        con.execute("LOAD iceberg")
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
        client_id = os.environ["POLARIS_ROOT_CLIENT_ID"].replace("'", "''")
        client_secret = os.environ["POLARIS_ROOT_CLIENT_SECRET"].replace("'", "''")
        con.execute(f"""
            CREATE OR REPLACE SECRET polaris_secret (
                TYPE iceberg,
                CLIENT_ID '{client_id}',
                CLIENT_SECRET '{client_secret}',
                OAUTH2_SCOPE 'PRINCIPAL_ROLE:ALL',
                OAUTH2_SERVER_URI 'http://localhost:8181/api/catalog/v1/oauth/tokens'
            )
        """)
        warehouse = os.environ["POLARIS_CATALOG"].replace("'", "''")
        con.execute(f"""
            ATTACH '{warehouse}' AS {catalog_alias} (
                TYPE iceberg,
                ENDPOINT 'http://localhost:8181/api/catalog',
                SECRET polaris_secret,
                ACCESS_DELEGATION_MODE 'vended_credentials'
            )
        """)
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {catalog_alias}.{args.namespace}")
        exists = con.execute(
            """SELECT count(*) FROM information_schema.tables
               WHERE table_catalog = ? AND table_schema = ? AND table_name = ?""",
            [catalog_alias, args.namespace, args.table],
        ).fetchone()[0]
        if exists:
            if not args.adopt_existing:
                raise RuntimeError(
                    f"target already exists: {target}; use --adopt-existing only "
                    "after independently confirming its provenance"
                )
            target_row_count = con.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
            if target_row_count != row_count:
                raise RuntimeError(
                    f"cannot adopt {target}: source has {row_count} rows but target "
                    f"has {target_row_count}"
                )
        else:
            quoted_source = str(source).replace("'", "''")
            con.execute(f"CREATE TABLE {target} AS SELECT * FROM read_parquet('{quoted_source}')")
        snapshot_id = con.execute(
            f"SELECT snapshot_id FROM iceberg_snapshots('{target}') ORDER BY timestamp_ms DESC LIMIT 1"
        ).fetchone()[0]
        con.execute(
            """UPDATE ingestion_runs SET source_sha256=?, source_size_bytes=?,
               source_row_count=?, source_schema_json=?, iceberg_snapshot_id=?,
               completed_at=current_timestamp, status='succeeded' WHERE run_id=?""",
            [checksum, source.stat().st_size, row_count, schema_json, snapshot_id, run_id],
        )
        print(f"Created {target}: {row_count:,} rows, snapshot {snapshot_id}, run {run_id}")
    except Exception as exc:
        con.execute(
            """UPDATE ingestion_runs SET completed_at=current_timestamp, status='failed',
               error_message=? WHERE run_id=?""", [str(exc), run_id]
        )
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
