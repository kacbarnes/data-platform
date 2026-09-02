from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb

from .contracts import quote_identifier, validate_identifier
from .errors import QualityError


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    observed: Any
    expected: Any


def evaluate(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    expectations: dict[str, Any],
) -> list[CheckResult]:
    results: list[CheckResult] = []
    schema_rows = connection.execute(
        f"DESCRIBE SELECT * FROM ({query}) AS lake_quality_source"
    ).fetchall()
    columns = {row[0] for row in schema_rows}

    if "minimum_rows" in expectations:
        expected = int(expectations["minimum_rows"])
        observed = int(
            connection.execute(f"SELECT count(*) FROM ({query}) AS lake_quality_source").fetchone()[0]
        )
        results.append(CheckResult("minimum_rows", observed >= expected, observed, expected))

    required = list(expectations.get("required_columns") or ())
    if required:
        missing = sorted(set(required) - columns)
        results.append(CheckResult("required_columns", not missing, missing, required))

    for column in expectations.get("not_null") or ():
        validate_identifier(column, "quality column")
        if column not in columns:
            results.append(CheckResult(f"not_null:{column}", False, "missing", 0))
            continue
        observed = int(
            connection.execute(
                f"SELECT count(*) FROM ({query}) AS lake_quality_source "
                f"WHERE {quote_identifier(column)} IS NULL"
            ).fetchone()[0]
        )
        results.append(CheckResult(f"not_null:{column}", observed == 0, observed, 0))

    unique = tuple(expectations.get("unique") or ())
    if unique:
        for column in unique:
            validate_identifier(column, "unique column")
        missing = sorted(set(unique) - columns)
        if missing:
            results.append(CheckResult("unique", False, {"missing": missing}, list(unique)))
        else:
            keys = ", ".join(quote_identifier(column) for column in unique)
            observed = int(
                connection.execute(f"""
                    SELECT count(*) FROM (
                        SELECT {keys}, count(*) AS occurrences
                        FROM ({query}) AS lake_quality_source
                        GROUP BY {keys}
                        HAVING count(*) > 1
                    ) AS duplicate_groups
                """).fetchone()[0]
            )
            results.append(CheckResult("unique", observed == 0, observed, 0))
    return results


def ensure_passed(results: list[CheckResult]) -> None:
    failures = [result.name for result in results if not result.passed]
    if failures:
        raise QualityError("quality checks failed: " + ", ".join(failures))

