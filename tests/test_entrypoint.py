import subprocess
import sys


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
