from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BATCH_FILES = (
    PROJECT_ROOT / "setup_classpilot.bat",
    PROJECT_ROOT / "start_classpilot.bat",
    PROJECT_ROOT / "stop_classpilot.bat",
    PROJECT_ROOT / "scripts" / "classpilot-conda.bat",
)


def test_batch_entry_files_are_ascii_crlf_without_bom_or_controls() -> None:
    for path in BATCH_FILES:
        content = path.read_bytes()

        assert not content.startswith((b"\xef\xbb\xbf", b"\xff\xfe")), path
        assert b"\x00" not in content, path
        assert all(byte < 128 for byte in content), path
        assert b"\r\n" in content, path
        assert b"\n" not in content.replace(b"\r\n", b""), path
        assert b"\r" not in content.replace(b"\r\n", b""), path


def _run_launcher_probe(work_dir: Path, probe: str) -> str:
    launcher_path = PROJECT_ROOT / "scripts" / "classpilot.py"
    code = f"""
import importlib.util
import os
import sys
from pathlib import Path

launcher_path = Path({str(launcher_path)!r})
spec = importlib.util.spec_from_file_location("classpilot_launcher", launcher_path)
launcher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = launcher
spec.loader.exec_module(launcher)
os.chdir({str(work_dir)!r})
assert Path.cwd() == Path({str(work_dir)!r})
{probe}
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=work_dir,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result.stdout


def test_launcher_builds_real_locked_uvicorn_config_from_outside_project() -> None:
    output = _run_launcher_probe(
        PROJECT_ROOT.parent,
        """
import uvicorn
config = launcher.create_uvicorn_config()
assert isinstance(config, uvicorn.Config)
assert config.app == "app.main:app"
print("CONFIG_CONSTRUCTED")
""",
    )

    assert "CONFIG_CONSTRUCTED" in output


def test_launcher_loads_app_using_backend_path_from_its_own_location() -> None:
    output = _run_launcher_probe(
        PROJECT_ROOT.parent,
        """
import uvicorn
config = launcher.create_uvicorn_config()
assert str(launcher.BACKEND_ROOT) in sys.path
config.load()
assert config.loaded_app is not None
print("APP_LOADED_FROM_BACKEND")
""",
    )

    assert "APP_LOADED_FROM_BACKEND" in output


def test_launcher_explains_schema_mismatch_without_exposing_details() -> None:
    output = _run_launcher_probe(
        PROJECT_ROOT.parent,
        """
message = launcher.readiness_failure_message({
    'X-ClassPilot-Readiness-Reason': 'schema_mismatch',
})
assert '20261019_0004' in message
assert '不會自動執行 migration' in message
assert 'password' not in message.lower()
print('SAFE_SCHEMA_DIAGNOSIS')
""",
    )

    assert "SAFE_SCHEMA_DIAGNOSIS" in output
