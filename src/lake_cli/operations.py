from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import duckdb

from .catalog import (
    attach_catalog,
    current_snapshot,
    publish_query,
    relation_name,
    relation_schema,
    schema_json,
    source_query,
    table_exists,
)
from .config import Settings
from .contracts import Contract, ContractLoader
from .control import ControlStore
from .errors import ContractError, LakeError
from .quality import CheckResult, ensure_passed, evaluate


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class LakeService:
    def __init__(self, settings: Settings, loader: ContractLoader | None = None):
        self.settings = settings
        self.loader = loader or ContractLoader(settings.project_root / "contracts")

    def _connect(self) -> tuple[duckdb.DuckDBPyConnection, ControlStore]:
        self.settings.control_db.parent.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect(str(self.settings.control_db))
        return connection, ControlStore(connection, self.settings.project_root)

    def _validate_contract_catalog(self, contract: Contract) -> None:
        if contract.target.catalog != self.settings.catalog:
            raise ContractError(
                f"{contract.dataset_id} targets catalog {contract.target.catalog!r}, but "
                f"the active catalog is {self.settings.catalog!r}"
            )

    @staticmethod
    def _record_checks(
        control: ControlStore,
        run_id,
        dataset_id: str,
        results: list[CheckResult],
    ) -> None:
        for result in results:
            control.record_quality(
                run_id,
                dataset_id,
                result.name,
                result.passed,
                result.observed,
                result.expected,
            )

    def datasets(self) -> list[Contract]:
        return [self.loader.all()[key] for key in sorted(self.loader.all())]

    def ingest(
        self,
        dataset_id: str,
        *,
        source_override: Path | None = None,
        force: bool = False,
        adopt_existing: bool = False,
        full_refresh: bool = False,
    ) -> dict[str, Any]:
        contract = self.loader.get(dataset_id)
        self._validate_contract_catalog(contract)
        if contract.kind != "source":
            raise ContractError(f"{dataset_id} is a transform; use lake build")
        source = (source_override or contract.source_path)
        if source is None:
            raise ContractError(f"{dataset_id} does not define source.path")
        source = source.expanduser().resolve()
        if not source.is_file():
            raise LakeError(f"source does not exist: {source}")

        connection, control = self._connect()
        run_id = control.begin("ingest", contract, source.as_uri())
        try:
            checksum = file_sha256(source)
            if control.successful_source(dataset_id, checksum) and not force:
                metrics = {"action": "skipped", "reason": "source checksum already succeeded"}
                control.finish(run_id, "skipped", metrics=metrics)
                return {"run_id": str(run_id), "status": "skipped", **metrics}

            query = source_query(source, contract.source_format)
            rows = int(
                connection.execute(f"SELECT count(*) FROM ({query}) AS lake_source").fetchone()[0]
            )
            source_schema = json.dumps(relation_schema(connection, query), separators=(",", ":"))
            checks = evaluate(connection, query, contract.quality)
            self._record_checks(control, run_id, dataset_id, checks)
            ensure_passed(checks)

            attach_catalog(connection, self.settings)
            exists = table_exists(connection, self.settings, contract)
            if exists and adopt_existing:
                target = relation_name(self.settings, contract)
                target_rows = int(connection.execute(f"SELECT count(*) FROM {target}").fetchone()[0])
                if target_rows != rows:
                    raise LakeError(
                        f"cannot adopt {contract.target.dotted}: source has {rows} rows, "
                        f"target has {target_rows}"
                    )
                action = "adopted"
            else:
                action = publish_query(
                    connection,
                    self.settings,
                    contract,
                    query,
                    full_refresh=full_refresh,
                )

            snapshot = current_snapshot(connection, self.settings, contract)
            target = relation_name(self.settings, contract)
            target_rows = int(connection.execute(f"SELECT count(*) FROM {target}").fetchone()[0])
            target_schema = schema_json(connection, f"SELECT * FROM {target}")
            control.record_version(
                run_id, contract, snapshot, target_rows, target_schema, checksum
            )
            metrics = {
                "action": action,
                "source_bytes": source.stat().st_size,
                "source_rows": rows,
                "target_rows": target_rows,
                "snapshot_id": snapshot,
                "source_schema": json.loads(source_schema),
                "contract_metadata": contract.metadata,
            }
            control.finish(run_id, "succeeded", metrics=metrics)
            return {
                "run_id": str(run_id),
                "status": "succeeded",
                "action": action,
                "source_rows": rows,
                "target_rows": target_rows,
                "snapshot_id": snapshot,
            }
        except Exception as exc:
            control.finish(run_id, "failed", error=str(exc))
            raise
        finally:
            connection.close()

    def build(self, dataset_id: str, *, full_refresh: bool = False) -> dict[str, Any]:
        contract = self.loader.get(dataset_id)
        self._validate_contract_catalog(contract)
        if contract.kind != "transform":
            raise ContractError(f"{dataset_id} is a source; use lake ingest")
        sql_path = Path(str(contract.transform["sql"]))
        if not sql_path.is_absolute():
            sql_path = self.settings.project_root / sql_path
        sql_path = sql_path.resolve()
        if not sql_path.is_file():
            raise LakeError(f"transformation SQL does not exist: {sql_path}")
        query = sql_path.read_text().strip().rstrip(";")
        if not query:
            raise LakeError(f"transformation SQL is empty: {sql_path}")

        input_contracts = [self.loader.get(dataset_id) for dataset_id in contract.inputs]
        connection, control = self._connect()
        run_id = control.begin("build", contract, sql_path.as_uri())
        try:
            attach_catalog(connection, self.settings)
            inputs: list[tuple[str, int]] = []
            for input_contract in input_contracts:
                self._validate_contract_catalog(input_contract)
                if not table_exists(connection, self.settings, input_contract):
                    raise LakeError(f"input table does not exist: {input_contract.target.dotted}")
                inputs.append(
                    (
                        input_contract.dataset_id,
                        current_snapshot(connection, self.settings, input_contract),
                    )
                )

            checks = evaluate(connection, query, contract.quality)
            self._record_checks(control, run_id, dataset_id, checks)
            ensure_passed(checks)
            action = publish_query(
                connection,
                self.settings,
                contract,
                query,
                full_refresh=full_refresh,
            )
            snapshot = current_snapshot(connection, self.settings, contract)
            target = relation_name(self.settings, contract)
            target_rows = int(connection.execute(f"SELECT count(*) FROM {target}").fetchone()[0])
            target_schema = schema_json(connection, f"SELECT * FROM {target}")
            control.record_version(run_id, contract, snapshot, target_rows, target_schema)
            control.record_lineage(run_id, dataset_id, snapshot, inputs)
            metrics = {
                "action": action,
                "target_rows": target_rows,
                "snapshot_id": snapshot,
                "input_snapshots": dict(inputs),
            }
            control.finish(run_id, "succeeded", metrics=metrics)
            return {"run_id": str(run_id), "status": "succeeded", **metrics}
        except Exception as exc:
            control.finish(run_id, "failed", error=str(exc))
            raise
        finally:
            connection.close()

    def test(self, dataset_id: str) -> dict[str, Any]:
        contract = self.loader.get(dataset_id)
        self._validate_contract_catalog(contract)
        connection, control = self._connect()
        run_id = control.begin("test", contract)
        try:
            attach_catalog(connection, self.settings)
            if not table_exists(connection, self.settings, contract):
                raise LakeError(f"table does not exist: {contract.target.dotted}")
            target = relation_name(self.settings, contract)
            checks = evaluate(connection, f"SELECT * FROM {target}", contract.quality)
            self._record_checks(control, run_id, dataset_id, checks)
            ensure_passed(checks)
            metrics = {
                "checks": len(checks),
                "passed": sum(result.passed for result in checks),
                "snapshot_id": current_snapshot(connection, self.settings, contract),
            }
            control.finish(run_id, "succeeded", metrics=metrics)
            return {"run_id": str(run_id), "status": "succeeded", **metrics}
        except Exception as exc:
            control.finish(run_id, "failed", error=str(exc))
            raise
        finally:
            connection.close()

    def status(self, dataset_id: str | None, limit: int) -> list[tuple]:
        connection, control = self._connect()
        try:
            return control.recent_runs(dataset_id, limit)
        finally:
            connection.close()

    def lineage(self, dataset_id: str) -> list[tuple]:
        self.loader.get(dataset_id)
        connection, control = self._connect()
        try:
            return control.lineage(dataset_id)
        finally:
            connection.close()

    def query(self, arguments: list[str]) -> int:
        if arguments[:1] == ["--"]:
            arguments = arguments[1:]
        command = [str(self.settings.project_root / "scripts" / "lakehouse"), *arguments]
        return subprocess.run(command, check=False).returncode
