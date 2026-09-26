from pathlib import Path

import pytest

from lake_cli.config import Settings, discover_project_root, resolve_config_path
from lake_cli.errors import LakeError


def make_project(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    (path / "contracts").mkdir()
    return path


def test_explicit_project_root_wins(tmp_path):
    explicit = make_project(tmp_path / "explicit")
    configured = make_project(tmp_path / "configured")

    result = discover_project_root(
        explicit,
        environ={"DATA_PLATFORM_HOME": str(configured)},
        cwd=tmp_path,
    )

    assert result == explicit.resolve()


def test_environment_root_is_used(tmp_path):
    configured = make_project(tmp_path / "configured")

    result = discover_project_root(
        environ={"DATA_PLATFORM_HOME": str(configured)},
        cwd=tmp_path,
    )

    assert result == configured.resolve()


def test_searches_parent_directories(tmp_path):
    project = make_project(tmp_path / "project")
    nested = project / "a" / "directory with spaces"
    nested.mkdir(parents=True)

    result = discover_project_root(environ={}, cwd=nested)
    assert result == project.resolve()


def test_missing_project_root_fails(tmp_path):
    fake_package_file = tmp_path / "site-packages" / "lake_cli" / "config.py"
    with pytest.raises(LakeError, match="cannot locate"):
        discover_project_root(
            environ={},
            cwd=tmp_path,
            package_file=fake_package_file,
        )


def test_settings_environment_isolation_and_precedence(tmp_path, monkeypatch):
    project = make_project(tmp_path / "project")
    data_root = tmp_path / "data"
    control_db = tmp_path / "configured.duckdb"

    (project / ".env").write_text(
        "\n".join(
            [
                f'DATA_ROOT="{data_root}"',
                f'CONTROL_DB="{control_db}"',
                "POLARIS_CATALOG=file_catalog",
                "POLARIS_ROOT_CLIENT_ID=test_client",
                "POLARIS_ROOT_CLIENT_SECRET=test_secret",
                "RUSTFS_ACCESS_KEY=test_access",
                "RUSTFS_SECRET_KEY=test_secret",
            ]
        )
        + "\n"
    )

    monkeypatch.setenv("POLARIS_CATALOG", "unwanted_shell_catalog")

    settings = Settings.load(project, environ={})

    assert settings.catalog == "file_catalog"
    assert settings.data_root == data_root.resolve()
    assert settings.control_db == control_db.resolve()
    assert settings.polaris_url == "http://localhost:8181"

    overridden = Settings.load(
        environ={
            "DATA_PLATFORM_HOME": str(project),
            "POLARIS_CATALOG": "environment_catalog",
        }
    )

    assert overridden.project_root == project.resolve()
    assert overridden.catalog == "environment_catalog"
    assert overridden.client_id == "test_client"


@pytest.mark.parametrize(
    "control_path",
    [None, "state files/control.duckdb"],
)
def test_relative_settings_paths_use_project_root(tmp_path, monkeypatch, control_path):
    project = make_project(tmp_path / "project with spaces")
    other_directory = tmp_path / "somewhere else"
    other_directory.mkdir()

    lines = [
        'DATA_ROOT="data files"',
        "POLARIS_CATALOG=test_catalog",
        "POLARIS_ROOT_CLIENT_ID=test_client",
        "POLARIS_ROOT_CLIENT_SECRET=test_secret",
        "RUSTFS_ACCESS_KEY=test_access",
        "RUSTFS_SECRET_KEY=test_secret",
    ]
    if control_path is not None:
        lines.append(f'CONTROL_DB="{control_path}"')

    (project / ".env").write_text("\n".join(lines) + "\n")

    monkeypatch.chdir(project)
    first = Settings.load(project, environ={})

    monkeypatch.chdir(other_directory)
    second = Settings.load(project, environ={})

    expected_data = (project / "data files").resolve()
    expected_control = (
        expected_data / "Databases" / "duckdb" / "control.duckdb"
        if control_path is None
        else (project / control_path).resolve()
    )

    assert first.data_root == second.data_root == expected_data
    assert first.control_db == second.control_db == expected_control


def test_config_path_expands_home(tmp_path, monkeypatch):
    fake_home = tmp_path / "home directory"
    project = tmp_path / "project"

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    result = resolve_config_path("~/data files", base=project)

    assert result == (fake_home / "data files").resolve()
