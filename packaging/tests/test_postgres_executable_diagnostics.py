from __future__ import annotations

import ctypes
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from app.core import postgres_runtime
from app.core import runtime as runtime_module


def test_frozen_bootstrap_keeps_bundled_resources_read_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "classpilot_release.py"
    spec = importlib.util.spec_from_file_location("classpilot_release_readonly_test", script_path)
    assert spec is not None and spec.loader is not None
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    monkeypatch.setattr(release.sys, "frozen", True, raising=False)
    monkeypatch.setattr(release.sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(release.sys, "dont_write_bytecode", False)
    monkeypatch.setattr(release.sys, "path", [])

    root, backend = release._bootstrap_paths()

    assert root == tmp_path / "bundle"
    assert backend == root / "backend"
    assert str(backend) in release.sys.path
    assert release.sys.dont_write_bytecode is True


def test_windows_child_dll_search_is_reset_and_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str | None] = []

    class Kernel32:
        class Function:
            argtypes: object = None
            restype: object = None

            def __call__(self, path: str | None) -> int:
                calls.append(path)
                return 1

        SetDllDirectoryW = Function()

    monkeypatch.setattr(postgres_runtime.os, "name", "nt")
    monkeypatch.setattr(postgres_runtime.sys, "frozen", True, raising=False)
    monkeypatch.setattr(postgres_runtime.sys, "_MEIPASS", r"C:\ClassPilot\_internal", raising=False)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: Kernel32(), raising=False)

    context = postgres_runtime._ExternalWindowsProcess()
    with context:
        assert calls == [None]
    assert calls == [None, r"C:\ClassPilot\_internal"]
    assert context.diagnostic()["child_search_state_at_createprocess"] == "standard-windows-search"
    assert context.diagnostic()["set_dll_directory_restored_after_createprocess"] is True


def test_frozen_dll_search_change_refuses_concurrent_python_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = threading.Event()
    release = threading.Event()

    def wait_for_release() -> None:
        ready.set()
        release.wait(timeout=5)

    worker = threading.Thread(target=wait_for_release, daemon=True)
    worker.start()
    assert ready.wait(timeout=2)
    monkeypatch.setattr(postgres_runtime.os, "name", "nt")
    monkeypatch.setattr(postgres_runtime.sys, "frozen", True, raising=False)
    try:
        with pytest.raises(postgres_runtime.PrototypeRuntimeError, match="process-wide"):
            with postgres_runtime._ExternalWindowsProcess():
                pytest.fail("DLL search should not be changed while a worker is alive.")
    finally:
        release.set()
        worker.join(timeout=2)


def test_version_probe_runs_only_read_only_version_argument(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}

    def fake_run(
        executable: Path,
        arguments: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        observed["executable"] = executable
        observed["arguments"] = arguments
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess([str(executable), *arguments], 0, b"initdb (PostgreSQL) 18.6\n", b"")

    monkeypatch.setattr(postgres_runtime, "_execute_external_postgres_tool", fake_run)

    postgres_runtime._probe_executable_version(tmp_path / "initdb.exe", expected_version="18.6")

    assert observed["executable"] == tmp_path / "initdb.exe"
    assert observed["arguments"] == ["--version"]
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["timeout"] == 10
    assert kwargs["stage"] == "executable-preflight"


class _NoopContext:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: object) -> None:
        return None


def test_dll_not_found_error_has_safe_stage_and_both_exit_code_forms() -> None:
    error = postgres_runtime._component_failure(
        "initdb.exe", 3221225781, stage="cluster-init"
    )

    assert "cluster-init" in str(error)
    assert "initdb.exe" in str(error)
    assert "3221225781" in str(error)
    assert "0xC0000135" in str(error)
    assert "STATUS_DLL_NOT_FOUND" in str(error)


def test_probe_nonzero_reports_runtime_failure_without_child_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(
        executable: Path,
        arguments: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            [str(executable), *arguments],
            3221225781,
            b"password=sentinel-secret",
            b"password=sentinel-secret",
        )

    monkeypatch.setattr(postgres_runtime, "_execute_external_postgres_tool", fake_run)

    with pytest.raises(postgres_runtime.PrototypeRuntimeError) as caught:
        postgres_runtime._probe_executable_version(
            tmp_path / "postgres.exe", expected_version="18.6"
        )

    assert "0xC0000135" in str(caught.value)
    assert "sentinel-secret" not in str(caught.value)


