from __future__ import annotations

import os
import shlex
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import LakeError


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        raise LakeError(f"cannot read platform configuration: {path}")
    for number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise LakeError(f"invalid .env entry at {path}:{number}")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        try:
            parts = shlex.split(raw_value, comments=True, posix=True)
        except ValueError as exc:
            raise LakeError(f"invalid .env value at {path}:{number}: {exc}") from exc
        values[key] = parts[0] if parts else ""
    return values


def resolve_config_path(value: str, *, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def resolve_contracts_dir(
    configured: str | Path | None, *, data_root: Path, project_root: Path
) -> Path:
    if configured is not None and str(configured) == "":
        raise LakeError("CONTRACTS_DIR must not be empty")
    path = resolve_config_path(
        str(configured if configured is not None else data_root / "contracts"),
        base=project_root,
    )
    warehouse = (data_root / "DataLake" / "warehouse").resolve()
    if path.is_relative_to(warehouse):
        raise LakeError("CONTRACTS_DIR must be outside the managed Iceberg warehouse")
    return path


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_root: Path
    control_db: Path
    catalog: str
    client_id: str
    client_secret: str
    polaris_url: str
    catalog_alias: str = "lakehouse"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_endpoint: str = "localhost:9000"
    s3_region: str = "us-west-2"
    contracts_dir: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "contracts_dir", resolve_contracts_dir(
            self.contracts_dir, data_root=self.data_root, project_root=self.project_root,
        ))

    @classmethod
    def load(
        cls,
        project_root: Path | None = None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> Settings:
        environment = os.environ if environ is None else environ
        root = discover_project_root(project_root, environ=environment)
        file_values = _read_env(root / ".env")

        def value(name: str, default: str | None = None) -> str:
            result = environment.get(name, file_values.get(name, default))
            if result is None or result == "":
                raise LakeError(f"missing {name} in {root / '.env'}")
            return result

        data_root = resolve_config_path(
            value("DATA_ROOT", str(Path.home() / "Data")),
            base=root,
        )
        control_db = resolve_config_path(
            value(
                "CONTROL_DB",
                str(data_root / "Databases" / "duckdb" / "control.duckdb"),
            ),
            base=root,
        )

        return cls(
            project_root=root,
            data_root=data_root,
            control_db=control_db,
            catalog=value("POLARIS_CATALOG"),
            client_id=value("POLARIS_ROOT_CLIENT_ID"),
            client_secret=value("POLARIS_ROOT_CLIENT_SECRET"),
            polaris_url=value("POLARIS_URL", "http://localhost:8181"),
            s3_access_key=value("RUSTFS_ACCESS_KEY"),
            s3_secret_key=value("RUSTFS_SECRET_KEY"),
            s3_endpoint=value("RUSTFS_CLIENT_ENDPOINT", "localhost:9000"),
            s3_region=value("AWS_REGION", "us-west-2"),
            contracts_dir=resolve_contracts_dir(
                environment.get("CONTRACTS_DIR", file_values.get("CONTRACTS_DIR")),
                data_root=data_root, project_root=root,
            ),
        )


def discover_project_root(
    explicit: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    package_file: Path | None = None,
) -> Path:
    environment = os.environ if environ is None else environ

    if explicit is not None:
        return explicit.expanduser().resolve()

    configured = environment.get("DATA_PLATFORM_HOME")
    if configured:
        return Path(configured).expanduser().resolve()

    current = (cwd or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if _is_project_root(candidate):
            return candidate

    resolved_package_file = package_file or Path(__file__)
    package_root = resolved_package_file.resolve().parents[2]
    if _is_project_root(package_root):
        return package_root

    raise LakeError(
        "cannot locate the data-platform project; use --project-root "
        "or set DATA_PLATFORM_HOME"
    )


def _is_project_root(path: Path) -> bool:
    try:
        project = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    metadata = project.get("project", {})
    return isinstance(metadata, dict) and metadata.get("name") == "research-lake-cli"
