from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import uuid
from typing import Any

import duckdb

from .contracts import Contract


def git_commit(project_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


class ControlStore:
    def __init__(self, connection: duckdb.DuckDBPyConnection, project_root: Path):
        self.connection = connection
        self.project_root = project_root
        self.migrate()

    def migrate(self) -> None:
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id UUID PRIMARY KEY,
                command VARCHAR NOT NULL,
                dataset_id VARCHAR NOT NULL,
                contract_sha256 VARCHAR NOT NULL,
                source_uri VARCHAR,
                target_catalog VARCHAR NOT NULL,
                target_namespace VARCHAR NOT NULL,
                target_table VARCHAR NOT NULL,
                pipeline_git_commit VARCHAR,
                started_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
                completed_at TIMESTAMPTZ,
                status VARCHAR NOT NULL,
                error_message VARCHAR,
                metrics_json JSON
            )
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS dataset_versions (
                run_id UUID NOT NULL,
                dataset_id VARCHAR NOT NULL,
                catalog_name VARCHAR NOT NULL,
                namespace_name VARCHAR NOT NULL,
                table_name VARCHAR NOT NULL,
                snapshot_id UBIGINT NOT NULL,
                row_count UBIGINT,
                source_sha256 VARCHAR,
                schema_json JSON,
                created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
            )
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS lineage_edges (
                run_id UUID NOT NULL,
                output_dataset_id VARCHAR NOT NULL,
                output_snapshot_id UBIGINT NOT NULL,
                input_dataset_id VARCHAR NOT NULL,
                input_snapshot_id UBIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
            )
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS quality_results (
                run_id UUID NOT NULL,
                dataset_id VARCHAR NOT NULL,
                check_name VARCHAR NOT NULL,
                passed BOOLEAN NOT NULL,
                observed_json JSON,
                expected_json JSON,
                checked_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
            )
        """)

    def begin(self, command: str, contract: Contract, source_uri: str | None = None) -> uuid.UUID:
        run_id = uuid.uuid4()
        self.connection.execute(
            """INSERT INTO pipeline_runs
               (run_id, command, dataset_id, contract_sha256, source_uri,
                target_catalog, target_namespace, target_table,
                pipeline_git_commit, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')""",
            [
                run_id,
                command,
                contract.dataset_id,
                contract.digest,
                source_uri,
                contract.target.catalog,
                contract.target.namespace,
                contract.target.table,
                git_commit(self.project_root),
            ],
        )
        return run_id

    def finish(
        self,
        run_id: uuid.UUID,
        status: str,
        *,
        metrics: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            """UPDATE pipeline_runs
               SET completed_at=current_timestamp, status=?, error_message=?, metrics_json=?
               WHERE run_id=?""",
            [status, error, json.dumps(metrics or {}), run_id],
        )

    def successful_source(self, dataset_id: str, checksum: str) -> bool:
        row = self.connection.execute(
            """SELECT count(*) FROM dataset_versions v
               JOIN pipeline_runs r USING (run_id)
               WHERE v.dataset_id=? AND v.source_sha256=? AND r.status='succeeded'""",
            [dataset_id, checksum],
        ).fetchone()
        return bool(row[0])

    def record_version(
        self,
        run_id: uuid.UUID,
        contract: Contract,
        snapshot_id: int,
        row_count: int,
        schema_json: str,
        source_sha256: str | None = None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO dataset_versions
               (run_id, dataset_id, catalog_name, namespace_name, table_name,
                snapshot_id, row_count, source_sha256, schema_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                run_id,
                contract.dataset_id,
                contract.target.catalog,
                contract.target.namespace,
                contract.target.table,
                snapshot_id,
                row_count,
                source_sha256,
                schema_json,
            ],
        )

    def record_lineage(
        self,
        run_id: uuid.UUID,
        output_dataset_id: str,
        output_snapshot_id: int,
        inputs: list[tuple[str, int]],
    ) -> None:
        for input_dataset_id, input_snapshot_id in inputs:
            self.connection.execute(
                """INSERT INTO lineage_edges
                   (run_id, output_dataset_id, output_snapshot_id,
                    input_dataset_id, input_snapshot_id)
                   VALUES (?, ?, ?, ?, ?)""",
                [run_id, output_dataset_id, output_snapshot_id, input_dataset_id, input_snapshot_id],
            )

    def record_quality(
        self,
        run_id: uuid.UUID,
        dataset_id: str,
        name: str,
        passed: bool,
        observed: Any,
        expected: Any,
    ) -> None:
        self.connection.execute(
            """INSERT INTO quality_results
               (run_id, dataset_id, check_name, passed, observed_json, expected_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [run_id, dataset_id, name, passed, json.dumps(observed), json.dumps(expected)],
        )

    def recent_runs(self, dataset_id: str | None, limit: int) -> list[tuple]:
        if dataset_id:
            return self.connection.execute(
                """SELECT run_id, command, dataset_id, status, started_at, completed_at,
                          error_message
                   FROM pipeline_runs WHERE dataset_id=?
                   ORDER BY started_at DESC LIMIT ?""",
                [dataset_id, limit],
            ).fetchall()
        return self.connection.execute(
            """SELECT run_id, command, dataset_id, status, started_at, completed_at,
                      error_message
               FROM pipeline_runs ORDER BY started_at DESC LIMIT ?""",
            [limit],
        ).fetchall()

    def lineage(self, dataset_id: str) -> list[tuple]:
        return self.connection.execute(
            """SELECT input_dataset_id, input_snapshot_id,
                      output_dataset_id, output_snapshot_id, created_at
               FROM lineage_edges
               WHERE input_dataset_id=? OR output_dataset_id=?
               ORDER BY created_at DESC""",
            [dataset_id, dataset_id],
        ).fetchall()

