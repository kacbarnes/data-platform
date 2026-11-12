"""Machine-local configuration and non-destructive Compose lifecycle."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import uuid

from .config import _read_env, discover_project_root, resolve_config_path
from .errors import LakeError
from .forwarding import Forwarding
from .local_state import atomic_json as _atomic_json, read_json


PORTS = {"POLARIS_PORT": 8181, "POLARIS_HEALTH_PORT": 8182,
         "RUSTFS_PORT": 9000, "RUSTFS_CONSOLE_PORT": 9001}
REQUIRED = ("POLARIS_ROOT_CLIENT_ID", "POLARIS_ROOT_CLIENT_SECRET",
            "POLARIS_REALM", "POLARIS_CATALOG", "POSTGRES_USER",
            "POSTGRES_PASSWORD", "POSTGRES_DB", "RUSTFS_ACCESS_KEY",
            "RUSTFS_SECRET_KEY", "ICEBERG_BUCKET")


def _write_private(path: Path, content: str) -> None:
    """Create without overwriting or following an existing symlink."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_postgres_storage() -> str:
    return "volume" if _windows_host() else "bind"


def _windows_host() -> bool:
    return os.name == "nt"


def _forwarding_runtime_root(state_dir: Path) -> Path:
    # Ephemeral heartbeats belong on the local system disk, not the external lake.
    cache = (Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
             if _windows_host() else Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))))
    if not cache.is_absolute():
        raise LakeError("local cache directory must be absolute")
    identity = hashlib.sha256(str(state_dir).encode()).hexdigest()[:20]
    return cache / "data-platform" / identity


