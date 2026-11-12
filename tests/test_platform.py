import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

from lake_cli.cli import main
from lake_cli.config import Settings, _read_env
from lake_cli.errors import LakeError
from lake_cli.platform import PORTS, Platform, initialize, _forwarding_runtime_root


@pytest.fixture
def project(tmp_path, monkeypatch):
    # General lifecycle cases model Linux bind storage; Windows-specific
    # cases below explicitly select their host/storage behavior.
    monkeypatch.setattr("lake_cli.platform._windows_host", lambda: False)
    root = tmp_path / "checkout with spaces"
    root.mkdir()
    repository = Path(__file__).resolve().parents[1]
    shutil.copyfile(repository / "compose.yaml", root / "compose.yaml")
    (root / "contracts").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='test'\n")
    for key in (*PORTS, "DATA_ROOT", "PLATFORM_BACKEND", "COMPOSE_PROJECT_NAME",
                "POLARIS_ROOT_CLIENT_SECRET", "POSTGRES_PASSWORD", "RUSTFS_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key in ("POSTGRES_STORAGE", "POSTGRES_STORAGE_TYPE", "POSTGRES_STORAGE_SOURCE"):
        monkeypatch.delenv(key, raising=False)
    for key in ("PODMAN_FORWARDING", "PODMAN_MACHINE", "PODMAN_COMPOSE_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    return root


def test_init_private_repeatable_and_relative(project, capsys):
    result = initialize(project, data_root=Path("storage with spaces"), backend="podman")
    assert result["created"]
    original = (project / ".env").read_bytes()
    values = _read_env(project / ".env")
    assert values["LOCAL_UID"] == "0"
    assert values["DATA_ROOT"] == (project / "storage with spaces").as_posix()
    assert len(values["POSTGRES_PASSWORD"]) == 48
    assert Settings.load(project, environ={}).data_root == project / "storage with spaces"
    if os.name == "posix":
        assert stat.S_IMODE((project / ".env").stat().st_mode) == 0o600
    assert not initialize(project)["created"]
    assert (project / ".env").read_bytes() == original
    assert not (project / "storage with spaces/.data-platform/bootstrap.json").exists()
    assert values["POSTGRES_PASSWORD"] not in json.dumps(result)
    assert capsys.readouterr().out == ""
    with pytest.raises(LakeError, match="differs"):
        initialize(project, data_root=Path("different"))


def test_init_creates_minimal_physical_lake_not_catalog_tiers(project):
    initialize(project, data_root=Path("data"))
    lake = project / "data/DataLake"
    assert {entry.name for entry in lake.iterdir()} == {"landing", "warehouse"}
    assert (lake / "landing").is_dir()
    assert (lake / "warehouse").is_dir()
    assert not any((lake / stage).exists() for stage in ("bronze", "silver", "gold"))


def test_init_preserves_legacy_tier_files_without_relocating(project):
    storage = project / "data"
    artifacts = []
    for stage in ("bronze", "silver", "gold"):
        directory = storage / "DataLake" / stage
        directory.mkdir(parents=True)
        artifact = directory / "existing-file.txt"
        artifact.write_bytes(b"existing user data\n")
        artifacts.append(artifact)
    initialize(project, data_root=storage)
    initialize(project, data_root=storage)
    assert all(p.read_bytes() == b"existing user data\n" for p in artifacts)
    assert not any((storage / "DataLake/warehouse").iterdir())
    assert not any((storage / "DataLake/landing").iterdir())


def test_init_refuses_existing_storage_and_invalid_ports(project):
    storage = project / "old"
    metastore = storage / "Databases/postgres/polaris"
    metastore.mkdir(parents=True)
    (metastore / "PG_VERSION").write_text("17")
    with pytest.raises(LakeError, match="already contains"):
        initialize(project, data_root=storage)
    assert not (project / ".env").exists()
    with pytest.raises(LakeError, match="distinct"):
        initialize(project, ports={"POLARIS_PORT": 9000})


def test_init_permission_failure_does_not_create_credentials(project, monkeypatch):
    def denied(storage):
        raise PermissionError("read-only storage")
    monkeypatch.setattr("lake_cli.platform._prepare_directories", denied)
    assert main(["--project-root", str(project), "platform", "init"]) == 2
    assert not (project / ".env").exists()


def test_windows_path_is_single_compose_mount():
    import yaml
    document = yaml.safe_load((Path(__file__).resolve().parents[1] / "compose.yaml").read_text())
    mount = document["services"]["postgres"]["volumes"][0]
    assert mount["type"] == "${POSTGRES_STORAGE_TYPE:-bind}"
    assert mount["target"] == "/var/lib/postgresql/data"
    assert "DATA_ROOT" in mount["source"]
    assert "postgres-data" in document["volumes"]


@pytest.mark.parametrize("backend", ["docker", "podman"])
def test_commands_override_ambient_compose_settings_and_redact(project, monkeypatch, backend):
    initialize(project, data_root=Path("data"), backend=backend)
    monkeypatch.setenv("COMPOSE_FILE", "unrelated-compose.yaml")
    platform = Platform(project)
    observed = []

    def run(command, **kwargs):
        observed.append(command)
        assert command[1] == "compose"
        assert "--project-directory" not in command
        assert kwargs["cwd"] == project
        if backend == "podman":
            assert kwargs["env"]["PODMAN_COMPOSE_PROVIDER"] == backend
        assert "COMPOSE_FILE" not in kwargs["env"]
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        assert kwargs["env"]["DATA_ROOT"] == (project / "data").as_posix()
        assert platform.values["POSTGRES_PASSWORD"] not in command
        return subprocess.CompletedProcess(command, 1, "", platform.values["POSTGRES_PASSWORD"])

    monkeypatch.setattr("lake_cli.platform.shutil.which", lambda _: backend)
    monkeypatch.setattr("lake_cli.platform.subprocess.run", run)
    with pytest.raises(LakeError) as error:
        platform.status()
    assert "[redacted]" in str(error.value)
    assert platform.values["POSTGRES_PASSWORD"] not in str(error.value)
    assert str(project / "compose.yaml") in observed[0]


def fake_runtime(platform, monkeypatch, fail_catalog=False):
    calls = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        if fail_catalog and "catalog-setup" in arguments:
            raise LakeError("simulated catalog failure")
        return ""

    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, address): pass
        def setsockopt(self, *args): pass

    monkeypatch.setattr(platform, "_run", run)
    monkeypatch.setattr(platform, "_wait_health", lambda _: None)
    monkeypatch.setattr("lake_cli.platform.socket.socket", Socket)
    return calls


def test_bootstrap_resume_and_journal(project, monkeypatch):
    initialize(project, data_root=Path("data"))
    platform = Platform(project)
    calls = fake_runtime(platform, monkeypatch, fail_catalog=True)
    with pytest.raises(LakeError, match="simulated catalog"):
        platform.up()
    state = json.loads(platform.state_file.read_text())
    assert state["steps"] == ["realm"]
    journals = [p for p in platform.state_dir.glob("*.json") if p != platform.state_file]
    assert json.loads(journals[0].read_text())["status"] == "failed"
    assert not (platform.state_dir / "operation.lock").exists()
    assert any("polaris-bootstrap" in c for c in calls)
    retry_calls = fake_runtime(platform, monkeypatch)
    assert platform.up()["status"] == "running"
    assert not any("polaris-bootstrap" in c for c in retry_calls)
    assert any("catalog-setup" in c for c in retry_calls)
    steady_calls = fake_runtime(platform, monkeypatch)
    platform.up()
    assert not any("run" in c for c in steady_calls)
    platform.down()
    assert ["down"] in steady_calls
    assert platform.state_file.exists()
    assert not any("--volumes" in c for c in steady_calls)


def test_identity_lock_and_legacy_guard(project, monkeypatch):
    initialize(project, data_root=Path("data"))
    platform = Platform(project)
    fake_runtime(platform, monkeypatch)
    lock = platform.state_dir / "operation.lock"
    lock.write_text("another process")
    with pytest.raises(LakeError, match="another operation"):
        platform.up()
    lock.unlink()
    (platform.data_root / "Databases/postgres/polaris/PG_VERSION").write_text("17")
    with pytest.raises(LakeError, match="untracked existing"):
        platform.up()
    calls = fake_runtime(platform, monkeypatch)
    platform.up(existing=True)
    assert not any("run" in c for c in calls)
    assert not platform.state_file.exists()


def test_fingerprint_change_fails_without_bootstrap(project, monkeypatch):
    initialize(project, data_root=Path("data"))
    platform = Platform(project)
    fake_runtime(platform, monkeypatch)
    platform.up()
    monkeypatch.setenv("POLARIS_ROOT_CLIENT_SECRET", "new-value")
    changed = Platform(project)
    calls = fake_runtime(changed, monkeypatch)
    with pytest.raises(LakeError, match="identity changed"):
        changed.up()
    assert not any("run" in c for c in calls)


def test_doctor_is_read_only_and_cli_json(project, monkeypatch, capsys):
    initialize(project, data_root=Path("data"))
    platform = Platform(project)
    before = {p: p.read_bytes() for p in platform.state_dir.glob("*")}
    fake_runtime(platform, monkeypatch)
    monkeypatch.setattr(Platform, "_run", lambda *args, **kwargs: "")
    monkeypatch.setattr("lake_cli.platform.shutil.which", lambda _: "docker")
    assert main(["--project-root", str(project), "--json", "platform", "doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    assert {p: p.read_bytes() for p in platform.state_dir.glob("*")} == before


@pytest.mark.parametrize("storage", ["bind", "volume"])
def test_compose_configuration_with_real_docker(project, storage):
    executable = shutil.which("docker")
    if executable is None:
        pytest.skip("Docker is not installed")
    initialize(project, data_root=Path("data files"), postgres_storage=storage)
    result = subprocess.run(
        [executable, "compose", "--env-file", str(project / ".env"),
         "-f", str(project / "compose.yaml"), "config", "--quiet"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_occupied_port_and_runtime_timeout_are_journaled(project, monkeypatch):
    initialize(project, data_root=Path("data"))
    platform = Platform(project)
    fake_runtime(platform, monkeypatch)

    class BusySocket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def setsockopt(self, *args): pass
        def bind(self, address): raise OSError("in use")

    monkeypatch.setattr("lake_cli.platform.socket.socket", BusySocket)
    with pytest.raises(LakeError, match="occupied"):
        platform.up()
    assert not (platform.state_dir / "operation.lock").exists()
    assert all(json.loads(p.read_text())["status"] == "failed"
               for p in platform.state_dir.glob("*.json") if p != platform.state_file)
    monkeypatch.setattr("lake_cli.platform.shutil.which", lambda _: "docker")

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr("lake_cli.platform.subprocess.run", timeout)
    # Use a new instance because fake_runtime replaced the first instance's runner.
    with pytest.raises(LakeError, match="could not finish"):
        Platform(project).status()


def test_health_timeout_and_missing_runtime(project, monkeypatch):
    initialize(project, data_root=Path("data"))
    platform = Platform(project)
    with pytest.raises(LakeError, match="did not become healthy"):
        platform._wait_health(0)
    monkeypatch.setattr("lake_cli.platform.shutil.which", lambda _: None)
    with pytest.raises(LakeError, match="not installed"):
        platform.status()


def test_json_error_and_existing_file_permissions(project, monkeypatch, capsys):
    initialize(project, data_root=Path("data"))
    original = (project / ".env").read_bytes()
    assert main(["--project-root", str(project), "--json", "platform", "init",
                 "--data-root", "different"]) == 2
    assert "error" in json.loads(capsys.readouterr().out)
    assert (project / ".env").read_bytes() == original


def test_legacy_data_root_default_and_environment_ports(project, tmp_path, monkeypatch):
    monkeypatch.setenv("POLARIS_PORT", "18181")
    initialize(project, data_root=Path("data"))
    values = _read_env(project / ".env")
    assert values["POLARIS_URL"] == "http://localhost:18181"
    monkeypatch.delenv("POLARIS_PORT")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # Simulate the original private .env, which relied on ~/Data defaults.
    lines = (project / ".env").read_text().splitlines()
    (project / ".env").write_text("\n".join(line for line in lines if not line.startswith("DATA_ROOT=")))
    assert Platform(project).data_root == tmp_path / "Data"


def test_storage_selection_and_existing_config_preserved(project, monkeypatch):
    monkeypatch.setattr("lake_cli.platform._default_postgres_storage", lambda: "volume")
    initialize(project, data_root=Path("data"), backend="podman")
    platform = Platform(project)
    assert platform.postgres_storage == "volume"
    assert platform.environment["POSTGRES_STORAGE_SOURCE"] == "postgres-data"
    original = (project / ".env").read_bytes()
    with pytest.raises(LakeError, match="does not migrate"):
        initialize(project, postgres_storage="bind")
    assert (project / ".env").read_bytes() == original
    monkeypatch.setenv("POSTGRES_STORAGE", "invalid")
    with pytest.raises(LakeError, match="bind or volume"):
        Platform(project)


def test_failed_legacy_bind_can_retry_empty_volume(project, monkeypatch):
    initialize(project, data_root=Path("data"))
    old = Platform(project)
    old.state_file.write_text(json.dumps({"fingerprint": old._fingerprint(), "steps": []}))
    monkeypatch.setenv("POSTGRES_STORAGE", "volume")
    platform = Platform(project)
    monkeypatch.setattr(platform, "_volume_exists", lambda: False)
    fake_runtime(platform, monkeypatch)
    platform.up()
    assert json.loads(platform.state_file.read_text())["postgres_storage"] == "volume"
    monkeypatch.setattr(platform, "_volume_exists", lambda: True)
    calls = fake_runtime(platform, monkeypatch)
    platform.up()
    assert not any("run" in c for c in calls)
    platform.down()
    assert ["down"] in calls
    assert not any("--volumes" in c for c in calls)
    monkeypatch.setattr(platform, "_volume_exists", lambda: False)
    with pytest.raises(LakeError, match="volume is missing"):
        platform.up()


@pytest.mark.parametrize("kind", ["completed", "files", "recorded"])
def test_storage_change_never_abandons_database(project, monkeypatch, kind):
    initialize(project, data_root=Path("data"))
    old = Platform(project)
    state = {"fingerprint": old._fingerprint(), "steps": []}
    if kind == "completed":
        state["steps"] = ["realm"]
    elif kind == "files":
        (old.data_root / "Databases/postgres/polaris/PG_VERSION").write_text("17")
    else:
        state["postgres_storage"] = "bind"
    old.state_file.write_text(json.dumps(state))
    monkeypatch.setenv("POSTGRES_STORAGE", "volume")
    platform = Platform(project)
    calls = fake_runtime(platform, monkeypatch)
    with pytest.raises(LakeError, match="migration"):
        platform.up()
    assert not any("up" in c for c in calls)


def test_untracked_volume_refused_and_inspection_errors(project, monkeypatch):
    initialize(project, data_root=Path("data"), postgres_storage="volume")
    platform = Platform(project)
    monkeypatch.setattr("lake_cli.platform.shutil.which", lambda _: "podman")
    monkeypatch.setattr("lake_cli.platform.subprocess.run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, platform.project_name + "_postgres-data\n", ""))
    assert platform._volume_exists()
    calls = fake_runtime(platform, monkeypatch)
    with pytest.raises(LakeError, match="untracked PostgreSQL volume"):
        platform.up()
    assert not any("up" in c for c in calls)
    monkeypatch.setattr("lake_cli.platform.subprocess.run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 1, "", "runtime unavailable"))
    with pytest.raises(LakeError, match="runtime connection"):
        platform._volume_exists()


def test_windows_doctor_rejects_bind_without_touching_storage(project, monkeypatch):
    initialize(project, data_root=Path("data"), postgres_storage="bind")
    monkeypatch.setattr("lake_cli.platform._windows_host", lambda: True)
    platform = Platform(project)
    calls = fake_runtime(platform, monkeypatch)
    monkeypatch.setattr("lake_cli.platform.shutil.which", lambda _: "podman")
    assert not platform.doctor()["ok"]
    with pytest.raises(LakeError, match="POSIX permissions"):
        platform.up()
    assert not any("up" in c for c in calls)


@pytest.mark.parametrize("exit_code", [0, 1])
def test_container_output_utf8_and_invalid_bytes_remain_readable(project, monkeypatch, exit_code):
    initialize(project, data_root=Path("data"))
    platform = Platform(project)
    actual_run = subprocess.run
    payload = "service: snowman ☃ ".encode("utf-8") + b"\x8f " + platform.values["POSTGRES_PASSWORD"].encode()

    def container_output(command, **kwargs):
        # Real pipe decoding, including bytes undefined in Windows CP1252.
        script = (f"import sys; sys.stdout.buffer.write({payload!r}); "
                  f"sys.stderr.buffer.write({payload!r}); sys.exit({exit_code})")
        return actual_run([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr("lake_cli.platform.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("lake_cli.platform.subprocess.run", container_output)
    if exit_code:
        with pytest.raises(LakeError) as error:
            platform.logs()
        output = str(error.value)
        assert "Compose failed (1)" in output
    else:
        output = platform.logs()["logs"]
    assert "☃" in output
    assert "\ufffd" in output
    assert "[redacted]" in output
    assert platform.values["POSTGRES_PASSWORD"] not in output


def test_managed_forwarding_lifecycle_and_journal(project, monkeypatch):
    initialize(project, data_root=Path("data"), backend="podman", podman_forwarding="ssh")
    platform = Platform(project)
    calls = fake_runtime(platform, monkeypatch)
    events = []
    monkeypatch.setattr(platform.forwarder, "ensure", lambda: events.append("ensure") or {"status": "running"})
    monkeypatch.setattr(platform.forwarder, "stop", lambda: events.append("stop") or {"status": "stopped"})
    monkeypatch.setattr(platform, "_wait_health", lambda _: events.append("health"))
    assert platform.up()["forwarding_mode"] == "ssh"
    assert events == ["ensure", "health"]
    journals = [json.loads(p.read_text()) for p in platform.state_dir.glob("*.json") if p != platform.state_file]
    assert journals[0]["forwarding"]["status"] == "running"
    platform.down()
    assert events[-1] == "stop"
    assert ["down"] in calls


def test_forwarding_failure_records_run_and_preserves_bootstrap(project, monkeypatch):
    initialize(project, data_root=Path("data"), backend="podman", podman_forwarding="ssh")
    platform = Platform(project)
    fake_runtime(platform, monkeypatch)

    def failure():
        raise LakeError("SSH forwarding failed")

    monkeypatch.setattr(platform.forwarder, "ensure", failure)
    with pytest.raises(LakeError, match="SSH forwarding failed"):
        platform.up()
    assert json.loads(platform.state_file.read_text())["steps"] == ["realm", "catalog"]
    journals = [json.loads(p.read_text()) for p in platform.state_dir.glob("*.json") if p != platform.state_file]
    assert journals[0]["status"] == "failed"
    assert not (platform.state_dir / "operation.lock").exists()


def test_forwarding_config_and_doctor_are_non_mutating(project, monkeypatch):
    with pytest.raises(LakeError, match="podman backend"):
        initialize(project, backend="docker", podman_forwarding="ssh")
    assert not (project / ".env").exists()
    initialize(project, data_root=Path("data"), backend="podman", podman_forwarding="ssh", podman_machine="work-vm")
    with pytest.raises(LakeError, match="forwarding mode differs"):
        initialize(project, podman_forwarding="native")
    platform = Platform(project)
    fake_runtime(platform, monkeypatch)
    monkeypatch.setattr("lake_cli.platform.shutil.which", lambda _: "podman")
    monkeypatch.setattr(platform.forwarder, "_command", lambda: ["ssh"])
    assert platform.doctor()["checks"]["forwarding_prerequisites"]
    assert not platform.forwarder.directory.exists()


def test_windows_forwarding_runtime_is_local_not_external(tmp_path, monkeypatch):
    cache = tmp_path / "local cache"
    monkeypatch.setattr("lake_cli.platform._windows_host", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(cache))
    lake = tmp_path / "external lake/.data-platform"
    runtime = _forwarding_runtime_root(lake)
    assert runtime.is_relative_to(cache)
    assert not runtime.is_relative_to(lake)
    assert runtime != _forwarding_runtime_root(tmp_path / "other lake/.data-platform")


def test_podman_provider_override_and_missing_dependency(project, monkeypatch):
    initialize(project, data_root=Path("data"), backend="podman")
    monkeypatch.setattr("lake_cli.platform.shutil.which", lambda name: "podman" if name == "podman" else None)
    with pytest.raises(LakeError, match="provider not found"):
        Platform(project).status()
    monkeypatch.setenv("PODMAN_COMPOSE_PROVIDER", "/user tools/custom-compose")
    monkeypatch.setattr("lake_cli.platform.shutil.which", lambda name: name)

    def run(command, **kwargs):
        assert kwargs["env"]["PODMAN_COMPOSE_PROVIDER"] == "/user tools/custom-compose"
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("lake_cli.platform.subprocess.run", run)
    Platform(project).status()


def test_legacy_podman_provider_precedence_is_preserved(project, monkeypatch):
    initialize(project, data_root=Path("data"), backend="podman")
    lines = (project / ".env").read_text().splitlines()
    (project / ".env").write_text("\n".join(line for line in lines if not line.startswith("PODMAN_COMPOSE_PROVIDER=")))
    paths = {"podman": "/tools/podman", "docker-compose": "/tools/docker-compose", "podman-compose": "/env/podman-compose"}
    monkeypatch.setattr("lake_cli.platform.shutil.which", lambda name: paths.get(name, name if name.startswith("/tools/") else None))

    def run(command, **kwargs):
        assert kwargs["env"]["PODMAN_COMPOSE_PROVIDER"] == "/tools/docker-compose"
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("lake_cli.platform.subprocess.run", run)
    Platform(project).status()
