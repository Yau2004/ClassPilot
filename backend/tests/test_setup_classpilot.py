from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup_classpilot.py"
SPEC = importlib.util.spec_from_file_location("classpilot_setup", SETUP_SCRIPT)
setup = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(setup)


def completed(args, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_node_supported_ranges_match_vite_prerequisites() -> None:
    assert setup.node_version_supported((20, 19, 0))
    assert setup.node_version_supported((22, 12, 0))
    assert setup.node_version_supported((24, 0, 0))
    assert not setup.node_version_supported((20, 18, 9))
    assert not setup.node_version_supported((21, 99, 0))


def test_npm_cmd_is_invoked_via_node_without_a_shell(tmp_path: Path) -> None:
    node = tmp_path / "Node Folder" / "node.exe"
    npm = tmp_path / "Node Folder" / "npm.cmd"
    cli = tmp_path / "Node Folder" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    cli.parent.mkdir(parents=True)
    cli.touch()

    assert setup.npm_prefix(str(node), str(npm)) == (str(node), str(cli))


def test_conda_batch_command_quotes_unicode_and_space_paths() -> None:
    command = setup.windows_batch_command(
        [r"D:\Apps 中文\condabin\conda.bat", "run", "-n", "base"]
    )
    assert command == 'call "D:\\Apps 中文\\condabin\\conda.bat" run -n base'


def test_real_runner_uses_cmd_wrapper_for_conda_batch(monkeypatch, tmp_path: Path) -> None:
    seen = {}

    def runner(command, **kwargs):
        seen["command"] = command
        seen.update(kwargs)
        return completed(command)

    monkeypatch.setattr(setup.subprocess, "run", runner)
    setup._run(
        runner,
        (tmp_path / "Conda 中文" / "conda.bat", "env", "list", "--json"),
        step="test",
    )

    assert seen["command"].startswith('call "')
    assert seen["shell"] is True


def test_read_only_database_check_reports_required_schema_revision() -> None:
    script_path = PROJECT_ROOT / "scripts" / "check_db.py"
    check_spec = importlib.util.spec_from_file_location("classpilot_db_check", script_path)
    check = importlib.util.module_from_spec(check_spec)
    assert check_spec and check_spec.loader
    check_spec.loader.exec_module(check)

    assert check.schema_status("20261019_0004") == (
        True,
        "schema revision=20261019_0004",
    )
    assert check.schema_status(None) == (
        False,
        "schema revision mismatch: current=missing, required=20261019_0004",
    )


def test_read_only_database_check_does_not_print_exception_secrets(
    monkeypatch, capsys
) -> None:
    from sqlalchemy.exc import OperationalError
    from pydantic import SecretStr

    script_path = PROJECT_ROOT / "scripts" / "check_db.py"
    check_spec = importlib.util.spec_from_file_location("classpilot_db_check_secret", script_path)
    check = importlib.util.module_from_spec(check_spec)
    assert check_spec and check_spec.loader
    check_spec.loader.exec_module(check)
    sentinel = "SENTINEL-CHECK-DB-SECRET"

    class SettingsStub:
        postgres_password = SecretStr("configured")
        postgres_db = "steam_classroom_dev"
        postgres_test_db = "steam_classroom_test"

    class EngineStub:
        def connect(self):
            raise OperationalError("SELECT 1", {"password": sentinel}, Exception(sentinel))

        def dispose(self):
            pass

    monkeypatch.setattr(check, "Settings", SettingsStub)
    monkeypatch.setattr(check, "create_database_engine", lambda *_: EngineStub())
    monkeypatch.setattr(sys, "argv", [str(script_path), "dev"])

    assert check.main() == 1
    output = capsys.readouterr().err
    assert "OperationalError" in output
    assert "Confirm PostgreSQL" in output
    assert sentinel not in output


def test_read_only_database_check_stops_before_connect_with_template_password(
    monkeypatch, capsys
) -> None:
    from pydantic import SecretStr

    script_path = PROJECT_ROOT / "scripts" / "check_db.py"
    check_spec = importlib.util.spec_from_file_location("classpilot_db_check_template", script_path)
    check = importlib.util.module_from_spec(check_spec)
    assert check_spec and check_spec.loader
    check_spec.loader.exec_module(check)

    class SettingsStub:
        postgres_password = SecretStr("CHANGE_ME_WITH_YOUR_LOCAL_PASSWORD")

    monkeypatch.setattr(check, "Settings", SettingsStub)
    monkeypatch.setattr(
        check,
        "create_database_engine",
        lambda *_: pytest.fail("Template configuration must not connect to PostgreSQL."),
    )
    monkeypatch.setattr(sys, "argv", [str(script_path), "dev"])

    assert check.main() == 3
    output = capsys.readouterr().err
    assert "POSTGRES_PASSWORD is not configured" in output
    assert "CHANGE_ME_WITH_YOUR_LOCAL_PASSWORD" not in output


def test_conda_env_detection_is_name_based_and_case_insensitive(tmp_path: Path) -> None:
    target = tmp_path / "envs" / "Steam-Classroom"
    assert setup.find_target_environment([tmp_path / "base", target]) == target
    assert setup.find_target_environment([tmp_path / "base"]) is None


def test_existing_environment_must_run_python_312() -> None:
    def runner(args, **kwargs):
        return completed(args, stdout="3.11\n")

    with pytest.raises(setup.SetupError, match="3.12"):
        setup._verify_existing_environment(runner, Path("conda.exe"), Path("env"))


def test_env_template_is_created_only_when_env_is_missing(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("POSTGRES_PASSWORD=template\n", encoding="utf-8")
    assert setup.ensure_env_file(tmp_path)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "POSTGRES_PASSWORD=template\n"

    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=local-only\n", encoding="utf-8")
    assert not setup.ensure_env_file(tmp_path)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "POSTGRES_PASSWORD=local-only\n"


def test_failed_dependency_process_hides_raw_output_secret() -> None:
    sentinel = "SENTINEL-DO-NOT-PRINT"

    def runner(args, **kwargs):
        return completed(args, stderr=f"private package token {sentinel}", returncode=1)

    with pytest.raises(setup.SetupError) as error:
        setup._run(runner, ["npm", "ci"])
    assert sentinel not in str(error.value)


def test_unicode_space_project_path_and_existing_env_stop_before_changes(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = tmp_path / "ClassPilot 中文 Project"
    conda_root = tmp_path / "Conda Place"
    (conda_root / "condabin").mkdir(parents=True)
    conda_bat = conda_root / "condabin" / "conda.bat"
    conda_bat.touch()
    (conda_root / "Scripts").mkdir()
    conda_exe = conda_root / "Scripts" / "conda.exe"
    conda_exe.touch()
    env_prefix = conda_root / "envs" / "steam-classroom"
    node = tmp_path / "Node Place" / "node.exe"
    npm = tmp_path / "Node Place" / "npm.cmd"
    node.parent.mkdir()
    node.touch()
    npm.touch()
    npm_cli = npm.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    npm_cli.parent.mkdir(parents=True)
    npm_cli.touch()
    root.mkdir()
    (root / ".env").write_text("POSTGRES_PASSWORD=keep-me\n", encoding="utf-8")
    monkeypatch.setenv("CLASSPILOT_CONDA_BAT", str(conda_bat))
    monkeypatch.setattr(setup.shutil, "which", lambda name: str(node if name == "node" else npm))
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        joined = " ".join(args)
        if args[-1:] == ["--version"] and "npm-cli.js" in joined:
            return completed(args, stdout="10.9.0\n")
        if args[-1:] == ["--version"] and "node.exe" in joined:
            return completed(args, stdout="v22.12.0\n")
        if args[-1:] == ["--version"]:
            return completed(args, stdout="conda 25.1.0\n")
        if "env" in args and "list" in args:
            return completed(args, stdout=json.dumps({"envs": [str(env_prefix)]}))
        if "python" in args and "-c" in args:
            return completed(args, stdout="3.12\n")
        pytest.fail(f"Unexpected setup mutation/probe: {args}")

    result = setup.main(root, runner=runner, input_fn=lambda _: "n")

    assert result == 0
    assert "Node.js" in capsys.readouterr().out
    assert (root / ".env").read_text(encoding="utf-8") == "POSTGRES_PASSWORD=keep-me\n"
    assert not any("install" in call or "create" in call or "npm" in call for call in calls)


def test_missing_environment_setup_failure_is_safe_and_rerunnable(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = tmp_path / "ClassPilot 中文 Space"
    root.mkdir()
    (root / ".env.example").write_text(
        "POSTGRES_PASSWORD=CHANGE_ME_WITH_YOUR_LOCAL_PASSWORD\n", encoding="utf-8"
    )
    conda_root = tmp_path / "Conda"
    conda_bat = conda_root / "condabin" / "conda.bat"
    conda_bat.parent.mkdir(parents=True)
    conda_bat.touch()
    conda_exe = conda_root / "Scripts" / "conda.exe"
    conda_exe.parent.mkdir()
    conda_exe.touch()
    env_prefix = conda_root / "envs" / "steam-classroom"
    node = tmp_path / "Node" / "node.exe"
    npm = tmp_path / "Node" / "npm.cmd"
    npm.parent.mkdir()
    node.touch()
    npm.touch()
    npm_cli = npm.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    npm_cli.parent.mkdir(parents=True)
    npm_cli.touch()
    monkeypatch.setenv("CLASSPILOT_CONDA_BAT", str(conda_bat))
    monkeypatch.setattr(setup.shutil, "which", lambda name: str(node if name == "node" else npm))
    state = {"created": False, "fail_npm_once": True, "create_count": 0}

    def runner(args, **kwargs):
        joined = " ".join(args)
        if args[-1:] == ["--version"] and "npm-cli.js" in joined:
            return completed(args, stdout="10.9.0\n")
        if args[-1:] == ["--version"] and "node.exe" in joined:
            return completed(args, stdout="v22.12.0\n")
        if args[-1:] == ["--version"]:
            return completed(args, stdout="conda 25.1.0\n")
        if "env" in args and "list" in args:
            envs = [str(env_prefix)] if state["created"] else []
            return completed(args, stdout=json.dumps({"envs": envs}))
        if "env" in args and "create" in args:
            state["created"] = True
            state["create_count"] += 1
            return completed(args)
        if "python" in args and "-c" in args:
            return completed(args, stdout="3.12\n")
        if "pip" in args and "install" in args:
            return completed(args)
        if "npm-cli.js" in joined and args[-1:] == ["ci"] and state["fail_npm_once"]:
            state["fail_npm_once"] = False
            return completed(args, stderr="SENTINEL-SETUP-OUTPUT", returncode=1)
        if "npm-cli.js" in joined and args[-1:] == ["ci"]:
            return completed(args)
        if "npm-cli.js" in joined and args[-2:] == ["run", "build"]:
            return completed(args)
        if "check_db.py" in joined:
            return completed(
                args,
                stderr="POSTGRES_PASSWORD is not configured. Set it in the project-root .env.",
                returncode=3,
            )
        pytest.fail(f"Unexpected setup command: {args}")

    first_result = setup.main(root, runner=runner, input_fn=lambda _: "Y")
    first_output = capsys.readouterr()
    assert first_result == 1
    assert "SENTINEL-SETUP-OUTPUT" not in first_output.out + first_output.err
    assert (root / ".env").is_file()

    second_result = setup.main(root, runner=runner, input_fn=lambda _: "Y")
    second_output = capsys.readouterr()
    assert second_result == 0
    assert "SENTINEL-SETUP-OUTPUT" not in second_output.out + second_output.err
    assert state["create_count"] == 1
    assert (root / ".env").read_text(encoding="utf-8") == (
        "POSTGRES_PASSWORD=CHANGE_ME_WITH_YOUR_LOCAL_PASSWORD\n"
    )


def test_schema_mismatch_is_reported_without_running_migration(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = tmp_path / "ClassPilot Schema"
    root.mkdir()
    sentinel = "SENTINEL-LOCAL-PASSWORD"
    (root / ".env").write_text(
        f"POSTGRES_PASSWORD={sentinel}\n", encoding="utf-8"
    )
    conda_root = tmp_path / "Conda"
    conda_bat = conda_root / "condabin" / "conda.bat"
    conda_bat.parent.mkdir(parents=True)
    conda_bat.touch()
    conda_exe = conda_root / "Scripts" / "conda.exe"
    conda_exe.parent.mkdir()
    conda_exe.touch()
    env_prefix = conda_root / "envs" / "steam-classroom"
    node = tmp_path / "Node" / "node.exe"
    npm = tmp_path / "Node" / "npm.cmd"
    npm.parent.mkdir()
    node.touch()
    npm.touch()
    npm_cli = npm.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    npm_cli.parent.mkdir(parents=True)
    npm_cli.touch()
    monkeypatch.setenv("CLASSPILOT_CONDA_BAT", str(conda_bat))
    monkeypatch.setattr(setup.shutil, "which", lambda name: str(node if name == "node" else npm))
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        joined = " ".join(args)
        if args[-1:] == ["--version"] and "npm-cli.js" in joined:
            return completed(args, stdout="10.9.0\n")
        if args[-1:] == ["--version"] and "node.exe" in joined:
            return completed(args, stdout="v22.12.0\n")
        if args[-1:] == ["--version"]:
            return completed(args, stdout="conda 25.1.0\n")
        if "env" in args and "list" in args:
            return completed(args, stdout=json.dumps({"envs": [str(env_prefix)]}))
        if "python" in args and "-c" in args:
            return completed(args, stdout="3.12\n")
        if "pip" in args and "install" in args:
            return completed(args)
        if "npm-cli.js" in joined and args[-1:] == ["ci"]:
            return completed(args)
        if "npm-cli.js" in joined and args[-2:] == ["run", "build"]:
            return completed(args)
        if "check_db.py" in joined:
            return completed(
                args,
                stderr=(
                    "Read-only database check connected: target=dev, "
                    "database=classpilot_dev; schema revision mismatch: "
                    "current=20261015_0003, required=20261019_0004. No migration was run."
                ),
                returncode=2,
            )
        pytest.fail(f"Unexpected setup command: {args}")

    result = setup.main(root, runner=runner, input_fn=lambda _: "Y")
    output = capsys.readouterr()

    assert result == 2
    assert "database=classpilot_dev" in output.err
    assert "20261019_0004" in output.err
    assert "No migration was run" in output.err
    assert sentinel not in output.out + output.err
    assert not any("alembic" in " ".join(call).casefold() for call in calls)


def test_missing_node_prerequisite_stops_before_install(monkeypatch, tmp_path: Path, capsys) -> None:
    conda_root = tmp_path / "conda"
    conda_bat = conda_root / "condabin" / "conda.bat"
    conda_bat.parent.mkdir(parents=True)
    conda_bat.touch()
    (conda_root / "Scripts").mkdir()
    (conda_root / "Scripts" / "conda.exe").touch()
    monkeypatch.setenv("CLASSPILOT_CONDA_BAT", str(conda_bat))
    monkeypatch.setattr(setup.shutil, "which", lambda _: None)
    assert setup.main(tmp_path, runner=subprocess.run, input_fn=lambda _: "Y") == 1
    assert "Node.js 或 npm" in capsys.readouterr().err