def initialize(
    project_root: Path | None,
    *, data_root: Path | None = None,
    backend: str | None = None,
    project_name: str | None = None,
    ports: dict[str, int] | None = None,
    postgres_storage: str | None = None,
    podman_forwarding: str | None = None,
    podman_machine: str | None = None,
) -> dict:
    root = discover_project_root(project_root)
    if not (root / "compose.yaml").is_file():
        raise LakeError("project root must contain compose.yaml")
    env_file = root / ".env"
    if env_file.exists():
        platform = Platform(root)
        if data_root is not None and resolve_config_path(str(data_root), base=root) != platform.data_root:
            raise LakeError("existing DATA_ROOT differs; initialization does not migrate storage")
        if backend is not None and backend != platform.backend:
            raise LakeError("existing backend differs; update PLATFORM_BACKEND explicitly after stopping services")
        if project_name is not None and project_name != platform.project_name:
            raise LakeError("existing Compose project name differs")
        if ports and any(str(v) != platform.values[k] for k, v in ports.items()):
            raise LakeError("existing port configuration differs")
        if postgres_storage is not None and postgres_storage != platform.postgres_storage:
            raise LakeError("existing PostgreSQL storage differs; initialization does not migrate databases")
        if podman_forwarding is not None and podman_forwarding != platform.forwarding_mode:
            raise LakeError("existing forwarding mode differs; stop services before editing PODMAN_FORWARDING")
        if podman_machine is not None and podman_machine != platform.machine:
            raise LakeError("existing Podman machine differs; initialization does not migrate storage")
        return {"created": False, "project_root": str(root), "data_root": str(platform.data_root),
                "backend": platform.backend, "message": "Existing configuration preserved; no bootstrap performed"}
    selected_backend = backend or os.environ.get("PLATFORM_BACKEND", "docker")
    if selected_backend not in ("docker", "podman"):
        raise LakeError("backend must be docker or podman")
    forwarding_mode = podman_forwarding or os.environ.get("PODMAN_FORWARDING", "native")
    machine = podman_machine or os.environ.get("PODMAN_MACHINE", "podman-machine-default")
    _validate_forwarding(selected_backend, forwarding_mode, machine)
    storage = resolve_config_path(str(data_root or os.environ.get("DATA_ROOT") or Path.home() / "Data"), base=root)
    try:
        selected_ports = {**{k: int(os.environ.get(k, v)) for k, v in PORTS.items()},
                          **(ports or {})}
    except ValueError as exc:
        raise LakeError("host ports must be integers") from exc
    _validate_ports(selected_ports)
    name = project_name or os.environ.get("COMPOSE_PROJECT_NAME") or (
        "research-lake-" + hashlib.sha256(str(storage).encode()).hexdigest()[:10]
    )
    _validate_name(name)
    selected_storage = postgres_storage or os.environ.get("POSTGRES_STORAGE") or _default_postgres_storage()
    if selected_storage not in ("bind", "volume"):
        raise LakeError("POSTGRES_STORAGE must be bind or volume")
    # Never generate new credentials over an existing metastore or warehouse.
    for relative in ("Databases/postgres/polaris", "DataLake/warehouse"):
        directory = storage / relative
        if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
            raise LakeError("storage already contains lakehouse state; restore its original configuration")
    _prepare_directories(storage)
    values = {
        "DATA_ROOT": storage.as_posix(), "PLATFORM_BACKEND": selected_backend,
        "PODMAN_FORWARDING": forwarding_mode, "PODMAN_MACHINE": machine,
        "POSTGRES_STORAGE": selected_storage,
        "POSTGRES_STORAGE_TYPE": selected_storage,
        "POSTGRES_STORAGE_SOURCE": "postgres-data" if selected_storage == "volume" else
            (storage / "Databases/postgres/polaris").as_posix(),
        "COMPOSE_PROJECT_NAME": name, "POLARIS_REALM": "POLARIS",
        "POLARIS_ROOT_CLIENT_ID": "root", "POLARIS_ROOT_CLIENT_SECRET": secrets.token_hex(24),
        "POLARIS_CATALOG": "research", "POSTGRES_USER": "polaris", "POSTGRES_DB": "polaris",
        "POSTGRES_PASSWORD": secrets.token_hex(24), "RUSTFS_ACCESS_KEY": "research_lake",
        "RUSTFS_SECRET_KEY": secrets.token_hex(24), "ICEBERG_BUCKET": "research-lakehouse",
        # Container UID 0 maps to the invoking user for rootless Podman.
        "LOCAL_UID": str(os.getuid() if hasattr(os, "getuid") and selected_backend == "docker" else 0),
        "LOCAL_GID": str(os.getgid() if hasattr(os, "getgid") and selected_backend == "docker" else 0),
        **{k: str(v) for k, v in selected_ports.items()},
        "POLARIS_URL": f"http://localhost:{selected_ports['POLARIS_PORT']}",
        "RUSTFS_CLIENT_ENDPOINT": f"localhost:{selected_ports['RUSTFS_PORT']}",
    }
    if selected_backend == "podman":
        values["PODMAN_COMPOSE_PROVIDER"] = os.environ.get("PODMAN_COMPOSE_PROVIDER") or "podman-compose"
    # Use the subset of .env quoting accepted by both Compose and our reader.
    if any("\n" in v or "\r" in v or "'" in v or "$" in v for v in values.values()):
        raise LakeError("configuration paths cannot contain apostrophes, dollars, or newlines yet")
    try:
        _write_private(env_file, "# Private machine-local configuration; do not commit.\n" +
                       "".join(f"{k}='{v}'\n" for k, v in values.items()))
    except FileExistsError as exc:
        raise LakeError("configuration was created concurrently; rerun init") from exc
    return {"created": True, "project_root": str(root), "data_root": str(storage),
            "backend": selected_backend, "project_name": name,
            "message": "Configuration and directories created; run lake platform up"}


