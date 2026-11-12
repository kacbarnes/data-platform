import json
from pathlib import Path

import pytest

from lake_cli import local_state


def conflict(code=32):
    error = OSError("sharing conflict")
    error.winerror = code
    return error


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(local_state, "_windows_host", lambda: True)
    sleeps = []
    monkeypatch.setattr(local_state.time, "sleep", sleeps.append)
    return sleeps


@pytest.mark.parametrize("code", [5, 32, 33])
def test_read_retries_transient_conflicts(tmp_path, monkeypatch, windows, code):
    path = tmp_path / "state.json"
    path.write_text('{"status": "running"}')
    original = Path.read_text
    attempts = []

    def read(selected, **kwargs):
        attempts.append(selected)
        if len(attempts) < 3:
            raise conflict(code)
        return original(selected, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert local_state.read_json(path) == {"status": "running"}
    assert len(windows) == 2


@pytest.mark.parametrize("persistent", [False, True])
def test_replace_retries_preserves_old_state_and_cleans_temp(tmp_path, monkeypatch, windows, persistent):
    path = tmp_path / "state.json"
    path.write_text('{"old": true}')
    original = local_state.os.replace
    calls = []

    def replace(source, destination):
        assert json.loads(path.read_text()) == {"old": True}
        calls.append(source)
        if persistent or len(calls) < 3:
            raise conflict()
        return original(source, destination)

    monkeypatch.setattr(local_state.os, "replace", replace)
    if persistent:
        with pytest.raises(OSError):
            local_state.atomic_json(path, {"new": True})
        assert local_state.read_json(path) == {"old": True}
        assert len(calls) == 6
    else:
        local_state.atomic_json(path, {"new": True})
        assert local_state.read_json(path) == {"new": True}
    assert list(tmp_path.iterdir()) == [path]


def test_corrupt_missing_and_unrelated_errors_are_not_retried(tmp_path, windows):
    path = tmp_path / "state.json"
    with pytest.raises(FileNotFoundError):
        local_state.read_json(path)
    path.write_text("invalid")
    with pytest.raises(json.JSONDecodeError):
        local_state.read_json(path)
    with pytest.raises(OSError):
        local_state._retry_sharing(lambda: (_ for _ in ()).throw(conflict(87)))
    assert not windows


def test_read_retry_is_bounded(tmp_path, monkeypatch, windows):
    def read(*args, **kwargs):
        raise conflict()
    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(OSError):
        local_state.read_json(tmp_path / "state.json")
    assert len(windows) == 5


def test_posix_does_not_retry_windows_errors(monkeypatch):
    monkeypatch.setattr(local_state, "_windows_host", lambda: False)
    calls = []
    def operation():
        calls.append(1)
        raise conflict()
    with pytest.raises(OSError):
        local_state._retry_sharing(operation)
    assert len(calls) == 1
