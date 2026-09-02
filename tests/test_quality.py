import duckdb
import pytest

from lake_cli.errors import QualityError
from lake_cli.quality import ensure_passed, evaluate


def test_quality_checks_pass():
    connection = duckdb.connect()
    results = evaluate(
        connection,
        "SELECT * FROM (VALUES (1, 'a'), (2, 'b')) AS t(id, name)",
        {
            "minimum_rows": 2,
            "required_columns": ["id", "name"],
            "not_null": ["id"],
            "unique": ["id"],
        },
    )
    assert results
    assert all(result.passed for result in results)


def test_quality_failure_is_reported():
    connection = duckdb.connect()
    results = evaluate(
        connection,
        "SELECT * FROM (VALUES (1), (1)) AS t(id)",
        {"unique": ["id"]},
    )
    with pytest.raises(QualityError, match="unique"):
        ensure_passed(results)

