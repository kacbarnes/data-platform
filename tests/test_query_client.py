from dataclasses import replace
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

from lake_cli.catalog import attach_catalog, catalog_statements
from lake_cli.config import Settings
from lake_cli.errors import LakeError
from lake_cli.operations import LakeService
from lake_cli import query_client


@pytest.fixture
def settings(tmp_path):
    return Settings(
        project_root=tmp_path / "project with spaces",
        data_root=tmp_path,
        control_db=tmp_path / "control.duckdb",
        catalog="research",
        client_id="dummy'client",
        client_secret="dummy'secret",
        polaris_url="http://example.invalid:8181/",
        s3_access_key="dummy_access",
        s3_secret_key="dummy_s3_secret",
        s3_endpoint="example.invalid:9000",
        s3_region="test-region",
    )


def test_setup_is_shared_and_escapes_credentials(settings):
    executed = []

    class Connection:
        def execute(self, statement):
            executed.append(statement)

    attach_catalog(Connection(), settings)
    assert executed == list(catalog_statements(settings))
    sql = query_client.startup_sql(settings)
    assert "dummy''secret" in sql
    assert "http://example.invalid:8181/api/catalog" in sql
    assert "8181//api" not in sql
    assert "example.invalid:9000" in sql
    assert "test-region" in sql
    assert 'USE "lakehouse".bronze;' in sql
    assert f".output {os.devnull}" in sql
    with pytest.raises(LakeError):
        query_client.startup_sql(replace(settings, catalog_alias="bad;alias"))


def test_discovery_explicit_executable_and_missing_override():
    assert query_client.discover_duckdb(
        environ={"DATA_PLATFORM_DUCKDB": sys.executable}
    ) == sys.executable
    with pytest.raises(LakeError, match="DuckDB CLI not found"):
        query_client.discover_duckdb(
            environ={"DATA_PLATFORM_DUCKDB": "definitely-missing-duckdb", "PATH": ""}
        )


@pytest.mark.parametrize("host,mode", [("nt", "table"), ("posix", "duckbox")])
def test_query_table_borders_follow_host(settings, monkeypatch, host, mode):
    monkeypatch.setattr(query_client.os, "name", host)
    assert f".mode {mode}\n" in query_client.startup_sql(settings)


def test_real_ascii_table_output(settings, monkeypatch, capfd):
    executable = shutil.which("duckdb")
    if executable is None:
        pytest.skip("DuckDB CLI is not installed in PATH")
    monkeypatch.setattr(query_client, "startup_sql", lambda _: ".mode table\n")
    assert query_client.run_query(settings, ["--", "-c", "SELECT 42 AS answer"],
                                 environ={**os.environ, "DATA_PLATFORM_DUCKDB": executable}) == 0
    output = capfd.readouterr().out
    assert "42" in output and "+" in output and "|" in output
    assert output.isascii()


def test_discovery_from_path(monkeypatch):
    observed = []

    def which(name, path):
        observed.append((name, path))
        return "/environment/bin/duckdb"

    monkeypatch.setattr(query_client.shutil, "which", which)
    assert query_client.discover_duckdb(environ={"PATH": "/environment/bin"})
    assert observed == [("duckdb", "/environment/bin")]


@pytest.mark.parametrize("outcome", [0, 7, "oserror", "interrupt"])
def test_private_startup_file_and_cleanup(settings, monkeypatch, outcome):
    observed = []

    def run(command, **kwargs):
        path = Path(command[2])
        observed.append(path)
        assert path.exists()
        assert "dummy''secret" in path.read_text()
        if os.name == "posix":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert command[3:] == ["-c", "SELECT 'a b'", ":memory:"]
        assert settings.client_secret not in command
        assert kwargs["check"] is False
        if outcome == "oserror":
            raise OSError("simulated launch failure")
        if outcome == "interrupt":
            raise KeyboardInterrupt
        return subprocess.CompletedProcess(command, outcome)

    monkeypatch.setattr(query_client.subprocess, "run", run)
    environment = {"DATA_PLATFORM_DUCKDB": sys.executable}
    arguments = ["--", "-c", "SELECT 'a b'"]
    if outcome == "oserror":
        with pytest.raises(LakeError, match="cannot start DuckDB"):
            query_client.run_query(settings, arguments, environ=environment)
    else:
        assert query_client.run_query(settings, arguments, environ=environment) == (
            130 if outcome == "interrupt" else outcome
        )
    assert observed and not observed[0].exists()
    assert arguments[0] == "--"


def test_service_uses_settings_without_checkout_script(settings, monkeypatch):
    observed = []

    def run(selected, arguments):
        observed.append((selected, arguments))
        return 7

    monkeypatch.setattr("lake_cli.operations.run_query", run)
    assert LakeService(settings).query(["--", "-c", "SELECT 1"]) == 7
    assert observed == [(settings, ["--", "-c", "SELECT 1"])]


@pytest.mark.parametrize("fail_startup", [False, True])
def test_local_duckdb_startup_without_catalog(settings, monkeypatch, capfd, fail_startup):
    executable = shutil.which("duckdb")
    if executable is None:
        pytest.skip("DuckDB CLI is not installed in PATH")
    # Exercise the real CLI's dot commands and failure handling, without any
    # extensions, credentials, network access, or live catalog attachment.
    setup = (
        "SELECT * FROM missing_startup_table;\n"
        if fail_startup else "SELECT 'hidden startup output';\n"
    )
    monkeypatch.setattr(
        query_client,
        "startup_sql",
        lambda _: f".echo off\n.bail on\n.output {os.devnull}\n"
        + setup + ".output stdout\n.bail off\n",
    )
    result = query_client.run_query(
        settings, ["--", "-c", "SELECT 42 AS answer"],
        environ={**os.environ, "DATA_PLATFORM_DUCKDB": executable},
    )
    output = capfd.readouterr()
    assert "hidden startup output" not in output.out
    if fail_startup:
        assert result != 0
        assert "missing_startup_table" in output.err
        assert "42" not in output.out
    else:
        assert result == 0
        assert "42" in output.out
