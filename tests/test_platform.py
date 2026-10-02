import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

from lake_cli.cli import main
from lake_cli.config import Settings, _read_env
from lake_cli.errors import LakeError
from lake_cli.platform import PORTS, Platform, initialize


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "checkout with spaces"
    root.mkdir()
    repository = Path(__file__).resolve().parents[1]
    shutil.copyfile(repository / "compose.yaml", root / "compose.yaml")
    (root / "contracts").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='test'\n")
    for key in (*PORTS, "DATA_ROOT", "PLATFORM_BACKEND", "COMPOSE_PROJECT_NAME",
                "POLARIS_ROOT_CLIENT_SECRET", "POSTGRES_PASSWORD", "RUSTFS_SECRET_KEY"):
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
    assert mount["type"] == "bind"
    assert mount["target"] == "/var/lib/postgresql/data"
    assert "DATA_ROOT" in mount["source"]
    assert mount["bind"]["create_host_path"] is False


@pytest.mark.parametrize("backend", ["docker", "podman"])
def test_commands_override_ambient_compose_settings_and_redact(project, monkeypatch, backend):
    initialize(project, data_root=Path("data"), backend=backend)
    monkeypatch.setenv("COMPOSE_FILE", "unrelated-compose.yaml")
    platform = Platform(project)
    observed = []

    def run(command, **kwargs):
        observed.append(command)
        assert command[1] == "compose"
        assert "COMPOSE_FILE" not in kwargs["env"]
        assert kwargs["env"]["DATA_ROOT"] == str(project / "data")
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


def test_compose_configuration_with_real_docker(project):
    executable = shutil.which("docker")
    if executable is None:
        pytest.skip("Docker is not installed")
    initialize(project, data_root=Path("data files"))
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