def _validate_name(name: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise LakeError("Compose project name must use lowercase letters, digits, hyphens or underscores")


def _validate_forwarding(backend: str, mode: str, machine: str) -> None:
    if mode not in ("native", "ssh") or (mode == "ssh" and backend != "podman"):
        raise LakeError("PODMAN_FORWARDING must be native, or ssh with the podman backend")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", machine):
        raise LakeError("invalid PODMAN_MACHINE name")


def _validate_ports(ports: dict) -> None:
    try:
        numbers = [int(v) for v in ports.values()]
    except (ValueError, TypeError) as exc:
        raise LakeError("host ports must be integers") from exc
    if any(p < 1024 or p > 65535 for p in numbers) or len(set(numbers)) != len(numbers):
        raise LakeError("host ports must be distinct numbers between 1024 and 65535")


def _prepare_directories(storage: Path) -> None:
    for relative in ("DataLake/landing", "DataLake/warehouse", "Databases/postgres/polaris",
                     "Databases/duckdb", ".data-platform"):
        (storage / relative).mkdir(parents=True, exist_ok=True)
    # Test actual write access without modifying the warehouse or metastore.
    with tempfile.TemporaryFile(dir=storage / ".data-platform") as stream:
        stream.write(b"write check")


class Platform:
    def __init__(self, project_root: Path | None = None):
        self.root = discover_project_root(project_root)
        self.values = {**_read_env(self.root / ".env")}
        for key in (*REQUIRED, *PORTS, "DATA_ROOT", "PLATFORM_BACKEND", "COMPOSE_PROJECT_NAME",
                    "LOCAL_UID", "LOCAL_GID", "POLARIS_URL", "RUSTFS_CLIENT_ENDPOINT", "POSTGRES_STORAGE"):
            if key in os.environ:
                self.values[key] = os.environ[key]
        for key in ("PODMAN_FORWARDING", "PODMAN_MACHINE", "PODMAN_COMPOSE_PROVIDER"):
            if key in os.environ:
                self.values[key] = os.environ[key]
        for key in REQUIRED:
            if not self.values.get(key) or self.values[key] == "replace-me":
                raise LakeError(f"missing or placeholder configuration: {key}")
        # Preserve Settings.load's historical ~/Data default for legacy .env
        # files; always pass the absolute result explicitly to Compose.
        self.data_root = resolve_config_path(
            self.values.get("DATA_ROOT") or str(Path.home() / "Data"), base=self.root
        )
        self.backend = self.values.get("PLATFORM_BACKEND", "docker")
        if self.backend not in ("docker", "podman"):
            raise LakeError("PLATFORM_BACKEND must be docker or podman")
        self.forwarding_mode = self.values.get("PODMAN_FORWARDING", "native")
        self.machine = self.values.get("PODMAN_MACHINE", "podman-machine-default")
        _validate_forwarding(self.backend, self.forwarding_mode, self.machine)
        self.project_name = self.values.get("COMPOSE_PROJECT_NAME", "research-data-platform")
        _validate_name(self.project_name)
        # Legacy installations retain their original mount until explicitly changed.
        self.postgres_storage = self.values.get("POSTGRES_STORAGE", "bind")
        if self.postgres_storage not in ("bind", "volume"):
            raise LakeError("POSTGRES_STORAGE must be bind or volume")
        self.values["POSTGRES_STORAGE_TYPE"] = self.postgres_storage
        self.values["POSTGRES_STORAGE_SOURCE"] = (
            "postgres-data" if self.postgres_storage == "volume" else
            (self.data_root / "Databases/postgres/polaris").as_posix()
        )
        for key, default in PORTS.items():
            self.values.setdefault(key, str(default))
        _validate_ports({k: self.values[k] for k in PORTS})
        self.values["DATA_ROOT"] = self.data_root.as_posix()
        self.values.setdefault("LOCAL_UID", str(os.getuid() if hasattr(os, "getuid") and self.backend == "docker" else 0))
        self.values.setdefault("LOCAL_GID", str(os.getgid() if hasattr(os, "getgid") and self.backend == "docker" else 0))
        self.values.setdefault("RUSTFS_CLIENT_ENDPOINT", f"localhost:{self.values['RUSTFS_PORT']}")
        self.state_dir = self.data_root / ".data-platform"
        self.state_file = self.state_dir / "bootstrap.json"
        self.forwarder = Forwarding(_forwarding_runtime_root(self.state_dir), self.machine,
                                    [int(self.values[k]) for k in PORTS])
        self.environment = {**os.environ, **self.values}
        # An unrelated shell Compose setting must not select a different stack.
        for key in ("COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES"):
            self.environment.pop(key, None)

    def _redact(self, text: str) -> str:
        for key, value in self.values.items():
            if value and ("SECRET" in key or "PASSWORD" in key):
                text = text.replace(value, "[redacted]")
        return text

    def _run(self, arguments: list[str], *, timeout: int = 600) -> str:
        executable = shutil.which(self.backend)
        if executable is None:
            raise LakeError(f"{self.backend} is not installed or not on PATH")
        environment = self.environment.copy()
        if self.backend == "podman":
            # Fresh configurations explicitly choose the bundled provider.
            # Legacy installations retain Podman's Docker-first precedence:
            # switching providers on a running stack can create duplicate
            # containers against the same PostgreSQL volume.
            provider = self.values.get("PODMAN_COMPOSE_PROVIDER") or environment.get("PODMAN_COMPOSE_PROVIDER")
            if not provider:
                provider = shutil.which("docker-compose") or "podman-compose"
            resolved = shutil.which(provider)
            if resolved is None:
                raise LakeError("Podman Compose provider not found; update environment.yml or install research-lake-cli[podman], or set PODMAN_COMPOSE_PROVIDER explicitly")
            environment["PODMAN_COMPOSE_PROVIDER"] = resolved
        command = [executable, "compose", "--project-name", self.project_name,
                   "--env-file", str(self.root / ".env"),
                   "--file", str(self.root / "compose.yaml"), *arguments]
        try:
            result = subprocess.run(command, env=environment, cwd=self.root,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    check=False, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LakeError("container operation could not finish; check runtime and retry") from exc
        if result.returncode:
            detail = self._redact(result.stderr + result.stdout)[-3000:]
            raise LakeError(f"{self.backend} Compose failed ({result.returncode}): {detail}")
        return self._redact(result.stdout)

    def _fingerprint(self) -> str:
        selected = {k: self.values.get(k) for k in (*REQUIRED, "DATA_ROOT", "COMPOSE_PROJECT_NAME")}
        selected["COMPOSE_PROJECT_NAME"] = self.project_name
        return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()

    def _state(self) -> dict:
        if self.state_file.exists():
            try:
                state = read_json(self.state_file)
                if not isinstance(state.get("steps"), list):
                    raise ValueError("invalid steps")
            except (ValueError, AttributeError) as exc:
                raise LakeError("bootstrap journal is invalid; restore or review it before retrying") from exc
            if state.get("fingerprint") != self._fingerprint():
                raise LakeError("storage/configuration identity changed; use an explicit migration procedure")
            previous_storage = state.get("postgres_storage", "bind")
            if previous_storage != self.postgres_storage:
                # Permit recovery only from pre-feature, empty failed initialization.
                if ("postgres_storage" in state or state["steps"] or
                        any((self.data_root / "Databases/postgres/polaris").iterdir())):
                    raise LakeError("PostgreSQL storage changed; explicit database migration is required")
                if self._volume_exists():
                    raise LakeError("untracked PostgreSQL volume exists; restore original configuration")
            state["postgres_storage"] = self.postgres_storage
            return state
        return {"fingerprint": self._fingerprint(), "steps": [],
                "postgres_storage": self.postgres_storage}

    def _volume_exists(self) -> bool:
        executable = shutil.which(self.backend)
        if executable is None:
            raise LakeError(f"{self.backend} is not installed or not on PATH")
        # Listing distinguishes an absent volume from a broken runtime connection.
        try:
            result = subprocess.run(
                [executable, "volume", "ls", "--format", "{{.Name}}"],
                env=self.environment, capture_output=True, text=True,
                encoding="utf-8", errors="replace", check=False, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LakeError("could not inspect PostgreSQL volumes") from exc
        if result.returncode:
            raise LakeError("could not inspect PostgreSQL volumes; check runtime connection")
        return f"{self.project_name}_postgres-data" in result.stdout.splitlines()

    @contextmanager
    def _operation(self, action: str):
        _prepare_directories(self.data_root)
        lock = self.state_dir / "operation.lock"
        run = {"run_id": str(uuid.uuid4()), "action": action, "started_at": _now(),
               "status": "running", "project_name": self.project_name, "steps": []}
        run["postgres_storage"] = self.postgres_storage
        run["forwarding_mode"] = self.forwarding_mode
        run["compose_sha256"] = hashlib.sha256((self.root / "compose.yaml").read_bytes()).hexdigest()
        bootstrap_script = self.root / "scripts/bootstrap_catalog.py"
        if bootstrap_script.is_file():
            run["bootstrap_sha256"] = hashlib.sha256(bootstrap_script.read_bytes()).hexdigest()
        journal = self.state_dir / (run["run_id"] + ".json")
        try:
            _write_private(lock, str(os.getpid()))
        except FileExistsError as exc:
            raise LakeError("another operation holds operation.lock; after a crash verify no operation is running before removing it") from exc
        try:
            _atomic_json(journal, run)
            yield run
            run["status"] = "succeeded"
        except BaseException as exc:
            run["status"] = "failed"
            run["error"] = self._redact(str(exc))
            raise
        finally:
            run["completed_at"] = _now()
            try:
                _atomic_json(journal, run)
            finally:
                lock.unlink(missing_ok=True)

    def up(self, *, existing: bool = False, timeout: int = 180) -> dict:
        self._run(["config", "--quiet"], timeout=30)
        with self._operation("up") as run:
            state = self._state()
            if state["steps"] and self.postgres_storage == "volume" and not self._volume_exists():
                raise LakeError("initialized PostgreSQL volume is missing; restore it before startup")
            if self.postgres_storage == "volume" and any((self.data_root / "Databases/postgres/polaris").iterdir()):
                raise LakeError("bind-mounted PostgreSQL files exist; explicit database migration is required")
            if self.postgres_storage == "bind" and _windows_host():
                raise LakeError("Windows PostgreSQL bind storage cannot guarantee POSIX permissions; use POSTGRES_STORAGE=volume (existing databases require migration)")
            if not self.state_file.exists() and not existing:
                postgres = self.data_root / "Databases/postgres/polaris"
                warehouse = self.data_root / "DataLake/warehouse"
                if any(postgres.iterdir()) or any(warehouse.iterdir()):
                    raise LakeError("untracked existing storage; restore original credentials and use up --existing for normal startup only")
                if self.postgres_storage == "volume" and self._volume_exists():
                    raise LakeError("untracked PostgreSQL volume exists; restore original configuration")
                # Record identity before launching containers so partial failure
                # can be retried even when storage has become non-empty.
                _atomic_json(self.state_file, state)
                for key in PORTS:
                    with socket.socket() as probe:
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        try:
                            probe.bind(("127.0.0.1", int(self.values[key])))
                        except OSError as exc:
                            raise LakeError(f"host port {self.values[key]} is occupied ({key})") from exc
            if self.state_file.exists():
                _atomic_json(self.state_file, state)
            if existing and not self.state_file.exists():
                self._run(["up", "-d", "postgres", "rustfs", "polaris"])
                run["steps"].append("existing services started without bootstrap")
            else:
                self._run(["up", "-d", "postgres", "rustfs"])
                for step, arguments in (
                    ("realm", ["--profile", "bootstrap", "run", "--rm", "polaris-bootstrap"]),
                    ("catalog", ["--profile", "bootstrap", "run", "--rm", "catalog-setup"]),
                ):
                    if step == "catalog":
                        self._run(["up", "-d", "polaris"])
                    if step not in state["steps"]:
                        run["steps"].append({"step": step, "status": "running"})
                        _atomic_json(self.state_dir / (run["run_id"] + ".json"), run)
                        self._run(arguments, timeout=max(600, timeout))
                        state["steps"].append(step)
                        _atomic_json(self.state_file, state)
                        run["steps"][-1]["status"] = "succeeded"
            if self.forwarding_mode == "ssh":
                run["forwarding"] = self.forwarder.ensure()
            self._wait_health(timeout)
            return {"status": "running", "backend": self.backend, "data_root": str(self.data_root),
                    "project_name": self.project_name, "polaris_url": f"http://localhost:{self.values['POLARIS_PORT']}",
                    "run_id": run["run_id"], "forwarding_mode": self.forwarding_mode}

    def _wait_health(self, timeout: int) -> None:
        import time
        import urllib.error
        import urllib.request

        deadline = time.monotonic() + timeout
        urls = (f"http://127.0.0.1:{self.values['POLARIS_HEALTH_PORT']}/q/health",
                f"http://127.0.0.1:{self.values['RUSTFS_PORT']}/health")
        while time.monotonic() < deadline:
            try:
                for url in urls:
                    with urllib.request.urlopen(url, timeout=2) as response:
                        if response.status != 200:
                            raise OSError("unhealthy service")
                return
            except (OSError, urllib.error.URLError):
                time.sleep(1)
        raise LakeError("services did not become healthy; inspect lake platform logs, then retry up")

    def down(self) -> dict:
        with self._operation("down") as run:
            self._state()
            self._run(["down"])
            run["forwarding"] = self.forwarder.stop()
            return {"status": "stopped", "run_id": run["run_id"], "data_preserved": True}

    def status(self) -> dict:
        return {"backend": self.backend, "project_name": self.project_name,
                "data_root": str(self.data_root), "services": self._run(["ps"], timeout=30),
                "forwarding_mode": self.forwarding_mode, "forwarding": self.forwarder.status()}

    def logs(self, tail: int = 100) -> dict:
        return {"logs": self._run(["logs", "--no-color", "--tail", str(tail)], timeout=30)}

    def doctor(self) -> dict:
        checks = {"compose_file": (self.root / "compose.yaml").is_file(),
                  "postgres_storage_compatible": not (_windows_host() and self.postgres_storage == "bind"),
                  "storage_exists": self.data_root.is_dir(),
                  "storage_writable": os.access(self.data_root, os.W_OK),
                  "runtime_executable": shutil.which(self.backend) is not None}
        errors = []
        if not checks["postgres_storage_compatible"]:
            errors.append("Windows PostgreSQL needs Linux-backed volume storage; host writability is insufficient")
        if self.forwarding_mode == "ssh":
            try:
                self.forwarder._command()
                checks["forwarding_prerequisites"] = True
            except LakeError as exc:
                checks["forwarding_prerequisites"] = False
                errors.append(str(exc))
        occupied = []
        for key in PORTS:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("127.0.0.1", int(self.values[key])))
                except OSError:
                    occupied.append(int(self.values[key]))
        try:
            self._run(["version"], timeout=30)
            self._run(["config", "--quiet"], timeout=30)
            running = self._run(["ps", "--quiet"], timeout=30).strip()
            if not self.state_file.exists() and not running:
                checks["ports_available"] = not occupied
            checks["compose_and_runtime"] = True
        except LakeError as exc:
            checks["compose_and_runtime"] = False
            errors.append(str(exc))
        return {"ok": all(checks.values()), "checks": checks, "errors": errors,
                "backend": self.backend, "data_root": str(self.data_root),
                "postgres_storage": self.postgres_storage,
                "forwarding_mode": self.forwarding_mode, "forwarding": self.forwarder.status(),
                "occupied_ports": occupied,
                "free_bytes": shutil.disk_usage(self.data_root).free if self.data_root.is_dir() else None}
