from pathlib import Path

import pytest

from lake_cli.catalog import source_query, sql_string
from lake_cli.errors import LakeError


def test_sql_string_escapes_quotes():
    assert sql_string("O'Reilly") == "'O''Reilly'"


def test_source_query_supports_parquet():
    query = source_query(Path("/tmp/source.parquet"), "parquet")
    assert "read_parquet" in query


def test_source_query_rejects_unknown_format():
    with pytest.raises(LakeError, match="unsupported source format"):
        source_query(Path("/tmp/source.xls"), "xls")

