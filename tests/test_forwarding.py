import json
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from lake_cli.errors import LakeError
from lake_cli.forwarding import Forwarding, _background_options


@pytest.fixture
def forwarding(tmp_path, monkeypatch):
    probes = [socket.socket() for _ in range(4)]
    for probe in probes:
        probe.bind(("127.0.0.1", 0))
    ports = [p.getsockname()[1] for p in probes]
    for probe in probes:
        probe.close()
    manager = Forwarding(tmp_path / "state with spaces", "podman-machine-default", ports)
    helper = Path(__file__).parent / "helpers/forwarding_listener.py"
    monkeypatch.setattr(manager, "_command", lambda: [sys.executable, str(helper), *map(str, ports)])
    yield manager
    if manager.state_path.exists():
        try:
            state = json.loads(manager.state_path.read_text())
        except ValueError:
            state = {}
        if state.get("status") in ("running", "starting") and time.time() - state.get("heartbeat", 0) < 10:
            manager.stop()


def test_real_detached_supervisor_reuse_shutdown_and_restart(forwarding, monkeypatch):
    actual_popen = subprocess.Popen
    monkeypatch.setenv("POSTGRES_PASSWORD", "must-not-reach-supervisor")

    def launch(command, **kwargs):
        assert "POSTGRES_PASSWORD" not in kwargs["env"]
        assert "POLARIS_ROOT_CLIENT_SECRET" not in kwargs["env"]
        return actual_popen(command, **kwargs)

    monkeypatch.setattr("lake_cli.forwarding.subprocess.Popen", launch)
    assert forwarding.ensure()["status"] == "running"
    first = json.loads(forwarding.state_path.read_text())
    assert forwarding.ensure()["status"] == "running"
    assert json.loads(forwarding.state_path.read_text())["token"] == first["token"]
    # A new CLI instance requests shutdown; it has no Popen handle or PID kill.
    independent = Forwarding(forwarding.directory.parent, forwarding.machine, forwarding.ports)
    assert independent.stop()["status"] == "stopped"
    assert not list(forwarding.directory.glob(first["token"] + ".json"))
    for port in forwarding.ports:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    assert forwarding.ensure()["status"] == "running"
    assert json.loads(forwarding.state_path.read_text())["token"] != first["token"]


def test_real_child_failure_can_retry(forwarding, monkeypatch):
    original = forwarding._command
    monkeypatch.setattr(forwarding, "_command", lambda: [sys.executable, "-c", "raise SystemExit(1)"])
    with pytest.raises(LakeError, match="SSH forwarding failed"):
        forwarding.ensure()
    assert forwarding.status()["status"] == "failed"
    assert forwarding.status()["ssh_exit_code"] == 1
    log = Path(forwarding.status()["log"]).read_text()
    assert "Python supervisor entered" in log
    assert "SSH exited with code 1" in log
    monkeypatch.setattr(forwarding, "_command", original)
    assert forwarding.ensure()["status"] == "running"


def test_child_launch_exception_is_not_silently_discarded(forwarding, monkeypatch):
    missing = str(forwarding.directory.parent / "missing-ssh-executable")
    monkeypatch.setattr(forwarding, "_command", lambda: [missing])
    with pytest.raises(LakeError, match="supervisor and SSH diagnostics"):
        forwarding.ensure()
    status = forwarding.status()
    assert status["status"] == "failed"
    assert "FileNotFoundError" in status["error"]
    assert "Starting SSH child" in Path(status["log"]).read_text()
    assert "FileNotFoundError" in Path(status["log"]).read_text()


def test_interpreter_startup_stderr_is_captured(forwarding, monkeypatch):
    actual_popen = subprocess.Popen

    def fail_interpreter(command, **kwargs):
        return actual_popen([sys.executable, "-c",
                            "import sys; sys.stderr.write('simulated interpreter startup failure'); sys.exit(23)"],
                           **kwargs)

    monkeypatch.setattr("lake_cli.forwarding.subprocess.Popen", fail_interpreter)
    with pytest.raises(LakeError, match="forwarding failed"):
        forwarding.ensure()
    log = Path(forwarding.status()["log"]).read_text()
    assert "simulated interpreter startup failure" in log
    assert "supervisor exit code: 23" in log
    # No SSH child was launched. Restore this test's manufactured stale record.
    state = json.loads(forwarding.state_path.read_text())
    state["status"] = "failed"
    forwarding.state_path.write_text(json.dumps(state))


