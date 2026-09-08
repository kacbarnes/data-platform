from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from .config import Settings
from .contracts import Contract, quote_identifier, validate_identifier
from .errors import LakeError


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def relation_name(settings: Settings, contract: Contract) -> str:
    return ".".join(
        quote_identifier(part)
        for part in (
            settings.catalog_alias,
            contract.target.namespace,
            contract.target.table,
        )
    )


def attach_catalog(connection: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    connection.execute("INSTALL iceberg")
    connection.execute("LOAD iceberg")
    connection.execute("INSTALL httpfs")
    connection.execute("LOAD httpfs")
    connection.execute(f"""
        CREATE OR REPLACE SECRET lakehouse_s3_secret (
            TYPE s3,
            KEY_ID {sql_string(settings.s3_access_key)},
            SECRET {sql_string(settings.s3_secret_key)},
            REGION {sql_string(settings.s3_region)},
            ENDPOINT {sql_string(settings.s3_endpoint)},
            URL_STYLE 'path',
            USE_SSL false
        )
    """)
    connection.execute(f"""
        CREATE OR REPLACE SECRET polaris_secret (
            TYPE iceberg,
            CLIENT_ID {sql_string(settings.client_id)},
            CLIENT_SECRET {sql_string(settings.client_secret)},
            OAUTH2_SCOPE 'PRINCIPAL_ROLE:ALL',
            OAUTH2_SERVER_URI {sql_string(settings.polaris_url + '/api/catalog/v1/oauth/tokens')}
        )
    """)
    connection.execute(f"""
        ATTACH {sql_string(settings.catalog)} AS {quote_identifier(settings.catalog_alias)} (
            TYPE iceberg,
            ENDPOINT {sql_string(settings.polaris_url + '/api/catalog')},
            SECRET polaris_secret,
            ACCESS_DELEGATION_MODE 'none'
        )
    """)


def table_exists(
    connection: duckdb.DuckDBPyConnection, settings: Settings, contract: Contract
) -> bool:
    count = connection.execute(
        """SELECT count(*) FROM information_schema.tables
           WHERE table_catalog = ? AND table_schema = ? AND table_name = ?""",
        [settings.catalog_alias, contract.target.namespace, contract.target.table],
    ).fetchone()[0]
    return bool(count)


def current_snapshot(
    connection: duckdb.DuckDBPyConnection, settings: Settings, contract: Contract
) -> int:
    target = relation_name(settings, contract)
    row = connection.execute(
        f"SELECT snapshot_id FROM iceberg_snapshots({sql_string(target)}) "
        "ORDER BY timestamp_ms DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise LakeError(f"no Iceberg snapshot found for {contract.target.dotted}")
    return int(row[0])


def relation_schema(connection: duckdb.DuckDBPyConnection, query: str) -> list[dict[str, Any]]:
    rows = connection.execute(f"DESCRIBE SELECT * FROM ({query}) AS lake_schema_source").fetchall()
    return [
        {"name": row[0], "type": row[1], "nullable": row[2]}
        for row in rows
    ]


def source_query(path: Path, source_format: str) -> str:
    path_sql = sql_string(str(path))
    readers = {
        "parquet": f"read_parquet({path_sql})",
        "csv": f"read_csv_auto({path_sql}, header=true)",
        "json": f"read_json_auto({path_sql})",
        "jsonl": f"read_json_auto({path_sql}, format='newline_delimited')",
    }
    try:
        reader = readers[source_format.lower()]
    except KeyError as exc:
        raise LakeError(
            f"unsupported source format {source_format!r}; use parquet, csv, json, or jsonl"
        ) from exc
    return f"SELECT * FROM {reader}"


def publish_query(
    connection: duckdb.DuckDBPyConnection,
    settings: Settings,
    contract: Contract,
    query: str,
    *,
    full_refresh: bool = False,
) -> str:
    target = relation_name(settings, contract)
    namespace = ".".join(
        [quote_identifier(settings.catalog_alias), quote_identifier(contract.target.namespace)]
    )
    connection.execute(f"CREATE SCHEMA IF NOT EXISTS {namespace}")
    exists = table_exists(connection, settings, contract)
    strategy = contract.update.strategy

    if not exists:
        connection.execute(f"CREATE TABLE {target} AS {query}")
        return "created"
    if strategy == "create":
        raise LakeError(
            f"target already exists: {contract.target.dotted}; choose an explicit update "
            "strategy in its contract"
        )
    if strategy == "append":
        connection.execute(f"INSERT INTO {target} {query}")
        return "appended"
    if strategy == "replace":
        if not full_refresh:
            raise LakeError("replace strategy requires --full-refresh")
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(f"DELETE FROM {target}")
            connection.execute(f"INSERT INTO {target} {query}")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        return "replaced"
    if strategy == "merge":
        connection.execute(f"CREATE OR REPLACE TEMP TABLE lake_staged AS {query}")
        schema = relation_schema(connection, "SELECT * FROM lake_staged")
        columns = [validate_identifier(column["name"], "source column") for column in schema]
        keys = contract.update.primary_key
        missing_keys = sorted(set(keys) - set(columns))
        if missing_keys:
            raise LakeError(f"merge keys missing from source: {', '.join(missing_keys)}")
        on_clause = " AND ".join(
            f"target.{quote_identifier(key)} = source.{quote_identifier(key)}" for key in keys
        )
        assignments = ", ".join(
            f"{quote_identifier(column)} = source.{quote_identifier(column)}"
            for column in columns
            if column not in keys
        )
        insert_columns = ", ".join(quote_identifier(column) for column in columns)
        insert_values = ", ".join(
            f"source.{quote_identifier(column)}" for column in columns
        )
        matched = f"WHEN MATCHED THEN UPDATE SET {assignments}" if assignments else ""
        connection.execute(f"""
            MERGE INTO {target} AS target
            USING lake_staged AS source
            ON {on_clause}
            {matched}
            WHEN NOT MATCHED THEN INSERT ({insert_columns}) VALUES ({insert_values})
        """)
        connection.execute("DROP TABLE lake_staged")
        return "merged"
    raise LakeError(f"unsupported update strategy: {strategy}")


def schema_json(connection: duckdb.DuckDBPyConnection, query: str) -> str:
    return json.dumps(relation_schema(connection, query), separators=(",", ":"))
