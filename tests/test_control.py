from pathlib import Path

import duckdb

from lake_cli.contracts import ContractLoader
from lake_cli.control import ControlStore


CONTRACT = """
dataset_id: example.source
source: {path: /tmp/a.parquet}
target: {catalog: research, namespace: bronze, table: example}
"""


def make_contract(tmp_path: Path):
    root = tmp_path / "contracts"
    root.mkdir()
    (root / "example.yml").write_text(CONTRACT)
    return ContractLoader(root).get("example.source")


def test_records_successful_run_and_version(tmp_path):
    connection = duckdb.connect(str(tmp_path / "control.duckdb"))
    store = ControlStore(connection, tmp_path)
    contract = make_contract(tmp_path)
    run_id = store.begin("ingest", contract, "file:///tmp/a.parquet")
    store.record_version(run_id, contract, 123, 10, "[]", "abc")
    store.finish(run_id, "succeeded", metrics={"rows": 10})
    assert store.successful_source(contract.dataset_id, "abc")
    row = store.recent_runs(contract.dataset_id, 1)[0]
    assert row[3] == "succeeded"
    connection.close()


def test_records_failure(tmp_path):
    connection = duckdb.connect(str(tmp_path / "control.duckdb"))
    store = ControlStore(connection, tmp_path)
    contract = make_contract(tmp_path)
    run_id = store.begin("ingest", contract)
    store.finish(run_id, "failed", error="expected failure")
    row = store.recent_runs(None, 1)[0]
    assert row[3] == "failed"
    assert row[6] == "expected failure"
    connection.close()

