import os

import pytest

from lake_cli import windows_process
from lake_cli.errors import LakeError


@pytest.mark.parametrize("flags,expected", [
    (0, False), (0x800, True), (0x2800, True), (0x3000, False), (0x3800, False),
])
def test_job_policy(flags, expected, monkeypatch):
    monkeypatch.setattr(windows_process, "current_job_flags", lambda: flags)
    assert windows_process.breakaway_required() is expected


def test_restrictive_job_fails_explicitly(monkeypatch):
    monkeypatch.setattr(windows_process, "current_job_flags", lambda: 0x2000)
    with pytest.raises(LakeError, match="does not permit breakaway"):
        windows_process.breakaway_required()


def test_inspection_failure_fails_explicitly(monkeypatch):
    def fail():
        raise OSError("denied")
    monkeypatch.setattr(windows_process, "current_job_flags", fail)
    with pytest.raises(LakeError, match="cannot inspect"):
        windows_process.breakaway_required()


@pytest.mark.skipif(os.name != "nt", reason="Windows API")
def test_native_job_inspection():
    assert isinstance(windows_process.current_job_flags(), int)
