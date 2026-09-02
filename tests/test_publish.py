from pathlib import Path

import duckdb
import pytest

from lake_cli.catalog import publish_query
from lake_cli.config import Settings
from lake_cli.contracts import Contract, Target, UpdatePolicy
from lake_cli.errors import LakeError


def settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        data_root=tmp_path,
        control_db=tmp_path / "control.duckdb",
        catalog="research",
        client_id="test",
        client_secret="test",
        polaris_url="http://invalid",
        catalog_alias="memory",
    )


def contract(tmp_path: Path, strategy: str, keys=()) -> Contract:
    return Contract(
        dataset_id="example.output",
        path=tmp_path / "contract.yml",
        digest="abc",
        kind="transform",
        owner="test",
        description="",
        target=Target("research", "silver", "output"),
        update=UpdatePolicy(strategy, tuple(keys)),
    )


def test_append_strategy(tmp_path):
    connection = duckdb.connect()
    item = contract(tmp_path, "append")
    assert publish_query(connection, settings(tmp_path), item, "SELECT 1 AS id") == "created"
    assert publish_query(connection, settings(tmp_path), item, "SELECT 2 AS id") == "appended"
    assert connection.execute("SELECT count(*) FROM memory.silver.output").fetchone()[0] == 2


def test_replace_requires_explicit_full_refresh(tmp_path):
    connection = duckdb.connect()
    item = contract(tmp_path, "replace")
    publish_query(connection, settings(tmp_path), item, "SELECT 1 AS id")
    with pytest.raises(LakeError, match="--full-refresh"):
        publish_query(connection, settings(tmp_path), item, "SELECT 2 AS id")
    assert (
        publish_query(
            connection, settings(tmp_path), item, "SELECT 2 AS id", full_refresh=True
        )
        == "replaced"
    )
    assert connection.execute("SELECT id FROM memory.silver.output").fetchone()[0] == 2


def test_merge_strategy(tmp_path):
    connection = duckdb.connect()
    item = contract(tmp_path, "merge", ("id",))
    publish_query(
        connection,
        settings(tmp_path),
        item,
        "SELECT * FROM (VALUES (1, 'old')) AS t(id, value)",
    )
    assert (
        publish_query(
            connection,
            settings(tmp_path),
            item,
            "SELECT * FROM (VALUES (1, 'new'), (2, 'added')) AS t(id, value)",
        )
        == "merged"
    )
    assert connection.execute(
        "SELECT * FROM memory.silver.output ORDER BY id"
    ).fetchall() == [(1, "new"), (2, "added")]