def test_postgres_child_environment_excludes_developer_runtime_and_masks_path_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", r"C:\Dev\Miniconda;C:\Windows\System32")
    monkeypatch.setenv("CONDA_PREFIX", r"C:\Dev\Miniconda\envs\steam-classroom")
    monkeypatch.setenv("PYTHONPATH", "sentinel-secret")
    monkeypatch.setenv("PGPASSWORD", "sentinel-secret")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")

    env, summary = postgres_runtime._external_postgres_environment(tmp_path / "postgres bin")

    assert "CONDA_PREFIX" not in env
    assert "PYTHONPATH" not in env
    assert "PGPASSWORD" not in env
    assert str(tmp_path / "postgres bin") in env["PATH"]
    assert "Miniconda" not in env["PATH"]
    assert summary["path_values_logged"] is False
    assert {"CONDA_PREFIX", "PGPASSWORD", "PYTHONPATH"}.issubset(
        set(summary["removed_environment_key_names"])
    )


def test_exit_code_one_uses_sanitized_postgres_reason_summary() -> None:
    error = postgres_runtime._component_failure(
        "postgres.exe",
        1,
        stage="existing-cluster-start",
        output='FATAL: could not bind IPv4 address; password=sentinel-secret',
    )

    assert "1 (0x00000001)" in str(error)
    assert "port-conflict" in str(error)
    assert "sentinel-secret" not in str(error)
    assert "password=<redacted>" in str(error)


def test_process_diagnostic_is_written_with_redacted_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "SENTINEL-PROCESS-DIAGNOSTIC-SECRET"
    monkeypatch.setattr(postgres_runtime, "_process_build_id", lambda: "unfrozen-runtime")

    postgres_runtime._write_process_diagnostic(
        tmp_path,
        {
            "stage": "cluster-init",
            "component": "initdb.exe",
            "safe_output_summary": f"ERROR password={sentinel}",
        },
    )

    path = tmp_path / "logs" / "postgres-process-diagnostics.jsonl"
    content = path.read_text(encoding="utf-8")
    row = json.loads(content)
    assert row["stage"] == "cluster-init"
    assert row["component"] == "initdb.exe"
    assert row["build_id"] == "unfrozen-runtime"
    assert sentinel not in content
    assert "password=<redacted>" in content


def test_dpapi_error_is_classified_without_exposing_exception_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.core import release_settings

    settings_path = tmp_path / "settings.dpapi"
    settings_path.write_bytes(b"CPDP1protected-data")
    monkeypatch.setattr(
        release_settings,
        "unprotect_for_current_user",
        lambda _data: (_ for _ in ()).throw(OSError("sentinel-secret")),
    )

    with pytest.raises(release_settings.ReleaseSettingsDecryptionError) as caught:
        release_settings.ReleaseSettingsStore(settings_path).load()

    assert "DPAPI" in str(caught.value)
    assert "sentinel-secret" not in str(caught.value)


def test_payload_version_probe_fails_before_creating_cluster_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resources = tmp_path / "resources"
    resources.mkdir()
    spec = {
        "version": "18.6",
        "package_revision": "5",
        "architecture": "windows-x64",
        "filename": "postgresql-18.6-5-windows-x64-binaries.zip",
        "sha256": "a" * 64,
        "official_download_page": "https://www.postgresql.org/download/windows/",
        "sha256_source": "test fixture",
        "required_runtime_files": [],
    }
    (resources / "postgresql_payload_spec.json").write_text(json.dumps(spec), encoding="utf-8")
    payload = tmp_path / "state" / "runtime" / "postgresql"
    (payload / "bin").mkdir(parents=True)
    for name in ("initdb.exe", "postgres.exe", "pg_ctl.exe"):
        (payload / "bin" / name).write_bytes(b"placeholder")
    (payload / "payload-manifest.json").write_text(
        json.dumps(
            {
                "version": "18.6",
                "package_revision": "5",
                "architecture": "windows-x64",
                "archive_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_module, "RUNTIME_PATHS", type("Paths", (), {"resource_root": resources})())
    monkeypatch.setattr(
        postgres_runtime,
        "_probe_executable_version",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            postgres_runtime._component_failure("initdb.exe", 3221225781, stage="executable-preflight")
        ),
    )
    from app.core.release_settings import ReleaseSettingsStore

    manager = postgres_runtime.PostgresPrototype(
        postgres_runtime.PostgresPayload(payload),
        tmp_path / "state",
        ReleaseSettingsStore(tmp_path / "state" / "config" / "settings.dpapi"),
    )

    with pytest.raises(postgres_runtime.PrototypeRuntimeError, match="0xC0000135"):
        manager.provision(migrate=lambda _settings: None)

    assert not manager.data_dir.exists()
    assert not (manager.state_root / "instance-owner.json").exists()
    assert not manager.settings_store.settings_file.exists()
