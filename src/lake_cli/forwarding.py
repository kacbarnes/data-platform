"""Opt-in localhost SSH forwarding with an independently owned supervisor.

No PID-based process killing: shutdown requests carry a per-launch token, and
only the supervisor holding the SSH child's Popen handle terminates that child.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

from .errors import LakeError


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (OSError, ValueError) as exc:
        raise LakeError("forwarding state is unreadable; review it before retrying") from exc


def _write(path: Path, value: dict) -> None:
    from .platform import _atomic_json
    _atomic_json(path, value)


def _diagnostic(path: Path, message: str) -> None:
    """Record lifecycle diagnostics without dumping commands, state or keys."""
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"[forwarding] {message}\n")
    except OSError:
        pass  # Preserve the original failure if the log itself is inaccessible.


def _background_options() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _fresh(state: dict) -> bool:
    try:
        return -5 <= time.time() - float(state["heartbeat"]) < 10
    except (KeyError, TypeError, ValueError):
        return False


def _listening(ports: list[int]) -> bool:
    for port in ports:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                pass
        except OSError:
            return False
    return True


class Forwarding:
    def __init__(self, state_dir: Path, machine: str, ports: list[int]):
        self.directory = state_dir / "forwarding"
        self.state_path = self.directory / "state.json"
        self.stop_path = self.directory / "stop.json"
        self.machine = machine
        self.ports = ports

    def _command(self) -> list[str]:
        podman = shutil.which("podman")
        ssh = shutil.which("ssh")
        if not podman or not ssh:
            raise LakeError("managed forwarding requires podman and OpenSSH ssh on PATH")
        try:
            result = subprocess.run(
                [podman, "machine", "inspect", self.machine], capture_output=True,
                text=True, encoding="utf-8", errors="replace", check=False, timeout=10,
            )
            if result.returncode:
                raise ValueError("inspection failed")
            config = json.loads(result.stdout)[0]["SSHConfig"]
            port = int(config["Port"])
            user = config["RemoteUsername"]
            identity = Path(config["IdentityPath"]).expanduser()
            if not 1 <= port <= 65535 or not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*", user):
                raise ValueError("invalid SSH address")
            if not identity.is_absolute() or not identity.is_file():
                raise ValueError("missing SSH identity")
            # The tunnel and Compose must refer to the same VM, not just two
            # machines that happen to expose similarly healthy services.
            if os.environ.get("CONTAINER_HOST"):
                uri = os.environ["CONTAINER_HOST"]
            else:
                connections = subprocess.run(
                    [podman, "system", "connection", "list", "--format", "json"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    check=False, timeout=10,
                )
                if connections.returncode:
                    raise ValueError("connection inspection failed")
                choices = json.loads(connections.stdout)
                name = os.environ.get("CONTAINER_CONNECTION")
                active = next(c for c in choices if c["Name"] == name) if name else next(
                    c for c in choices if c.get("Default") is True
                )
                uri = active["URI"]
            address = urlsplit(uri)
            if address.scheme != "ssh" or address.hostname not in ("localhost", "127.0.0.1") or address.port != port:
                raise ValueError("active connection is a different machine")
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError, TypeError, StopIteration) as exc:
            raise LakeError("cannot inspect matching Podman machine SSH configuration; start the machine and select its Podman connection") from exc
        command = [ssh, "-N", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                   "-o", "IdentitiesOnly=yes",
                   "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=10",
                   "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                   "-i", str(identity), "-p", str(port)]
        for host_port in self.ports:
            command.extend(["-L", f"127.0.0.1:{host_port}:127.0.0.1:{host_port}"])
        command.append(f"{user}@127.0.0.1")
        return command

    def status(self) -> dict:
        if not self.state_path.exists():
            return {"status": "stopped"}
        state = _read(self.state_path)
        return {"status": state.get("status", "unknown") if _fresh(state) or
                state.get("status") in ("stopped", "failed") else "stale",
                "machine": state.get("machine"), "ports": state.get("ports"),
                "log": state.get("log"), "error": state.get("error"),
                "ssh_exit_code": state.get("ssh_exit_code")}

    def ensure(self) -> dict:
        command = self._command()
        signature = hashlib.sha256(json.dumps(command).encode()).hexdigest()
        if self.state_path.exists():
            previous = _read(self.state_path)
            if previous.get("status") in ("starting", "running"):
                if not _fresh(previous):
                    raise LakeError("managed tunnel has stale state; verify its processes before recovery (no PID will be killed)")
                if previous.get("signature") != signature:
                    raise LakeError("forwarding configuration changed; run platform down with the original configuration first")
                if previous.get("status") == "running" and _listening(self.ports):
                    return self.status()
                raise LakeError("managed forwarding is not ready; retry or inspect its SSH log")
        # Never adopt a manually created tunnel or unrelated listener.
        for port in self.ports:
            with socket.socket() as probe:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError as exc:
                    raise LakeError(f"forwarding port {port} is occupied; close the temporary tunnel or use native forwarding") from exc
        self.directory.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        request = self.directory / f"{token}.json"
        log = self.directory / f"{token}.log"
        from .platform import _write_private
        state = {"status": "starting", "heartbeat": time.time(), "token": token,
                 "signature": signature, "machine": self.machine, "ports": self.ports,
                 "log": str(log), "command": command}
        _write_private(request, json.dumps(state))
        _write_private(log, "")
        _write(self.state_path, state)
        # Do not pass lakehouse credentials to the long-lived supervisor/SSH.
        environment = {key: value for key, value in os.environ.items() if key in (
            "PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "LANG", "LC_ALL",
        )}
        _diagnostic(log, "Launching detached Python supervisor")
        try:
            # Capture interpreter/module/DLL startup failures too, not only SSH.
            with log.open("ab") as output:
                child = subprocess.Popen(
                    [sys.executable, "-m", "lake_cli.forwarding", "--serve", str(request)],
                    stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                    env=environment, close_fds=True, **_background_options(),
                )
        except OSError as exc:
            _diagnostic(log, f"Supervisor launch failed: {type(exc).__name__}: {exc}")
            state.update(status="failed", heartbeat=time.time())
            _write(self.state_path, state)
            raise LakeError("could not launch forwarding supervisor") from exc
        deadline = time.monotonic() + 20
        try:
            while time.monotonic() < deadline:
                state = _read(self.state_path)
                if state.get("status") == "running":
                    return self.status()
                if state.get("status") == "failed" or child.poll() is not None:
                    _diagnostic(log, f"Startup failed; supervisor exit code: {child.poll()}")
                    raise LakeError(f"SSH forwarding failed; inspect {log} for supervisor and SSH diagnostics")
                time.sleep(0.1)
            raise LakeError(f"SSH forwarding startup timed out; inspect {log}")
        except BaseException:
            _write(self.stop_path, {"token": token})
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass  # Never kill a supervisor whose child may still need cleanup.
            raise

    def stop(self) -> dict:
        if not self.state_path.exists():
            return {"status": "stopped"}
        state = _read(self.state_path)
        if state.get("status") in ("stopped", "failed"):
            return self.status()
        if not isinstance(state.get("token"), str):
            raise LakeError("invalid forwarding ownership token; review state before recovery")
        _write(self.stop_path, {"token": state["token"]})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            latest = _read(self.state_path)
            if latest.get("token") != state["token"]:
                raise LakeError("forwarding ownership changed during shutdown")
            if latest.get("status") in ("stopped", "failed"):
                return self.status()
            time.sleep(0.1)
        raise LakeError("tunnel supervisor did not acknowledge shutdown; no unrelated process was killed")


def serve(request: Path) -> int:
    _diagnostic(request.with_suffix(".log"), "Python supervisor entered")
    state = _read(request)
    directory = request.parent
    state_path, stop_path = directory / "state.json", directory / "stop.json"
    child = None
    stopped = False
    try:
        _diagnostic(Path(state["log"]), "Starting SSH child")
        with Path(state["log"]).open("ab") as log:
            child = subprocess.Popen(state["command"], stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=log, close_fds=True,
                                     **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
            state.update(supervisor_pid=os.getpid(), ssh_pid=child.pid)
            deadline = time.monotonic() + 15
            last_write = 0.0
            while child.poll() is None:
                if stop_path.exists() and _read(stop_path).get("token") == state["token"]:
                    stopped = True
                    break
                if state["status"] == "starting":
                    if _listening(state["ports"]):
                        state["status"] = "running"
                    elif time.monotonic() > deadline:
                        break
                state["heartbeat"] = time.time()
                if time.monotonic() - last_write >= 1:
                    _write(state_path, state)
                    last_write = time.monotonic()
                time.sleep(0.2)
            state["ssh_exit_code"] = child.poll()
            if not stopped:
                state["error"] = (f"SSH exited with code {child.poll()}" if child.poll() is not None
                                  else "SSH did not open all forwarding ports before its startup deadline")
    except Exception as exc:
        state["error"] = f"Supervisor failure: {type(exc).__name__}: {exc}"
        _diagnostic(Path(state["log"]), state["error"])
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()  # Owned Popen handle, never a PID read from disk.
                child.wait()
        _diagnostic(Path(state["log"]), "Stopped" if stopped else state.get("error", "Supervisor failed"))
        state.update(status="stopped" if stopped else "failed", heartbeat=time.time())
        _write(state_path, state)
        request.unlink(missing_ok=True)
    return 0 if stopped else 1


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--serve":
        raise SystemExit("internal forwarding supervisor: use lake platform up")
    raise SystemExit(serve(Path(sys.argv[2])))
