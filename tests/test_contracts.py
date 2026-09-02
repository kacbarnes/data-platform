from pathlib import Path

import pytest

from lake_cli.contracts import ContractLoader, quote_identifier
from lake_cli.errors import ContractError


def write_contract(root: Path, body: str) -> None:
    root.mkdir()
    (root / "dataset.yml").write_text(body)


def test_loads_source_contract(tmp_path):
    contracts = tmp_path / "contracts"
    write_contract(contracts, """
dataset_id: example.source
kind: source
owner: researcher
source:
  format: parquet
  path: /tmp/source.parquet
target:
  catalog: research
  namespace: bronze
  table: example_source
quality:
  minimum_rows: 1
""")
    contract = ContractLoader(contracts).get("example.source")
    assert contract.kind == "source"
    assert contract.target.dotted == "research.bronze.example_source"
    assert contract.update.strategy == "create"


def test_merge_requires_primary_key(tmp_path):
    contracts = tmp_path / "contracts"
    write_contract(contracts, """
dataset_id: example.source
source: {path: /tmp/a.parquet}
target: {catalog: research, namespace: bronze, table: example}
update: {strategy: merge}
""")
    with pytest.raises(ContractError, match="primary_key"):
        ContractLoader(contracts).all()


def test_rejects_invalid_identifier(tmp_path):
    contracts = tmp_path / "contracts"
    write_contract(contracts, """
dataset_id: example.source
source: {path: /tmp/a.parquet}
target: {catalog: research, namespace: bronze, table: bad-name}
""")
    with pytest.raises(ContractError, match="invalid table"):
        ContractLoader(contracts).all()


def test_quote_identifier_validates():
    assert quote_identifier("valid_name") == '"valid_name"'
    with pytest.raises(ContractError):
        quote_identifier("invalid-name")

