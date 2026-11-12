from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .catalog import catalog_statements
from .config import Settings
from .contracts import quote_identifier
from .errors import LakeError


def discover_duckdb(*, environ: Mapping[str, str] | None = None) -> str:
    environment = os.environ if environ is None else environ
    requested = environment.get("DATA_PLATFORM_DUCKDB") or "duckdb"
    executable = shutil.which(
        str(Path(requested).expanduser()), path=environment.get("PATH", os.defpath)
    )
    if executable is None:
        raise LakeError(
            "DuckDB CLI not found; install duckdb-cli in the active environment "
            "or set DATA_PLATFORM_DUCKDB to its executable"
        )
    return executable


def startup_sql(settings: Settings) -> str:
    statements = ";\n".join(catalog_statements(settings)) + ";\n"
    return (
        f".echo off\n.bail on\n.output {os.devnull}\n"
        + statements
        + f"USE {quote_identifier(settings.catalog_alias)}.bronze;\n"
        + ".output stdout\n.bail off\n"
        + (".mode table\n" if os.name == "nt" else ".mode duckbox\n")
        + ".maxrows 40\n"
        + ".prompt 'lakehouse> ' '       ...> '\n"
    )


def run_query(
    settings: Settings,
    arguments: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    environment = os.environ if environ is None else environ
    executable = discover_duckdb(environ=environment)
    forwarded = list(arguments)
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    # Close the private startup file before DuckDB opens it, also on Windows.
    # Credentials are never passed as command-line arguments.
    try:
        fd, filename = tempfile.mkstemp(prefix="lakehouse-init-", suffix=".sql")
    except OSError as exc:
        raise LakeError("cannot create private query startup file; check temporary directory") from exc
    path = Path(filename)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(startup_sql(settings))
        return subprocess.run(
            [executable, "-init", str(path), *forwarded, ":memory:"],
            env=dict(environment),
            check=False,
        ).returncode
    except OSError as exc:
        raise LakeError(
            "cannot start DuckDB query client; check executable and temporary directory"
        ) from exc
    except KeyboardInterrupt:
        return 130
    finally:
        path.unlink(missing_ok=True)
