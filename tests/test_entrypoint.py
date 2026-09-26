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


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="Bash installer is for Linux/WSL",
)
@pytest.mark.parametrize("install_exit_code", [0, 7])
def test_installer(tmp_path, install_exit_code):
    repository = Path(__file__).resolve().parents[1]

    checkout = tmp_path / "checkout with spaces"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    installer = scripts / "install-cli"
    shutil.copyfile(repository / "scripts" / "install-cli", installer)

    fake_python = tmp_path / "fake python"
    fake_python.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$INSTALL_TEST_ARGUMENTS"\n'
        'exit "$INSTALL_TEST_EXIT_CODE"\n'
    )

    fake_python.chmod(0o700)

    arguments_file = tmp_path / "arguments.txt"
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    environment = os.environ.copy()
    environment.update(
        {
            "DATA_PLATFORM_PYTHON": str(fake_python),
            "INSTALL_TEST_ARGUMENTS": str(arguments_file),
            "INSTALL_TEST_EXIT_CODE": str(install_exit_code),
            "HOME": str(fake_home),
        }
    )

    result = subprocess.run(
        [shutil.which("bash"), str(installer)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == install_exit_code, result.stderr
    assert arguments_file.read_text().splitlines() == [
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--no-build-isolation",
        "--editable",
        str(checkout.resolve()),
    ]
    assert not (fake_home / ".local").exists()
    assert ("Installed lake CLI" in result.stdout) == (install_exit_code == 0)
