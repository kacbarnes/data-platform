from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex

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

    @classmethod
    def load(cls, project_root: Path | None = None) -> "Settings":
        configured_root = os.environ.get("DATA_PLATFORM_HOME")
        if project_root is not None:
            root = project_root
        elif configured_root:
            root = Path(configured_root)
        else:
            root = Path(__file__).resolve().parents[2]
        root = Path(root).expanduser().resolve()
        file_values = _read_env(root / ".env")

        def value(name: str, default: str | None = None) -> str:
            result = os.environ.get(name, file_values.get(name, default))
            if result is None or result == "":
                raise LakeError(f"missing {name} in {root / '.env'}")
            return result

        data_root = Path(value("DATA_ROOT", str(Path.home() / "Data"))).expanduser().resolve()
        control_db = Path(
            os.environ.get(
                "CONTROL_DB", str(data_root / "Databases" / "duckdb" / "control.duckdb")
            )
        ).expanduser().resolve()
        return cls(
            project_root=root,
            data_root=data_root,
            control_db=control_db,
            catalog=value("POLARIS_CATALOG"),
            client_id=value("POLARIS_ROOT_CLIENT_ID"),
            client_secret=value("POLARIS_ROOT_CLIENT_SECRET"),
            polaris_url=value("POLARIS_URL", "http://localhost:8181"),
        )