def test_manual_listener_not_adopted(forwarding):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", forwarding.ports[0]))
        listener.listen()
        with pytest.raises(LakeError, match="occupied"):
            forwarding.ensure()
        assert not forwarding.state_path.exists()


def test_changed_configuration_requires_shutdown(forwarding, monkeypatch):
    forwarding.ensure()
    command = forwarding._command()
    monkeypatch.setattr(forwarding, "_command", lambda: [*command, "changed"])
    with pytest.raises(LakeError, match="configuration changed"):
        forwarding.ensure()


def test_stale_state_is_not_overwritten_or_pid_killed(forwarding):
    forwarding.directory.mkdir(parents=True)
    previous = {"status": "running", "heartbeat": 0, "ssh_pid": 1, "token": "old"}
    forwarding.state_path.write_text(json.dumps(previous))
    with pytest.raises(LakeError, match="stale"):
        forwarding.ensure()
    assert json.loads(forwarding.state_path.read_text()) == previous
    assert forwarding.status()["status"] == "stale"


def test_stop_token_only_stops_its_launch(forwarding):
    forwarding.ensure()
    forwarding.stop_path.write_text(json.dumps({"token": "not-this-launch"}))
    time.sleep(0.3)
    assert forwarding.status()["status"] == "running"
    assert forwarding.stop()["status"] == "stopped"


def test_command_inspection_local_binding_and_security(tmp_path, monkeypatch):
    identity = tmp_path / "identity with spaces"
    identity.write_text("not a real key")
    manager = Forwarding(tmp_path, "work-machine", [18181, 18182, 19000, 19001])
    for key in ("CONTAINER_HOST", "CONTAINER_CONNECTION"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("lake_cli.forwarding.shutil.which", lambda name: name)
    inspection = [{"SSHConfig": {"Port": 50222, "RemoteUsername": "user", "IdentityPath": str(identity)}}]
    connections = [{"Name": "work", "Default": True, "URI": "ssh://user@127.0.0.1:50222/run/user/1000/podman/podman.sock"}]

    def run(command, **kwargs):
        assert kwargs["timeout"] == 10
        payload = inspection if command[1] == "machine" else connections
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("lake_cli.forwarding.subprocess.run", run)
    command = manager._command()
    assert "BatchMode=yes" in command
    assert "StrictHostKeyChecking=yes" in command
    assert "ExitOnForwardFailure=yes" in command
    assert str(identity) in command
    assert command[-1] == "user@127.0.0.1"
    assert [command[i + 1] for i, value in enumerate(command) if value == "-L"] == [
        f"127.0.0.1:{p}:127.0.0.1:{p}" for p in manager.ports]
    connections[0]["URI"] = "ssh://user@127.0.0.1:50223/run/user/1000/podman/podman.sock"
    with pytest.raises(LakeError, match="matching Podman"):
        manager._command()


def test_missing_ssh_and_bad_inspection(tmp_path, monkeypatch):
    manager = Forwarding(tmp_path, "machine", [8181])
    monkeypatch.setattr("lake_cli.forwarding.shutil.which", lambda _: None)
    with pytest.raises(LakeError, match="OpenSSH"):
        manager._command()
    monkeypatch.setattr("lake_cli.forwarding.shutil.which", lambda name: name)
    monkeypatch.setattr("lake_cli.forwarding.subprocess.run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, "invalid json", ""))
    with pytest.raises(LakeError, match="inspect"):
        manager._command()


def test_corrupt_state_is_not_replaced(forwarding):
    forwarding.directory.mkdir(parents=True)
    forwarding.state_path.write_text("not json")
    with pytest.raises(LakeError, match="unreadable"):
        forwarding.ensure()
    assert forwarding.state_path.read_text() == "not json"


def test_windows_detachment_flags(monkeypatch):
    monkeypatch.setattr("lake_cli.forwarding.os.name", "nt")
    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 8, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    assert _background_options() == {"creationflags": 520}
