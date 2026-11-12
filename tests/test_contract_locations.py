from pathlib import Path
import runpy
import sys

import pytest

from lake_cli.cli import main
from lake_cli.config import Settings
from lake_cli.errors import ContractError
from lake_cli.operations import LakeService


@pytest.fixture
def configured(tmp_path):
    root = tmp_path / "source checkout"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="research-lake-cli"')
    storage = tmp_path / "managed data"
    external = storage / "contracts"
    external.mkdir(parents=True)
    (root / ".env").write_text("\n".join([
        f"DATA_ROOT='{storage.as_posix()}'", "POLARIS_CATALOG=research",
        "POLARIS_ROOT_CLIENT_ID=test", "POLARIS_ROOT_CLIENT_SECRET=test",
        "RUSTFS_ACCESS_KEY=test", "RUSTFS_SECRET_KEY=test",
    ]))
    contract = """dataset_id: example.external
source: {path: /synthetic/input.parquet}
target: {catalog: research, namespace: bronze, table: external}
"""
    (external / "dataset.yml").write_text(contract)
    legacy = root / "contracts"
    legacy.mkdir()
    (legacy / "legacy.yml").write_text(contract.replace("example.external", "example.legacy"))
    return root, external


def test_service_and_cli_use_external_contracts(configured, monkeypatch, capsys):
    root, external = configured
    monkeypatch.delenv("CONTRACTS_DIR", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    settings = Settings.load(root, environ={})
    assert settings.contracts_dir == external
    assert [c.dataset_id for c in LakeService(settings).datasets()] == ["example.external"]
    assert main(["--project-root", str(root), "datasets"]) == 0
    output = capsys.readouterr().out
    assert "example.external" in output and "example.legacy" not in output


def test_missing_external_directory_never_falls_back_to_source(configured):
    root, _ = configured
    settings = Settings.load(root, environ={"CONTRACTS_DIR": "../missing contracts"})
    with pytest.raises(ContractError, match="set CONTRACTS_DIR"):
        LakeService(settings).datasets()


def test_compatibility_ingest_wrapper_uses_external_contract(configured, monkeypatch):
    root, _ = configured
    settings = Settings.load(root, environ={})
    monkeypatch.setattr(Settings, "load", lambda *args, **kwargs: settings)
    calls = []
    monkeypatch.setattr("lake_cli.cli.main", lambda args: calls.append(args) or 0)
    monkeypatch.setattr(sys, "argv", ["ingest_iceberg.py", "synthetic.parquet", "--dataset-id",
                                     "example.external", "--namespace", "bronze", "--table", "external"])
    script = Path(__file__).resolve().parents[1] / "scripts/ingest_iceberg.py"
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(script), run_name="__main__")
    assert result.value.code == 0
    assert calls[0][:2] == ["ingest", "example.external"]
