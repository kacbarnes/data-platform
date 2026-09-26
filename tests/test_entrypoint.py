import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def test_module_entrypoint_help(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "lake_cli", "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage: lake" in result.stdout
    assert "datasets" in result.stdout


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="Bash compatibility wrapper is for Linux/WSL",
)
@pytest.mark.parametrize(
    ("arguments", "expected_code"),
    [
        (["--help"], 0),
        (["--not-a-real-option"], 2),
    ],
)
def test_compatibility_launcher(tmp_path, arguments, expected_code):
    repository = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["DATA_PLATFORM_PYTHON"] = sys.executable
    result = subprocess.run(
        [
            shutil.which("bash"),
            str(repository / "scripts" / "lake"),
            *arguments,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == expected_code, result.stderr
    if expected_code == 0:
        assert "usage: lake" in result.stdout
    else:
        assert "error:" in result.stderr
